#!/usr/bin/env python3
"""gate.py - is the API-credit lane open right now? (ADR 0018)

The owner's rule: use the API credit when a weekly subscription window is more
than half elapsed and ahead of pace; at 98% or more, spend everything left.

Subcommands:

  status [--json | --line]
      --json  {open, reason, spend_all, allowance_usd, spent_this_week_usd,
              remaining_grant_usd, active_grant_expires, next_check_at,
              windows: [...]}
      --line  one line for the shell callers:
              <open|closed>\\t<next_check_epoch>\\t<spend-all|allowance left>\\t<reason>
      Always exits 0. Every error is a CLOSED verdict with a reason.
  record --cost <usd> --session <id> --model <m>
      Write one ledger file, attributed first in, first out to the active
      grant that expires earliest and still has money. Prints what is left of
      this week's allowance afterwards (`spend-all` when spending everything).
  close-until --until <iso|next-grant> --reason <r>
      Write the closed-until marker. `next-grant` means the next grant's
      arrival per config.next_grant, else now + 24 hours. Prints the time.
  hook --event <start|prompt>
      Read a hook's stdin JSON, evaluate, update the session's state file
      (credit_home/sessions/<session_id>.state, `<open|closed> <epoch>`), and
      print the hook's JSON answer when there is something to say: at start
      only when OPEN, on a prompt only when open/closed changed.

The current time is CLAUDE_CREDIT_NOW (ISO 8601) when set, else the clock.

Files (credit_home is <Windows home>/.config/claude-credit, shared by Windows
and WSL sessions; CLAUDE_CREDIT_HOME overrides it):

  credit_home/config.json        owner-maintained; config.example.json shows it
  credit_home/ledger/*.json      one per lane run: {ts, cost_usd, grant_expires,
                                 session_id, model}
  credit_home/closed-until.json  {until, reason}
  <Windows home>/.config/ai-usage/usage.json  the collector's output
                                 (CLAUDE_CREDIT_USAGE_JSON overrides it)

Standard library only.
"""

import argparse
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import uuid

UTC = dt.timezone.utc
WEEK_SECONDS = 604800
STALE_USAGE = dt.timedelta(minutes=15)
RECHECK = dt.timedelta(minutes=15)
SPEND_ALL_PCT = 98.0
# An assumed grant is the same grant as an explicit one whose `granted` lies
# within this many days of the assumed arrival (a grant that arrived late).
SAME_GRANT_DAYS = 15
SESSION_STATE_MAX_AGE = dt.timedelta(days=7)
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
WIF_KEYS = ("organization_id", "service_account_id", "federation_rule_id",
            "workspace_id", "issuer", "subject", "audience", "key_name")


class Closed(Exception):
    """The lane is closed for this reason."""


# =============================================================================
# Time
# =============================================================================


