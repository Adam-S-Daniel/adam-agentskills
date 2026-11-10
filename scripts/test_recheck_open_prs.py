#!/usr/bin/env python3
"""Tests for scripts/recheck_open_prs.py (issue #137), against a fake API.

Run: python3 -m pytest scripts/test_recheck_open_prs.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import recheck_open_prs as mod  # noqa: E402

REPO = "example-owner/example-repo"


def pr(number, sha, ref="feature", base="main"):
    return {"number": number, "head": {"sha": sha, "ref": ref}, "base": {"ref": base}}


def run(run_id, number, created, status="completed", branch="feature"):
    return {"id": run_id, "created_at": created, "status": status, "head_branch": branch,
            "pull_requests": [] if number is None else [{"number": number}]}


class FakeApi:
    """Answers the calls the script makes; `runs` maps a head sha to a list of
    run listings, one per poll (the last repeats)."""

    def __init__(self, prs, runs, jobs=None, refuse=()):
        self.prs, self.runs, self.jobs = prs, runs, jobs or {}
        self.refuse = set(refuse)
        self.polls = {}
        self.reran = []
        self.slept = []

    def __call__(self, *args):
        if args[0] == "-X":
            assert args[1] == "POST"
            job_id = int(args[2].split("/jobs/")[1].split("/")[0])
            if job_id in self.refuse:
                raise mod.GhError("gh api exited 1")
            self.reran.append(job_id)
            return None
        path = args[0]
        if path == f"repos/{REPO}":
            return {"default_branch": "main"}
        if path.startswith(f"repos/{REPO}/pulls?"):
            return self.prs if path.endswith("page=1") else []
        if "/actions/workflows/ci.yml/runs?" in path:
            assert "event=pull_request" in path
            sha = path.split("head_sha=")[1].split("&")[0]
            seq = self.runs.get(sha, [[]])
            i = self.polls.get(sha, 0)
            self.polls[sha] = i + 1
            return {"workflow_runs": seq[min(i, len(seq) - 1)]}
        if "/actions/runs/" in path:
            assert "filter=latest" in path
            run_id = int(path.split("/actions/runs/")[1].split("/")[0])
            return {"jobs": self.jobs.get(run_id, [{"id": run_id * 10, "name": "pytest"},
                                                   {"id": run_id * 10 + 1, "name": "privacy"}])}
        raise AssertionError(f"unexpected call {args}")

    def recheck(self):
        lines = []
        code = mod.recheck(self, REPO, sleep=self.slept.append, out=lines.append)
        return code, lines


def test_the_newest_run_on_each_open_pull_requests_head_is_rerun():
    api = FakeApi(
        [pr(7, "a" * 40), pr(9, "b" * 40)],
        {"a" * 40: [[run(1, 7, "2026-01-01T00:00:00Z"), run(2, 7, "2026-01-02T00:00:00Z")]],
         "b" * 40: [[run(3, 9, "2026-01-01T00:00:00Z")]]})
    code, lines = api.recheck()
    assert code == 0 and api.reran == [20, 30] and not api.slept
    assert lines == ["#7: re-ran job 20 of run 2", "#9: re-ran job 30 of run 3"]


def test_a_pull_request_into_another_base_is_left_alone():
    api = FakeApi([pr(7, "a" * 40, base="other")],
                  {"a" * 40: [[run(1, 7, "2026-01-01T00:00:00Z")]]})
    assert api.recheck() == (0, []) and api.reran == []


def test_a_run_of_another_pull_request_on_the_same_head_is_not_this_ones():
    """Two pull requests can share a head sha; each is re-run through its own run."""
    sha = "a" * 40
    both = [run(1, 7, "2026-01-01T00:00:00Z"), run(2, 8, "2026-01-02T00:00:00Z", branch="other")]
    api = FakeApi([pr(7, sha), pr(8, sha, ref="other")], {sha: [both]})
    assert api.recheck()[0] == 0 and api.reran == [10, 20]


def test_a_fork_pull_requests_run_is_matched_by_its_head_branch():
    sha = "a" * 40
    api = FakeApi([pr(7, sha, ref="patch-1")],
                  {sha: [[run(1, None, "2026-01-01T00:00:00Z", branch="patch-1"),
                          run(2, None, "2026-01-02T00:00:00Z", branch="elsewhere")]]})
    assert api.recheck()[0] == 0 and api.reran == [10]


def test_a_pull_request_with_no_run_has_nothing_to_go_stale():
    api = FakeApi([pr(7, "a" * 40)], {})
    code, lines = api.recheck()
    assert code == 0 and api.reran == [] and "nothing to re-run" in lines[0]


def test_it_waits_for_runs_in_progress_before_rerunning():
    """A run still going may have fetched the old base and would report after
    a re-run started now, and a job of a running run cannot be re-run."""
    sha = "a" * 40
    going = [run(1, 7, "2026-01-01T00:00:00Z"), run(2, 7, "2026-01-02T00:00:00Z", "in_progress")]
    done = [run(1, 7, "2026-01-01T00:00:00Z"), run(2, 7, "2026-01-02T00:00:00Z")]
    api = FakeApi([pr(7, sha)], {sha: [going, going, done]})
    assert api.recheck()[0] == 0
    assert api.slept == [mod.POLL_SECONDS] * 2 and api.reran == [20]


def test_runs_that_never_finish_fail_the_script_without_a_rerun():
    sha = "a" * 40
    api = FakeApi([pr(7, sha)], {sha: [[run(1, 7, "2026-01-01T00:00:00Z", "queued")]]})
    code, lines = api.recheck()
    assert code == 1 and api.reran == [] and len(api.slept) == mod.MAX_POLLS
    assert "#7" in lines[-1]


def test_a_refused_rerun_fails_the_script_and_the_others_still_run():
    api = FakeApi(
        [pr(7, "a" * 40), pr(9, "b" * 40)],
        {"a" * 40: [[run(1, 7, "2026-01-01T00:00:00Z")]],
         "b" * 40: [[run(3, 9, "2026-01-01T00:00:00Z")]]},
        refuse={10})
    code, lines = api.recheck()
    assert code == 1 and api.reran == [30]
    assert lines[-1].startswith("::error::") and "#7" in lines[-1] and "#9" not in lines[-1]


@pytest.mark.parametrize("jobs", [[], [{"id": 1, "name": "pytest"}, {"id": 2, "name": "pytest"}]])
def test_a_run_without_exactly_one_pytest_job_fails_closed(jobs):
    api = FakeApi([pr(7, "a" * 40)], {"a" * 40: [[run(1, 7, "2026-01-01T00:00:00Z")]]},
                  jobs={1: jobs})
    assert api.recheck()[0] == 1 and api.reran == []


def test_an_unreadable_listing_fails_main(monkeypatch, capsys):
    def broken(*_args):
        raise mod.GhError("gh api exited 1")
    monkeypatch.setattr(mod, "gh", broken)
    assert mod.main(["--repo", REPO]) == 1
    assert "::error::" in capsys.readouterr().out


def test_listings_are_read_past_the_first_page():
    calls = []

    def api(path):
        calls.append(path)
        return list(range(100)) if path.endswith("page=1") else [100]
    assert mod._paged(api, "x?state=open", None) == list(range(101))
    assert calls == ["x?state=open&per_page=100&page=1", "x?state=open&per_page=100&page=2"]
