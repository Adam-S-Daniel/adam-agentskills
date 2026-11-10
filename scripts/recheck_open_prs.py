#!/usr/bin/env python3
"""Re-run the `pytest` job of every open pull request after `main` moves.

The one-way-door check (scripts/check_one_way_doors.py, docs/one-way-doors.md)
judges a pull request against its base as the base is when the check runs.
Nothing re-ran it when the base moved, so a pull request that deletes a skill
another pull request adds stayed green after the other one merged (issue
#137). ci.yml's `recheck-open-prs` job runs this on a push to the default
branch that changes what the check reads there.

For each open pull request into the default branch it finds the CI runs on
the pull request's current head, waits for them to finish, and re-runs the
`pytest` job of the newest one. A re-run fetches the base again, so the check
in it reads the base as it is now, and the re-run's result is the newest one
on that head. The `merge-gate` job depends on `pytest`, so it re-runs too.

A pull request with no CI run on its head has no green result to go stale, so
it is skipped. One whose runs cannot be re-run (GitHub refuses after 30 days)
is reported and the script exits 1, so a pull request left with a stale result
shows as a failed run rather than passing in silence.

Prints pull request numbers, run ids and job ids only.

Run: GH_TOKEN=... python3 scripts/recheck_open_prs.py --repo <owner>/<repo>
"""

import argparse
import json
import subprocess
import sys
import time
from typing import Callable, List, Optional

WORKFLOW = "ci.yml"
JOB = "pytest"
POLL_SECONDS = 30
# 90 polls of 30 s: longer than the CI workflow's slowest job has ever taken.
MAX_POLLS = 90


class GhError(Exception):
    pass


def gh(*args: str) -> object:
    """Call `gh api` and return the parsed body (None when it is empty).

    The error text is the exit code alone: an API error body is never printed.
    """
    done = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise GhError(f"gh api exited {done.returncode}")
    return json.loads(done.stdout) if done.stdout.strip() else None


def _paged(api: Callable, path: str, key: Optional[str]) -> list:
    """Every item of a paginated listing, 100 a page."""
    items: list = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        body = api(f"{path}{sep}per_page=100&page={page}")
        batch = body[key] if key else body
        if not isinstance(batch, list):
            raise GhError("a listing did not return a list")
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def runs_for(api: Callable, repo: str, pr: dict) -> List[dict]:
    """The CI runs for this pull request on its current head.

    Runs are listed by head sha, which two pull requests can share, so each
    run must also name this pull request, or (a pull request from a fork,
    whose runs name none) come from its head branch.
    """
    sha = pr["head"]["sha"]
    runs = _paged(
        api,
        f"repos/{repo}/actions/workflows/{WORKFLOW}/runs?event=pull_request&head_sha={sha}",
        "workflow_runs",
    )
    mine = []
    for run in runs:
        numbers = [p.get("number") for p in run.get("pull_requests") or []]
        if pr["number"] in numbers or (not numbers and run.get("head_branch") == pr["head"]["ref"]):
            mine.append(run)
    return mine


def recheck(api: Callable, repo: str, sleep: Callable[[float], None] = time.sleep,
            out: Callable[[str], None] = print) -> int:
    default = api(f"repos/{repo}")["default_branch"]
    prs = [p for p in _paged(api, f"repos/{repo}/pulls?state=open", None)
           if p["base"]["ref"] == default]
    failed: List[int] = []
    for pr in sorted(prs, key=lambda p: p["number"]):
        number = pr["number"]
        try:
            runs = runs_for(api, repo, pr)
            polls = 0
            # A run still going fetched the base before or after the push;
            # only a run started after it finished is sure to read the new
            # base, and a job cannot be re-run while its run is going.
            while any(r.get("status") != "completed" for r in runs):
                if polls >= MAX_POLLS:
                    raise GhError("its CI runs did not finish in time")
                polls += 1
                sleep(POLL_SECONDS)
                runs = runs_for(api, repo, pr)
            if not runs:
                out(f"#{number}: no CI run on its head, nothing to re-run")
                continue
            newest = max(runs, key=lambda r: (r["created_at"], r["id"]))
            jobs = _paged(api, f"repos/{repo}/actions/runs/{newest['id']}/jobs?filter=latest", "jobs")
            job_ids = [j["id"] for j in jobs if j.get("name") == JOB]
            if len(job_ids) != 1:
                raise GhError(f"run {newest['id']} has {len(job_ids)} `{JOB}` jobs")
            api("-X", "POST", f"repos/{repo}/actions/jobs/{job_ids[0]}/rerun")
            out(f"#{number}: re-ran job {job_ids[0]} of run {newest['id']}")
        except (GhError, KeyError, TypeError, ValueError) as exc:
            # The exception's type and this script's own words only.
            detail = str(exc) if isinstance(exc, GhError) else type(exc).__name__
            out(f"::error::#{number}: could not re-run its check ({detail})")
            failed.append(number)
    if failed:
        out("::error::These pull requests may still show a result from before the base moved: "
            + ", ".join(f"#{n}" for n in failed)
            + ". Push to each, or edit its description, to run the check again.")
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--repo", required=True, help="<owner>/<repo>")
    args = ap.parse_args(argv)
    try:
        return recheck(gh, args.repo)
    except (GhError, KeyError, TypeError, ValueError) as exc:
        detail = str(exc) if isinstance(exc, GhError) else type(exc).__name__
        print(f"::error::Could not list the open pull requests ({detail})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