def parse_time(value):
    """An aware UTC datetime from an ISO 8601 string, or None."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso(moment):
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc(env=None):
    env = os.environ if env is None else env
    raw = env.get("CLAUDE_CREDIT_NOW")
    if raw:
        parsed = parse_time(raw)
        if parsed is None:
            raise Closed("CLAUDE_CREDIT_NOW is not an ISO 8601 time")
        return parsed
    return dt.datetime.now(UTC)


def _nth_sunday(year, month, n):
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def la_midnight(year, month, day):
    """00:00 America/Los_Angeles on that date, in UTC. Computed from the US
    rule (DST from the second Sunday of March to the first Sunday of November,
    switching at 02:00 local) because Windows Python ships no tz database."""
    date = dt.date(year, month, day)
    dst = _nth_sunday(year, 3, 2) < date <= _nth_sunday(year, 11, 1)
    offset = 7 if dst else 8
    return dt.datetime(year, month, day, tzinfo=UTC) + dt.timedelta(hours=offset)


# =============================================================================
# Where things live
# =============================================================================


def _is_wsl():
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as handle:
            return "microsoft" in handle.read().lower()
    except OSError:
        return False


def windows_home(env=None, is_wsl=None):
    """The Windows user home, as a path this process can open."""
    env = os.environ if env is None else env
    is_wsl = _is_wsl() if is_wsl is None else is_wsl
    if os.name == "nt" or (env.get("MSYSTEM") and env.get("USERPROFILE")):
        return env.get("USERPROFILE") or os.path.expanduser("~")
    home = env.get("HOME") or os.path.expanduser("~")
    if not is_wsl:
        return home
    cache = os.path.join(home, ".config", "claude-credit", "windows-home")
    try:
        with open(cache, encoding="utf-8") as handle:
            cached = handle.read().strip()
        if cached:
            return cached
    except OSError:
        pass
    profile = subprocess.run(["cmd.exe", "/c", "echo %USERPROFILE%"], cwd="/mnt/c",
                             stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             timeout=20, check=True).stdout.replace("\r", "").strip()
    resolved = subprocess.run(["wslpath", profile], capture_output=True, text=True,
                              timeout=10, check=True).stdout.strip()
    if not resolved.startswith("/"):
        raise OSError("wslpath gave no absolute path")
    _atomic_write(cache, resolved + "\n")
    return resolved


def credit_home(env=None):
    env = os.environ if env is None else env
    if env.get("CLAUDE_CREDIT_HOME"):
        return env["CLAUDE_CREDIT_HOME"]
    return os.path.join(windows_home(env), ".config", "claude-credit")


def usage_path(env=None):
    env = os.environ if env is None else env
    if env.get("CLAUDE_CREDIT_USAGE_JSON"):
        return env["CLAUDE_CREDIT_USAGE_JSON"]
    return os.path.join(windows_home(env), ".config", "ai-usage", "usage.json")


def _atomic_write(path, text):
    """Write text to path through a unique temp file and a rename."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path):
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle)


# =============================================================================
# Config and grants
# =============================================================================


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def load_config(home):
    path = os.path.join(home, "config.json")
    try:
        config = _read_json(path)
    except FileNotFoundError:
        raise Closed("config.json missing") from None
    except (OSError, ValueError):
        raise Closed("config.json unreadable") from None
    if not isinstance(config, dict):
        raise Closed("config.json invalid: not an object")
    wif = config.get("wif")
    if not isinstance(wif, dict) or not all(isinstance(wif.get(k), str) and wif[k] for k in WIF_KEYS):
        raise Closed("config.json invalid: wif needs " + ", ".join(WIF_KEYS))
    slots = config.get("slots", 2)
    if not isinstance(slots, int) or isinstance(slots, bool) or not 1 <= slots <= 16:
        raise Closed("config.json invalid: slots must be an integer from 1 to 16")
    grants = []
    raw_grants = config.get("grants", [])
    if not isinstance(raw_grants, list):
        raise Closed("config.json invalid: grants must be a list")
    for item in raw_grants:
        granted = parse_time(item.get("granted")) if isinstance(item, dict) else None
        expires = parse_time(item.get("expires")) if isinstance(item, dict) else None
        if granted is None or expires is None or not _number(item.get("usd")) \
                or item["usd"] <= 0 or expires <= granted:
            raise Closed("config.json invalid: each grant needs usd > 0, granted < expires")
        grants.append({"usd": float(item["usd"]), "granted": granted, "expires": expires,
                       "assumed": False})
    nxt = config.get("next_grant")
    if nxt is not None:
        if not isinstance(nxt, dict) or not isinstance(nxt.get("day_of_month"), int) \
                or not 1 <= nxt["day_of_month"] <= 28 or not _number(nxt.get("usd")) \
                or nxt["usd"] <= 0 or not isinstance(nxt.get("lifetime_days"), int) \
                or nxt["lifetime_days"] < 1:
            raise Closed("config.json invalid: next_grant needs day_of_month 1-28, usd > 0, lifetime_days >= 1")
    return {"wif": wif, "slots": slots, "grants": grants, "next_grant": nxt}


