#!/usr/bin/env python3
"""lane.py - the JSON the claude-credit wrapper reads (ADR 0018).

Parsing only: nothing here runs a command, and the wrapper never executes
anything this prints.

  wif-env
      Print the child's federation variables from credit_home/config.json, one
      NAME=value per line (ids only; every value must match [A-Za-z0-9_-]+).
      ANTHROPIC_IDENTITY_TOKEN_FILE is added by the wrapper.
  auth-verdict <auth-status.json>
      Read `claude auth status --json` output. Print `ok<TAB><method>` and
      exit 0 only when it reports a signed-in, first-party, non-subscription
      method; otherwise print `reject<TAB><method>` and exit 1.
  child-result <output.json> <stderr.txt> <exit-code> <result-file>
      Read `claude -p --output-format json` output. Write the `.result` text to
      <result-file> and print one line: `<ok|billing|error><TAB><cost><TAB><why>`.

What `claude auth status --json` prints was read from the Claude Code 2.1.295
binary, not from documentation: {loggedIn, authMethod, apiProvider, ...,
apiKeySource?, subscriptionType?, email?, orgId?, orgName?}, with authMethod
one of none, third_party, claude.ai, api_key_helper, oauth_token, api_key.
What it reports under workload identity federation could not be confirmed
without a live run, so the accepted set is deliberately small and everything
else fails closed.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gate  # noqa: E402

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
ENV_FROM_WIF = (
    ("ANTHROPIC_FEDERATION_RULE_ID", "federation_rule_id"),
    ("ANTHROPIC_ORGANIZATION_ID", "organization_id"),
    ("ANTHROPIC_SERVICE_ACCOUNT_ID", "service_account_id"),
    ("ANTHROPIC_WORKSPACE_ID", "workspace_id"),
)
# Methods that bill the API, never the subscription. `oauth_token` is what the
# 2.1.295 binary reports for a credential resolved through a profile, which is
# where the federation env vars land; it is also what CLAUDE_CODE_OAUTH_TOKEN
# reports, which is why the wrapper unsets that in the child. The federation
# spellings are accepted in case a later release names the method.
API_METHODS = frozenset({"oauth_token", "api_key", "oidc_federation", "federation",
                         "workload_identity_federation", "wif"})
SUBSCRIPTION_METHODS = frozenset({"claude.ai", "claudeai", "subscription"})
BILLING_RE = re.compile(
    r"billing|credit balance|insufficient[ _-]?(credit|funds|balance)|credits? exhausted|"
    r"out of credits?|spend(ing)? limit|usage limit|payment required|\b402\b",
    re.IGNORECASE)


def cmd_wif_env():
    config = gate.load_config(gate.credit_home())
    for name, key in ENV_FROM_WIF:
        value = config["wif"][key]
        if not ID_RE.match(value):
            print(f"lane.py: config wif.{key} has characters an id never has", file=sys.stderr)
            return 1
        print(f"{name}={value}")
    return 0


def auth_verdict(data):
    """(ok, method) for one parsed `claude auth status --json`."""
    if not isinstance(data, dict):
        return False, "unparseable"
    method = data.get("authMethod")
    method = method if isinstance(method, str) and method else "unknown"
    if data.get("loggedIn") is not True:
        return False, f"{method}, not logged in"
    if method.lower() in SUBSCRIPTION_METHODS or data.get("subscriptionType"):
        return False, f"{method} subscription"
    provider = data.get("apiProvider")
    if provider not in (None, "firstParty"):
        return False, f"{method} via {provider}"
    if method.lower() not in API_METHODS:
        return False, method
    return True, method


def cmd_auth_verdict(path):
    try:
        with open(path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = None
    ok, method = auth_verdict(data)
    method = re.sub(r"[\t\n\r]", " ", method)[:80]
    print(f"{'ok' if ok else 'reject'}\t{method}")
    return 0 if ok else 1


def _last_json(text):
    """The parsed `type: result` object, or the last JSON object in text."""
    found = None
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            index = start + 1
            continue
        if isinstance(value, dict):
            if value.get("type") == "result":
                return value
            found = value
        index = end
    return found


def _strings(node, depth=0):
    if depth > 4:
        return
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value, depth + 1)


def classify(result, stderr, code):
    """(kind, cost, why) for one child run."""
    cost = 0.0
    if isinstance(result, dict) and gate._number(result.get("total_cost_usd")):
        cost = max(0.0, float(result["total_cost_usd"]))
    failed = code != 0 or not isinstance(result, dict) or result.get("is_error") is True \
        or (isinstance(result.get("subtype"), str) and result["subtype"] != "success")
    if not failed:
        return "ok", cost, ""
    fields = []
    if isinstance(result, dict):
        for key in ("error", "subtype", "api_error_status", "status", "result", "errors"):
            fields.extend(_strings(result.get(key)))
    fields.append(stderr)
    for text in fields:
        match = BILLING_RE.search(text or "")
        if match:
            return "billing", cost, match.group(0).lower()
    if isinstance(result, dict) and result.get("api_error_status") in (402, "402"):
        return "billing", cost, "402"
    return "error", cost, ""


def cmd_child_result(out_path, err_path, code, result_path):
    try:
        with open(out_path, encoding="utf-8", errors="replace") as handle:
            result = _last_json(handle.read())
    except OSError:
        result = None
    try:
        with open(err_path, encoding="utf-8", errors="replace") as handle:
            stderr = handle.read()[-20000:]
    except OSError:
        stderr = ""
    try:
        code = int(code)
    except ValueError:
        code = 1
    kind, cost, why = classify(result, stderr, code)
    text = result.get("result") if isinstance(result, dict) else None
    with open(result_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text if isinstance(text, str) else "")
    print(f"{kind}\t{cost:.6f}\t{re.sub(r'[^A-Za-z0-9 .-]', ' ', why)[:60]}")
    return 0


def main(argv):
    if len(argv) == 2 and argv[1] == "wif-env":
        try:
            return cmd_wif_env()
        except gate.Closed as exc:
            print(f"lane.py: {exc}", file=sys.stderr)
            return 1
    if len(argv) == 3 and argv[1] == "auth-verdict":
        return cmd_auth_verdict(argv[2])
    if len(argv) == 6 and argv[1] == "child-result":
        return cmd_child_result(*argv[2:])
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