def _month_add(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def assumed_arrival(next_grant, year, month):
    return la_midnight(year, month, next_grant["day_of_month"])


def all_grants(config, now):
    """Explicit grants plus the ones next_grant lets the gate assume around
    now. An explicit grant whose `granted` lies within SAME_GRANT_DAYS of an
    assumed arrival IS that grant, so the explicit one wins."""
    grants = list(config["grants"])
    nxt = config["next_grant"]
    if nxt:
        for delta in range(-3, 3):
            year, month = _month_add(now.year, now.month, delta)
            arrival = assumed_arrival(nxt, year, month)
            if any(abs((g["granted"] - arrival).total_seconds()) < SAME_GRANT_DAYS * 86400
                   for g in config["grants"]):
                continue
            grants.append({"usd": float(nxt["usd"]), "granted": arrival,
                           "expires": arrival + dt.timedelta(days=nxt["lifetime_days"]),
                           "assumed": True})
    return sorted(grants, key=lambda g: (g["expires"], g["granted"]))


def next_arrival(config, now):
    """The next grant arrival after now, or None when config cannot say."""
    nxt = config["next_grant"] if config else None
    candidates = [g["granted"] for g in (config["grants"] if config else []) if g["granted"] > now]
    if nxt:
        for delta in range(0, 3):
            year, month = _month_add(now.year, now.month, delta)
            arrival = assumed_arrival(nxt, year, month)
            if arrival > now:
                candidates.append(arrival)
    return min(candidates) if candidates else None


def active(grants, now):
    return [g for g in grants if g["granted"] <= now < g["expires"]]


# =============================================================================
# Spend
# =============================================================================


def read_ledger(home):
    entries = []
    directory = os.path.join(home, "ledger")
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return entries
    for name in names:
        if not name.endswith(".json") or name.startswith("."):
            continue
        try:
            data = _read_json(os.path.join(directory, name))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        ts = parse_time(data.get("ts"))
        cost = data.get("cost_usd")
        if ts is None or not _number(cost) or cost < 0:
            continue
        entries.append({"ts": ts, "cost": float(cost),
                        "grant_expires": parse_time(data.get("grant_expires"))})
    return entries


def _key(moment):
    return iso(moment) if moment else None


def ledger_by_grant(entries):
    totals = {}
    for entry in entries:
        key = _key(entry["grant_expires"])
        totals[key] = totals.get(key, 0.0) + entry["cost"]
    return totals


def platform_by_grant(costs, grants):
    """The cost report's daily spend, attributed first in, first out: each
    day's dollars go to the grant active that day that expires earliest and
    still has money, overflowing to the next."""
    used = {_key(g["expires"]): 0.0 for g in grants}
    days = {}
    for row in costs if isinstance(costs, list) else []:
        if not isinstance(row, dict) or not _number(row.get("usd")) or row["usd"] <= 0:
            continue
        try:
            day = dt.date.fromisoformat(str(row.get("date"))[:10])
        except ValueError:
            continue
        days[day] = days.get(day, 0.0) + float(row["usd"])
    for day in sorted(days):
        left = days[day]
        start = dt.datetime(day.year, day.month, day.day, tzinfo=UTC)
        for grant in grants:  # sorted by expiry
            if left <= 0:
                break
            if not (grant["granted"].date() <= day and start < grant["expires"]):
                continue
            key = _key(grant["expires"])
            room = max(0.0, grant["usd"] - used[key])
            take = min(room, left)
            used[key] += take
            left -= take
    return used


def grant_spent(grants, entries, costs):
    """Per grant (keyed by its expiry), the larger of ledger and cost report:
    the report lags, the ledger misses spend outside the lane."""
    ledger = ledger_by_grant(entries)
    platform = platform_by_grant(costs, grants)
    return {_key(g["expires"]): max(ledger.get(_key(g["expires"]), 0.0),
                                    platform.get(_key(g["expires"]), 0.0)) for g in grants}


def attribute(grants, spent, now):
    """The grant a new run's cost goes to: the active grant that expires
    earliest and still has money, else the earliest-expiring active one."""
    live = active(grants, now)
    for grant in live:
        if spent.get(_key(grant["expires"]), 0.0) < grant["usd"]:
            return grant
    return live[0] if live else None


# =============================================================================
# The verdict
# =============================================================================


def weekly_windows(claude, now):
    out = []
    for window in claude.get("windows") or []:
        if not isinstance(window, dict) or window.get("period_seconds") != WEEK_SECONDS:
            continue
        resets = parse_time(window.get("resets_at"))
        used = window.get("used_pct")
        if resets is None or not _number(used):
            continue
        elapsed = 1 - (resets - now).total_seconds() / WEEK_SECONDS
        elapsed = min(1.0, max(0.0, elapsed))
        ahead = elapsed > 0.5 and used / 100 > elapsed
        out.append({"id": str(window.get("id")), "label": str(window.get("label") or window.get("id")),
                    "used_pct": float(used), "elapsed": round(elapsed, 4),
                    "resets_at": iso(resets), "_resets": resets,
                    "ahead_of_pace": ahead, "spend_all": used >= SPEND_ALL_PCT})
    return out


def _describe(window):
    return (f"week {window['used_pct']:.0f}% used at {window['elapsed'] * 100:.0f}% elapsed "
            f"({window['label']})")


def _closed(reason, now, **extra):
    verdict = {"open": False, "reason": reason, "spend_all": False, "allowance_usd": 0.0,
               "spent_this_week_usd": 0.0, "remaining_grant_usd": 0.0,
               "active_grant_expires": None, "next_check_at": iso(now + RECHECK), "windows": []}
    verdict.update(extra)
    return verdict


def _public(windows):
    return [{k: v for k, v in w.items() if not k.startswith("_")} for w in windows]


def evaluate(env=None):
    """The verdict dict. Never raises."""
    env = os.environ if env is None else env
    try:
        now = now_utc(env)
    except Closed as exc:
        now = dt.datetime.now(UTC)
        return _closed(str(exc), now)
    try:
        return _evaluate(env, now)
    except Closed as exc:
        return _closed(str(exc), now)
    except Exception as exc:  # noqa: BLE001 - every error is a closed verdict
        return _closed(f"gate error ({type(exc).__name__})", now)


def _evaluate(env, now):
    try:
        usage = _read_json(usage_path(env))
    except FileNotFoundError:
        raise Closed("usage.json missing") from None
    except (OSError, ValueError, subprocess.SubprocessError):
        raise Closed("usage.json unreadable") from None
    if not isinstance(usage, dict) or not isinstance(usage.get("sources"), dict):
        raise Closed("usage.json unreadable")
    claude = usage["sources"].get("claude")
    if not isinstance(claude, dict) or claude.get("ok") is not True:
        raise Closed("usage data not ok")
    fetched = parse_time(claude.get("fetched_at"))
    if fetched is None:
        raise Closed("usage data has no fetched_at")
    if now - fetched > STALE_USAGE:
        minutes = int((now - fetched).total_seconds() // 60)
        raise Closed(f"usage data stale ({minutes} min old)")

    home = credit_home(env)
    config = load_config(home)

    try:
        marker = _read_json(os.path.join(home, "closed-until.json"))
    except FileNotFoundError:
        marker = None
    except (OSError, ValueError):
        raise Closed("closed-until.json unreadable") from None
    if marker is not None:
        until = parse_time(marker.get("until")) if isinstance(marker, dict) else None
        if until is None:
            raise Closed("closed-until.json invalid")
        if until > now:
            why = str(marker.get("reason") or "no reason given")[:120]
            raise Closed(f"closed until {iso(until)}: {why}")

    grants = all_grants(config, now)
    live = active(grants, now)
    if not live:
        raise Closed("no active grant")

    entries = read_ledger(home)
    platform = usage["sources"].get("platform")
    costs = platform.get("costs") if isinstance(platform, dict) and platform.get("ok") is True else []
    spent = grant_spent(grants, entries, costs)
    remaining = {_key(g["expires"]): max(0.0, g["usd"] - spent[_key(g["expires"])]) for g in live}
    remaining_total = sum(remaining.values())

    windows = weekly_windows(claude, now)
    if not windows:
        raise Closed("usage data has no weekly window")
    overall = next((w for w in windows if w["id"] == "seven_day"), windows[0])
    week_start = overall["_resets"] - dt.timedelta(seconds=WEEK_SECONDS)
    this_week = [e for e in entries if week_start <= e["ts"] <= now]
    week_by_grant = ledger_by_grant(this_week)
    spent_this_week = sum(e["cost"] for e in this_week)

    # The allowance divides each grant's balance AT THE START OF THIS WEEK by
    # the weeks it has left, so spending during the week does not shrink the
    # week's own allowance. ceil() makes the last partial week before a grant
    # expires take everything left: the natural spend-down.
    allowance = 0.0
    for grant in live:
        key = _key(grant["expires"])
        start_balance = min(grant["usd"], remaining[key] + week_by_grant.get(key, 0.0))
        weeks = max(1, math.ceil((grant["expires"] - now).total_seconds() / 86400 / 7))
        allowance += start_balance / weeks

    spend_all = any(w["spend_all"] for w in windows)
    opening = [w for w in windows if w["ahead_of_pace"] or w["spend_all"]]
    common = {"spend_all": False, "allowance_usd": round(allowance, 4),
              "spent_this_week_usd": round(spent_this_week, 4),
              "remaining_grant_usd": round(remaining_total, 4),
              "active_grant_expires": iso(live[0]["expires"]), "windows": _public(windows)}

    if not opening:
        reason = "on pace: " + "; ".join(_describe(w) for w in windows)
        if all(w["elapsed"] <= 0.5 for w in windows):
            halves = [w["_resets"] - dt.timedelta(seconds=WEEK_SECONDS / 2) for w in windows]
            later = [h for h in halves if h > now]
            check = min(later) if later else now + RECHECK
        else:
            check = now + RECHECK
        return dict(common, open=False, reason=reason, next_check_at=iso(check))

    first = next((w for w in opening if w["spend_all"]), opening[0]) if spend_all else opening[0]
    reason = _describe(first) + (": spend everything left" if spend_all else "")
    if remaining_total <= 0:
        return dict(common, open=False, reason="grant spent", next_check_at=iso(now + RECHECK))
    if not spend_all and spent_this_week >= allowance:
        return dict(common, open=False, reason="weekly-allowance-used",
                    next_check_at=iso(now + RECHECK))
    return dict(common, open=True, reason=reason, spend_all=spend_all,
                next_check_at=iso(now + RECHECK))


def allowance_left(verdict):
    if verdict.get("spend_all"):
        return "spend-all"
    left = max(0.0, verdict.get("allowance_usd", 0.0) - verdict.get("spent_this_week_usd", 0.0))
    return f"{left:.2f}"


def _line(verdict):
    epoch = int(parse_time(verdict["next_check_at"]).timestamp())
    reason = verdict["reason"].replace("\t", " ").replace("\n", " ")
    return "\t".join(["open" if verdict["open"] else "closed", str(epoch),
                      allowance_left(verdict), reason])


# =============================================================================
# Subcommands
# =============================================================================


def cmd_status(args):
    verdict = evaluate()
    if args.line:
        print(_line(verdict))
    else:
        print(json.dumps(verdict, indent=None if args.json else 2, sort_keys=True))
    return 0


def cmd_record(args):
    if not _number(args.cost) or args.cost < 0:
        print("gate.py record: --cost must be a non-negative number", file=sys.stderr)
        return 2
    now = now_utc()
    home = credit_home()
    grant = None
    try:
        config = load_config(home)
        grants = all_grants(config, now)
        try:
            usage = _read_json(usage_path())
            platform = usage["sources"]["platform"]
            costs = platform.get("costs") if platform.get("ok") is True else []
        except Exception:  # noqa: BLE001 - the ledger alone still attributes
            costs = []
        grant = attribute(grants, grant_spent(grants, read_ledger(home), costs), now)
    except Closed:
        pass
    session = args.session if SESSION_RE.match(args.session or "") else "unknown"
    entry = {"ts": iso(now), "cost_usd": round(float(args.cost), 6),
             "grant_expires": iso(grant["expires"]) if grant else None,
             "session_id": session, "model": str(args.model)[:100]}
    name = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{session[:40]}-{uuid.uuid4().hex}.json"
    _atomic_write(os.path.join(home, "ledger", name), json.dumps(entry, sort_keys=True) + "\n")
    print(allowance_left(evaluate()))
    return 0


def cmd_close_until(args):
    now = now_utc()
    if args.until == "next-grant":
        try:
            config = load_config(credit_home())
        except Closed:
            config = None
        until = next_arrival(config, now) or now + dt.timedelta(hours=24)
    else:
        until = parse_time(args.until)
        if until is None:
            print("gate.py close-until: --until must be ISO 8601 or next-grant", file=sys.stderr)
            return 2
    marker = {"until": iso(until), "reason": str(args.reason)[:200]}
    _atomic_write(os.path.join(credit_home(), "closed-until.json"), json.dumps(marker) + "\n")
    print(iso(until))
    return 0


def _hook_answer(event, text):
    name = "SessionStart" if event == "start" else "UserPromptSubmit"
    return json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}})


def _open_context(verdict):
    budget = ("spend everything left in the grant" if verdict["spend_all"]
              else f"${allowance_left(verdict)} left of this week's allowance")
    return "\n".join([
        f"API-credit lane OPEN: {verdict['reason']}; {budget}.",
        "For delegated work, run `claude-credit -p \"<task>\" --model <cheapest capable model>` "
        "through Bash instead of the Agent tool; it bills the API credit, not the subscription.",
        "Exit 75 (closed or busy) or 76 (credit unavailable) means: use the Agent tool instead.",
        "At most 2 claude-credit runs at a time across this machine (2 slots).",
    ])


def _cleanup_sessions(directory, now):
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not name.endswith(".state"):
            continue
        path = os.path.join(directory, name)
        try:
            age = now.timestamp() - os.stat(path).st_mtime
            if age > SESSION_STATE_MAX_AGE.total_seconds():
                os.unlink(path)
        except OSError:
            pass


def cmd_hook(args):
    """Never prints anything but the hook's JSON; always exits 0."""
    try:
        payload = json.loads(sys.stdin.read() or "null")
    except ValueError:
        return 0
    session = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(session, str) or not SESSION_RE.match(session):
        return 0
    try:
        home = credit_home()
    except Exception:  # noqa: BLE001
        return 0
    verdict = evaluate()
    directory = os.path.join(home, "sessions")
    path = os.path.join(directory, session + ".state")
    previous = None
    try:
        with open(path, encoding="utf-8") as handle:
            previous = handle.read().split()[0]
    except (OSError, IndexError):
        pass
    state = "open" if verdict["open"] else "closed"
    epoch = int(parse_time(verdict["next_check_at"]).timestamp())
    if args.event == "start":
        try:
            _cleanup_sessions(directory, now_utc())
        except Closed:
            pass
    try:
        _atomic_write(path, f"{state} {epoch}\n")
    except OSError:
        pass
    if args.event == "start":
        if verdict["open"]:
            print(_hook_answer("start", _open_context(verdict)))
    elif state != (previous or "closed"):
        if verdict["open"]:
            text = (f"API-credit lane now OPEN: {verdict['reason']}. Delegate with "
                    "`claude-credit -p \"<task>\" --model <cheapest capable model>` via Bash; "
                    "exit 75/76 means use the Agent tool.")
        else:
            text = (f"API-credit lane now CLOSED: {verdict['reason']}. "
                    "Use the Agent tool for delegated work.")
        print(_hook_answer("prompt", text))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="gate.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    mode = status.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true")
    mode.add_argument("--line", action="store_true")
    record = sub.add_parser("record")
    record.add_argument("--cost", type=float, required=True)
    record.add_argument("--session", required=True)
    record.add_argument("--model", required=True)
    close = sub.add_parser("close-until")
    close.add_argument("--until", required=True)
    close.add_argument("--reason", required=True)
    hook = sub.add_parser("hook")
    hook.add_argument("--event", choices=("start", "prompt"), required=True)
    args = parser.parse_args(argv)
    handlers = {"status": cmd_status, "record": cmd_record, "close-until": cmd_close_until,
                "hook": cmd_hook}
    try:
        return handlers[args.command](args)
    except Closed as exc:
        print(f"gate.py {args.command}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
