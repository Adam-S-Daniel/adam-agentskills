#!/usr/bin/env python3
"""Tests for adam-coding-local's API-credit lane (ADR 0018).

Covers plugins/adam-coding-local/hooks/credit-lane/ (gate.py, slots.py,
lane.py, Sign-CreditJwt.ps1, the two hooks), bin/claude-credit, and the shape
of hooks/claude-code.json and the manifests that name it.

Hermetic and deterministic:
  - every path is a tmp dir: CLAUDE_CREDIT_HOME, CLAUDE_CREDIT_USAGE_JSON and
    HOME always point under tmp_path, so nothing reads a real home, config,
    credential or usage file;
  - the time is injected: CLAUDE_CREDIT_NOW (ISO) for the Python side and
    CLAUDE_CREDIT_NOW_EPOCH for the hooks' bash fast path. No sleeps;
  - `claude` and the signer are fakes (CLAUDE_CREDIT_CLAUDE_BIN,
    CLAUDE_CREDIT_SIGNER), so nothing calls Anthropic, and PATH drops every
    /mnt/ entry so a WSL machine's Windows binaries are unreachable;
  - the signer test uses a throwaway software key (-KeyPem), never the TPM.

Every wrapper run carries a unique marker in its environment, and each test
asserts afterwards that no process carrying it survived (Linux /proc).

Run: python3 -m pytest scripts/test_credit_lane.py -q
"""

import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_consistency  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugins" / "adam-coding-local"
LANE = PLUGIN / "hooks" / "credit-lane"
WRAPPER = PLUGIN / "bin" / "claude-credit"
CLAUDE_HOOKS = PLUGIN / "hooks" / "claude-code.json"
BASH = shutil.which("bash")
PYTHON = shutil.which("python3")

posix_only = pytest.mark.skipif(
    os.name == "nt" or BASH is None or PYTHON is None,
    reason="drives the lane's bash scripts with a POSIX bash")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, LANE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(LANE))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(LANE))
    return module


gate = _load("gate")
lane = _load("lane")

UTC = dt.timezone.utc
WEEK = 604800
NOW = dt.datetime(2026, 10, 20, 12, 0, tzinfo=UTC)  # a Tuesday


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_path():
    return os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                           if p and "/mnt/" not in p)


# =================================================================================
# Fixtures
# =================================================================================


def window(wid, label, used, elapsed, now=NOW, period=WEEK):
    resets = now + dt.timedelta(seconds=(1 - elapsed) * period)
    return {"id": wid, "label": label, "used_pct": used, "resets_at": iso(resets),
            "period_seconds": period}


def usage_doc(windows, now=NOW, fetched_ago=dt.timedelta(minutes=2), ok=True, costs=None):
    return {"schema": 1, "generated_at": iso(now),
            "sources": {"claude": {"ok": ok, "fetched_at": iso(now - fetched_ago),
                                   "windows": windows},
                        "platform": {"ok": True, "fetched_at": iso(now),
                                     "costs": costs or []}}}


def config_doc(grants=None, next_grant=True, slots=2):
    doc = {"wif": {"organization_id": "00000000-0000-4000-8000-000000000000",
                   "service_account_id": "svac_EXAMPLE01", "federation_rule_id": "fdrl_EXAMPLE01",
                   "workspace_id": "wrkspc_EXAMPLE01", "issuer": "https://issuer.example.com",
                   "subject": "api-credit-lane", "audience": "https://api.anthropic.com",
                   "key_name": "example-key"},
           "slots": slots,
           "grants": [{"usd": 200, "granted": "2026-10-08T00:00:00Z",
                       "expires": "2026-11-07T00:00:00Z"}] if grants is None else grants}
    if next_grant:
        doc["next_grant"] = {"day_of_month": 5, "usd": 200, "lifetime_days": 30}
    return doc


AHEAD = [window("seven_day", "overall", 64, 0.57), window("seven_day_fable", "Fable", 10, 0.57)]
ON_PACE = [window("seven_day", "overall", 40, 0.57), window("seven_day_fable", "Fable", 10, 0.57)]


class World:
    def __init__(self, tmp_path, now=NOW):
        self.tmp = tmp_path
        self.home = tmp_path / "credit-home"
        self.home.mkdir()
        self.usage = tmp_path / "usage.json"
        self.now = now
        self.marker = f"CREDIT_LANE_TEST_{uuid.uuid4().hex}"

    def write_usage(self, doc):
        self.usage.write_text(json.dumps(doc), encoding="utf-8")

    def write_config(self, doc):
        (self.home / "config.json").write_text(json.dumps(doc), encoding="utf-8")

    def ledger(self, ts, cost, grant_expires="2026-11-07T00:00:00Z", name=None):
        directory = self.home / "ledger"
        directory.mkdir(exist_ok=True)
        entry = {"ts": iso(ts), "cost_usd": cost, "grant_expires": grant_expires,
                 "session_id": "s-example", "model": "example-model"}
        (directory / (name or f"{uuid.uuid4().hex}.json")).write_text(json.dumps(entry))

    def ledger_entries(self):
        directory = self.home / "ledger"
        if not directory.is_dir():
            return []
        return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]

    def env(self, **extra):
        env = {"PATH": clean_path(), "HOME": str(self.tmp / "user-home"),
               "CLAUDE_CREDIT_HOME": str(self.home), "CLAUDE_CREDIT_USAGE_JSON": str(self.usage),
               "CLAUDE_CREDIT_NOW": iso(self.now),
               "CLAUDE_CREDIT_NOW_EPOCH": str(int(self.now.timestamp())),
               self.marker: "1"}
        env.update(extra)
        return env

    def verdict(self, **extra):
        return gate.evaluate(self.env(**extra))

    def survivors(self):
        """Pids of processes still carrying this world's marker."""
        found = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                environ = (entry / "environ").read_bytes()
            except OSError:
                continue
            if f"{self.marker}=1".encode() in environ.split(b"\0"):
                found.append(int(entry.name))
        return found


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    w.write_usage(usage_doc(AHEAD))
    w.write_config(config_doc())
    yield w
    if Path("/proc/self/environ").exists():
        assert w.survivors() == [], "a process from this test outlived it"


# =================================================================================
# The gate: closed reasons
# =================================================================================


def assert_closed(verdict, fragment):
    assert verdict["open"] is False, verdict
    assert fragment in verdict["reason"], verdict["reason"]


def test_closed_when_usage_json_is_missing(world):
    world.usage.unlink()
    assert_closed(world.verdict(), "usage.json missing")


def test_closed_when_usage_json_does_not_parse(world):
    world.usage.write_text("{not json", encoding="utf-8")
    assert_closed(world.verdict(), "usage.json unreadable")


def test_closed_when_the_claude_source_is_not_ok(world):
    world.write_usage(usage_doc(AHEAD, ok=False))
    assert_closed(world.verdict(), "usage data not ok")


def test_closed_when_usage_is_stale(world):
    world.write_usage(usage_doc(AHEAD, fetched_ago=dt.timedelta(minutes=16)))
    assert_closed(world.verdict(), "usage data stale (16 min old)")


def test_usage_just_inside_fifteen_minutes_is_fresh(world):
    world.write_usage(usage_doc(AHEAD, fetched_ago=dt.timedelta(minutes=15)))
    assert world.verdict()["open"] is True


def test_closed_when_config_is_missing(world):
    (world.home / "config.json").unlink()
    assert_closed(world.verdict(), "config.json missing")


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("wif"),
    lambda d: d["wif"].pop("federation_rule_id"),
    lambda d: d.update(slots="two"),
    lambda d: d.update(grants=[{"usd": 200, "granted": "2026-11-07T00:00:00Z",
                                "expires": "2026-10-08T00:00:00Z"}]),
    lambda d: d.update(next_grant={"day_of_month": 31, "usd": 200, "lifetime_days": 30}),
])
def test_closed_when_config_is_invalid(world, mutate):
    doc = config_doc()
    mutate(doc)
    world.write_config(doc)
    assert_closed(world.verdict(), "config.json invalid")


def test_closed_when_no_grant_is_active(world):
    world.write_config(config_doc(grants=[{"usd": 200, "granted": "2026-09-01T00:00:00Z",
                                           "expires": "2026-10-01T00:00:00Z"}],
                                  next_grant=False))
    assert_closed(world.verdict(), "no active grant")


def test_closed_while_a_closed_until_marker_is_in_the_future(world):
    marker = {"until": iso(NOW + dt.timedelta(hours=1)), "reason": "credit exhausted"}
    (world.home / "closed-until.json").write_text(json.dumps(marker))
    assert_closed(world.verdict(), "closed until 2026-10-20T13:00:00Z: credit exhausted")


def test_a_past_closed_until_marker_is_ignored(world):
    marker = {"until": iso(NOW - dt.timedelta(seconds=1)), "reason": "old"}
    (world.home / "closed-until.json").write_text(json.dumps(marker))
    assert world.verdict()["open"] is True


def test_closed_when_no_window_is_ahead_of_pace(world):
    world.write_usage(usage_doc(ON_PACE))
    verdict = world.verdict()
    assert_closed(verdict, "on pace")
    assert "week 40% used at 57% elapsed (overall)" in verdict["reason"]


def test_ahead_of_pace_before_half_elapsed_stays_closed(world):
    world.write_usage(usage_doc([window("seven_day", "overall", 45, 0.40)]))
    assert_closed(world.verdict(), "on pace")


def test_a_non_weekly_window_never_opens_the_lane(world):
    world.write_usage(usage_doc([window("five_hour", "session", 99, 0.9, period=18000),
                                 window("seven_day", "overall", 10, 0.6)]))
    assert_closed(world.verdict(), "on pace")


def test_a_bad_now_is_a_closed_verdict_not_an_error(world):
    verdict = world.verdict(CLAUDE_CREDIT_NOW="yesterday")
    assert_closed(verdict, "CLAUDE_CREDIT_NOW")


# =================================================================================
# The gate: opening
# =================================================================================


def test_open_when_overall_is_ahead_of_pace(world):
    verdict = world.verdict()
    assert verdict["open"] is True and verdict["spend_all"] is False
    assert verdict["reason"] == "week 64% used at 57% elapsed (overall)"
    assert verdict["active_grant_expires"] == "2026-11-07T00:00:00Z"
    assert {w["id"] for w in verdict["windows"]} == {"seven_day", "seven_day_fable"}


def test_open_when_only_fable_is_ahead_of_pace(world):
    world.write_usage(usage_doc([window("seven_day", "overall", 30, 0.57),
                                 window("seven_day_fable", "Fable", 70, 0.57)]))
    verdict = world.verdict()
    assert verdict["open"] is True
    assert verdict["reason"] == "week 70% used at 57% elapsed (Fable)"


def test_any_weekly_window_counts_not_only_the_named_ones(world):
    world.write_usage(usage_doc([window("seven_day", "overall", 10, 0.6),
                                 window("seven_day_other", "Other", 90, 0.6)]))
    assert world.verdict()["open"] is True


def test_ninety_eight_percent_opens_early_and_spends_everything(world):
    world.write_usage(usage_doc([window("seven_day", "overall", 98, 0.20),
                                 window("seven_day_fable", "Fable", 5, 0.20)]))
    verdict = world.verdict()
    assert verdict["open"] is True and verdict["spend_all"] is True
    assert verdict["reason"].startswith("week 98% used at 20% elapsed (overall)")


def test_elapsed_is_clamped_to_the_window(world):
    world.write_usage(usage_doc([window("seven_day", "overall", 50, 1.4)]))
    verdict = world.verdict()
    assert verdict["windows"][0]["elapsed"] == 1.0
    assert verdict["open"] is False


# =================================================================================
# The gate: allowance and spend
# =================================================================================


def test_the_allowance_spreads_the_grant_over_its_weeks(world):
    verdict = world.verdict()
    # 17.5 days to Nov 7 -> ceil(2.5) = 3 weeks.
    assert verdict["remaining_grant_usd"] == 200
    assert verdict["allowance_usd"] == pytest.approx(200 / 3, abs=1e-3)


def test_closed_when_the_weekly_allowance_is_used(world):
    world.ledger(NOW - dt.timedelta(hours=2), 40)
    world.ledger(NOW - dt.timedelta(hours=1), 30)
    verdict = world.verdict()
    assert verdict["spent_this_week_usd"] == 70
    # The allowance is the balance at the week's start (200) over 3 weeks.
    assert verdict["allowance_usd"] == pytest.approx(200 / 3, abs=1e-3)
    assert_closed(verdict, "weekly-allowance-used")


def test_spend_before_this_week_is_not_this_weeks(world):
    week_start = NOW - dt.timedelta(seconds=0.57 * WEEK)
    world.ledger(week_start - dt.timedelta(hours=1), 70)
    verdict = world.verdict()
    assert verdict["spent_this_week_usd"] == 0
    assert verdict["open"] is True
    assert verdict["remaining_grant_usd"] == 130


def test_spend_all_ignores_the_allowance(world):
    world.ledger(NOW - dt.timedelta(hours=1), 70)
    world.write_usage(usage_doc([window("seven_day", "overall", 99, 0.57)]))
    verdict = world.verdict()
    assert verdict["open"] is True and verdict["spend_all"] is True


def test_spend_all_still_stops_at_an_empty_grant(world):
    world.ledger(NOW - dt.timedelta(days=9), 200)
    world.write_usage(usage_doc([window("seven_day", "overall", 99, 0.57)]))
    assert_closed(world.verdict(), "grant spent")


def test_the_last_week_before_expiry_gets_everything_left(tmp_path):
    now = dt.datetime(2026, 11, 2, 12, 0, tzinfo=UTC)  # 4.5 days to Nov 7
    w = World(tmp_path, now=now)
    w.write_usage(usage_doc([window("seven_day", "overall", 70, 0.6, now=now)], now=now))
    w.write_config(config_doc())
    w.ledger(dt.datetime(2026, 10, 15, tzinfo=UTC), 80)
    verdict = w.verdict()
    assert verdict["remaining_grant_usd"] == 120
    assert verdict["allowance_usd"] == pytest.approx(120)
    assert verdict["open"] is True


def test_the_cost_report_wins_when_it_is_larger_than_the_ledger(world):
    world.ledger(NOW - dt.timedelta(days=9), 10)
    world.write_usage(usage_doc(AHEAD, costs=[
        {"date": "2026-10-07", "model": "example-model", "usd": 500},  # before the grant
        {"date": "2026-10-09", "model": "example-model", "usd": 30},
        {"date": "2026-10-12", "model": "example-model", "usd": 20}]))
    assert world.verdict()["remaining_grant_usd"] == 150


def test_the_ledger_wins_when_it_is_larger_than_the_cost_report(world):
    world.ledger(NOW - dt.timedelta(days=9), 60)
    world.write_usage(usage_doc(AHEAD, costs=[{"date": "2026-10-09", "model": "m", "usd": 5}]))
    assert world.verdict()["remaining_grant_usd"] == 140


# =================================================================================
# The gate: grants
# =================================================================================


def test_an_assumed_grant_comes_from_next_grant(world):
    world.write_config(config_doc(grants=[]))
    verdict = world.verdict()
    assert verdict["open"] is True
    # Oct 5 00:00 in Los Angeles (PDT) is 07:00Z; 30 days later is Nov 4.
    assert verdict["active_grant_expires"] == "2026-11-04T07:00:00Z"
    assert verdict["remaining_grant_usd"] == 200


def test_an_explicit_grant_replaces_the_assumed_one_it_overlaps(world):
    # Oct 8 explicit is the Oct 5 assumed grant arriving late: one grant, not two.
    verdict = world.verdict()
    assert verdict["remaining_grant_usd"] == 200
    assert verdict["active_grant_expires"] == "2026-11-07T00:00:00Z"


def test_fifo_across_overlapping_grants_on_november_6(tmp_path):
    now = dt.datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    w = World(tmp_path, now=now)
    w.write_usage(usage_doc([window("seven_day", "overall", 80, 0.6, now=now)], now=now))
    w.write_config(config_doc())
    verdict = w.verdict()
    # Oct 8 grant (to Nov 7) and the assumed Nov 5 grant (Nov 5 08:00Z PST, to Dec 5).
    assert verdict["active_grant_expires"] == "2026-11-07T00:00:00Z"
    assert verdict["remaining_grant_usd"] == 400
    # The expiring grant's last partial week takes all 200, the new one 200/5.
    assert verdict["allowance_usd"] == pytest.approx(200 + 200 / 5, abs=1e-3)

    env = w.env()
    run_gate(env, "record", "--cost", "1.5", "--session", "s-one", "--model", "example-model")
    assert [e["grant_expires"] for e in w.ledger_entries()] == ["2026-11-07T00:00:00Z"]


def test_fifo_overflows_to_the_next_grant_when_the_first_is_spent(tmp_path):
    now = dt.datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    w = World(tmp_path, now=now)
    w.write_usage(usage_doc([window("seven_day", "overall", 99, 0.6, now=now)], now=now))
    w.write_config(config_doc())
    w.ledger(dt.datetime(2026, 10, 20, tzinfo=UTC), 200, name="a-old.json")
    run_gate(w.env(), "record", "--cost", "2", "--session", "s-two", "--model", "m")
    new = [e for e in w.ledger_entries() if e["session_id"] == "s-two"]
    assert [e["grant_expires"] for e in new] == ["2026-12-05T08:00:00Z"]
    assert w.verdict()["remaining_grant_usd"] == pytest.approx(198)


def test_la_midnight_matches_the_tz_database():
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no tz database on this machine")
    day = dt.date(2026, 1, 1)
    while day.year == 2026:
        expected = dt.datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(UTC)
        assert gate.la_midnight(day.year, day.month, day.day) == expected, day
        day += dt.timedelta(days=1)


# =================================================================================
# The gate: next_check_at
# =================================================================================


def test_next_check_is_the_first_half_elapsed_moment_early_in_the_week(world):
    early = [window("seven_day", "overall", 45, 0.30), window("seven_day_fable", "Fable", 5, 0.20)]
    world.write_usage(usage_doc(early))
    verdict = world.verdict()
    assert verdict["open"] is False
    # The overall window reaches half elapsed in 0.2 weeks.
    assert verdict["next_check_at"] == iso(NOW + dt.timedelta(seconds=0.2 * WEEK))


def test_next_check_is_fifteen_minutes_otherwise(world):
    world.write_usage(usage_doc(ON_PACE))
    assert world.verdict()["next_check_at"] == iso(NOW + dt.timedelta(minutes=15))
    world.write_usage(usage_doc(AHEAD))
    assert world.verdict()["next_check_at"] == iso(NOW + dt.timedelta(minutes=15))
    world.usage.unlink()
    assert world.verdict()["next_check_at"] == iso(NOW + dt.timedelta(minutes=15))


# =================================================================================
# The gate's CLI
# =================================================================================


def run_gate(env, *args, stdin=""):
    proc = subprocess.run([PYTHON or sys.executable, str(LANE / "gate.py"), *args], env=env,
                          input=stdin, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_status_json_prints_every_field_and_exits_zero(world):
    out = json.loads(run_gate(world.env(), "status", "--json"))
    assert set(out) == {"open", "reason", "spend_all", "allowance_usd", "spent_this_week_usd",
                        "remaining_grant_usd", "active_grant_expires", "next_check_at",
                        "windows"}
    world.usage.unlink()
    out = json.loads(run_gate(world.env(), "status", "--json"))
    assert out["open"] is False and out["reason"] == "usage.json missing"


def test_status_line_is_tab_separated(world):
    fields = run_gate(world.env(), "status", "--line").rstrip("\n").split("\t")
    assert fields[0] == "open"
    assert fields[1] == str(int((NOW + dt.timedelta(minutes=15)).timestamp()))
    assert fields[2] == f"{200 / 3:.2f}"
    assert fields[3] == "week 64% used at 57% elapsed (overall)"


def test_record_writes_unique_ledger_files_atomically(world):
    for _ in range(3):
        left = run_gate(world.env(), "record", "--cost", "0.25", "--session", "s-x",
                        "--model", "example-model").strip()
    entries = world.ledger_entries()
    assert len(entries) == 3 and len({p.name for p in (world.home / "ledger").iterdir()}) == 3
    assert all(e["cost_usd"] == 0.25 and e["ts"] == iso(NOW) for e in entries)
    assert left == f"{200 / 3 - 0.75:.2f}"
    assert not [p for p in (world.home / "ledger").iterdir() if p.name.startswith(".")]


def test_close_until_next_grant_uses_the_next_arrival(world):
    out = run_gate(world.env(), "close-until", "--until", "next-grant", "--reason", "credit").strip()
    assert out == "2026-11-05T08:00:00Z"
    marker = json.loads((world.home / "closed-until.json").read_text())
    assert marker == {"until": "2026-11-05T08:00:00Z", "reason": "credit"}


def test_close_until_without_next_grant_is_a_day(world):
    world.write_config(config_doc(next_grant=False))
    out = run_gate(world.env(), "close-until", "--until", "next-grant", "--reason", "r").strip()
    assert out == iso(NOW + dt.timedelta(hours=24))


def test_windows_home_reads_the_wsl_cache(tmp_path):
    home = tmp_path / "linux-home"
    (home / ".config" / "claude-credit").mkdir(parents=True)
    (home / ".config" / "claude-credit" / "windows-home").write_text("/mnt/c/Users/example\n")
    env = {"HOME": str(home)}
    assert gate.windows_home(env, is_wsl=True) == "/mnt/c/Users/example"
    assert gate.credit_home(env) != ""  # resolvable
    assert gate.windows_home(env, is_wsl=False) == str(home)
    assert gate.credit_home({"CLAUDE_CREDIT_HOME": "/x"}) == "/x"


# =================================================================================
# Slots
# =================================================================================


def slots(world, *args, now=None):
    env = world.env(**({"CLAUDE_CREDIT_NOW": iso(now)} if now else {}))
    return subprocess.run([PYTHON or sys.executable, str(LANE / "slots.py"), *args], env=env,
                          capture_output=True, text=True)


def slot_file(world, n):
    return world.home / "slots" / f"slot-{n}.json"


def test_acquire_takes_free_slots_then_reports_busy(world):
    first, second, third = (slots(world, "acquire", "--pid", str(p)) for p in (101, 102, 103))
    assert (first.returncode, first.stdout.strip()) == (0, "1")
    assert (second.returncode, second.stdout.strip()) == (0, "2")
    assert third.returncode == 3 and third.stdout.strip() == "busy (2/2 slots)"
    data = json.loads(slot_file(world, 1).read_text())
    assert set(data) == {"host_kind", "pid", "started", "heartbeat"} and data["pid"] == 101


def test_a_stale_slot_is_taken_over(world):
    slots(world, "acquire", "--pid", "101")
    slots(world, "acquire", "--pid", "102")
    later = NOW + dt.timedelta(minutes=2)
    assert slots(world, "heartbeat", "2", "--pid", "102", now=later).returncode == 0
    taken = slots(world, "acquire", "--pid", "103", now=NOW + dt.timedelta(minutes=3, seconds=1))
    assert (taken.returncode, taken.stdout.strip()) == (0, "1")
    assert json.loads(slot_file(world, 1).read_text())["pid"] == 103
    assert json.loads(slot_file(world, 2).read_text())["pid"] == 102


def test_a_slot_at_exactly_three_minutes_is_not_stale(world):
    slots(world, "acquire", "--pid", "101")
    slots(world, "acquire", "--pid", "102")
    busy = slots(world, "acquire", "--pid", "103", now=NOW + dt.timedelta(minutes=3))
    assert busy.returncode == 3


def test_release_removes_only_the_owners_slot(world):
    slots(world, "acquire", "--pid", "101")
    assert slots(world, "release", "1", "--pid", "999").returncode == 0
    assert slot_file(world, 1).exists()
    assert slots(world, "heartbeat", "1", "--pid", "999").returncode == 4
    assert slots(world, "release", "1", "--pid", "101").returncode == 0
    assert not slot_file(world, 1).exists()


@pytest.mark.parametrize("stale", [False, True])
def test_racing_processes_never_share_a_slot(world, stale):
    if stale:
        for n, pid in ((1, 91), (2, 92)):
            slots(world, "acquire", "--pid", str(pid))
    when = NOW + dt.timedelta(minutes=10) if stale else NOW
    env = world.env(CLAUDE_CREDIT_NOW=iso(when))
    procs = [subprocess.Popen([PYTHON or sys.executable, str(LANE / "slots.py"), "acquire",
                               "--pid", str(200 + i)], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for i in range(10)]
    results = [(p.wait(), p.stdout.read().strip(), p.stderr.read()) for p in procs]
    for p in procs:
        p.stdout.close()
        p.stderr.close()
    won = sorted(out for code, out, _ in results if code == 0)
    assert won == ["1", "2"], results
    assert all(code == 3 for code, out, _ in results if out not in ("1", "2")), results
    owners = {json.loads(slot_file(world, n).read_text())["pid"] for n in (1, 2)}
    assert len(owners) == 2 and owners <= set(range(200, 210))


# =================================================================================
# The signer, with a throwaway software key
# =================================================================================

PWSH = shutil.which("pwsh")


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftwareKey:
    """A throwaway RSA key: python's cryptography if present, else openssl."""

    def __init__(self, directory):
        self.pem = directory / "throwaway.pem"
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError:
            self.private = None
            if shutil.which("openssl") is None:
                pytest.skip("neither python cryptography nor openssl is available")
            subprocess.run(["openssl", "genrsa", "-out", str(self.pem), "2048"], check=True,
                           capture_output=True)
            text = subprocess.run(["openssl", "rsa", "-in", str(self.pem), "-noout", "-text"],
                                  check=True, capture_output=True, text=True).stdout
            modulus = re.search(r"modulus:\s*([0-9a-f:\s]+?)\s*publicExponent", text, re.S)
            self.n = int(re.sub(r"[^0-9a-f]", "", modulus.group(1)), 16)
            self.e = int(re.search(r"publicExponent: (\d+)", text).group(1))
            return
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem.write_bytes(self.private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        numbers = self.private.public_key().public_numbers()
        self.n, self.e = numbers.n, numbers.e

    def verify(self, signing_input, signature, directory):
        if self.private is not None:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            self.private.public_key().verify(signature, signing_input, padding.PKCS1v15(),
                                             hashes.SHA256())
            return True
        pub = directory / "throwaway.pub"
        subprocess.run(["openssl", "rsa", "-in", str(self.pem), "-pubout", "-out", str(pub)],
                       check=True, capture_output=True)
        (directory / "sig.bin").write_bytes(signature)
        (directory / "msg.bin").write_bytes(signing_input)
        proc = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature",
                               str(directory / "sig.bin"), str(directory / "msg.bin")],
                              capture_output=True)
        return proc.returncode == 0

    def thumbprint(self):
        def to_bytes(value):
            return value.to_bytes((value.bit_length() + 7) // 8, "big")
        canon = json.dumps({"e": _b64(to_bytes(self.e)), "kty": "RSA", "n": _b64(to_bytes(self.n))},
                           separators=(",", ":"), sort_keys=True)
        return _b64(hashlib.sha256(canon.encode()).digest())


def sign(tmp_path, key, out, *extra):
    config = tmp_path / "config.json"
    config.write_text(json.dumps(config_doc()), encoding="utf-8")
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File",
                           str(LANE / "Sign-CreditJwt.ps1"), "-ConfigPath", str(config),
                           "-OutFile", str(out), "-KeyPem", str(key.pem), *extra],
                          capture_output=True, text=True, env=dict(os.environ, HOME=str(tmp_path)))


@pytest.mark.skipif(PWSH is None, reason="no pwsh on this machine")
def test_the_signer_writes_a_verifiable_rs256_jwt(tmp_path):
    key = SoftwareKey(tmp_path)
    out = tmp_path / "run" / "assertion.jwt"
    before = int(dt.datetime.now(UTC).timestamp())
    proc = sign(tmp_path, key, out)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    token = out.read_text(encoding="ascii")
    head, body, sig = token.split(".")
    header, claims = json.loads(_unb64(head)), json.loads(_unb64(body))
    assert header == {"alg": "RS256", "typ": "JWT", "kid": key.thumbprint()}
    assert key.verify(f"{head}.{body}".encode("ascii"), _unb64(sig), tmp_path)
    assert claims["iss"] == "https://issuer.example.com"
    assert claims["sub"] == "api-credit-lane"
    assert claims["aud"] == "https://api.anthropic.com"
    assert claims["exp"] - claims["iat"] == 3000 + 10
    assert before - 10 - 5 <= claims["iat"] <= before - 10 + 120
    assert re.fullmatch(r"[0-9a-f]{32}", claims["jti"])
    assert [p.name for p in out.parent.iterdir()] == ["assertion.jwt"]
    if os.name != "nt":
        assert out.stat().st_mode & 0o077 == 0

    again = sign(tmp_path, key, out, "-LifetimeSeconds", "3600")
    assert again.returncode == 0, again.stderr
    claims2 = json.loads(_unb64(out.read_text(encoding="ascii").split(".")[1]))
    assert claims2["jti"] != claims["jti"]
    assert claims2["exp"] - claims2["iat"] == 3610


@pytest.mark.skipif(PWSH is None, reason="no pwsh on this machine")
def test_the_signer_refuses_a_lifetime_over_an_hour(tmp_path):
    key = SoftwareKey(tmp_path)
    out = tmp_path / "assertion.jwt"
    proc = sign(tmp_path, key, out, "-LifetimeSeconds", "3601")
    assert proc.returncode != 0
    assert not out.exists()


def test_the_signer_is_ascii():
    (LANE / "Sign-CreditJwt.ps1").read_bytes().decode("ascii")


# =================================================================================
# The wrapper, with a fake claude and a fake signer
# =================================================================================

FAKE_CLAUDE = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_DIR/calls.txt"
if [[ "${1:-}" == auth ]]; then
  cat "$FAKE_DIR/auth.json"
  exit 0
fi
env > "$FAKE_DIR/child-env.txt"
cp "$ANTHROPIC_IDENTITY_TOKEN_FILE" "$FAKE_DIR/token-seen.txt" 2>/dev/null
cat "$FAKE_DIR/result.json"
exit "$(cat "$FAKE_DIR/exit-code" 2>/dev/null || echo 0)"
"""

FAKE_SIGNER = r"""#!/usr/bin/env bash
out=""
while [[ $# -gt 0 ]]; do
  [[ "$1" == -OutFile ]] && out="$2"
  shift
done
printf 'signed\n' >> "$FAKE_DIR/signs.txt"
printf 'eyJhbGciOiJSUzI1NiJ9.eyJqdGkiOiJ4In0.c2ln' > "$out"
"""

API_AUTH = {"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"}
SUCCESS = {"type": "result", "subtype": "success", "is_error": False,
           "result": "hello from the child", "total_cost_usd": 0.42, "session_id": "child-1"}
LEAKY = {"ANTHROPIC_API_KEY": "leak-api-key", "ANTHROPIC_AUTH_TOKEN": "leak-auth",
         "CLAUDE_CODE_OAUTH_TOKEN": "leak-oauth", "ANTHROPIC_PROFILE": "leak-profile"}


class Fakes:
    def __init__(self, world):
        self.dir = world.tmp / "fakes"
        self.dir.mkdir()
        for name, body in (("claude", FAKE_CLAUDE), ("signer", FAKE_SIGNER)):
            path = self.dir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        self.auth(API_AUTH)
        self.result(SUCCESS)

    def auth(self, doc):
        (self.dir / "auth.json").write_text(json.dumps(doc))

    def result(self, doc, code=0):
        (self.dir / "result.json").write_text(json.dumps(doc))
        (self.dir / "exit-code").write_text(str(code))

    def calls(self):
        path = self.dir / "calls.txt"
        return path.read_text().splitlines() if path.exists() else []

    def child_env(self):
        lines = (self.dir / "child-env.txt").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)


def run_wrapper(world, fakes, *args, claudecode="1", extra=None, stdin=""):
    env = world.env(CLAUDE_CREDIT_CLAUDE_BIN=str(fakes.dir / "claude"),
                    CLAUDE_CREDIT_SIGNER=str(fakes.dir / "signer"), FAKE_DIR=str(fakes.dir),
                    CLAUDE_CODE_SESSION_ID="s-parent", **LEAKY)
    if claudecode is not None:
        env["CLAUDECODE"] = claudecode
    env.update(extra or {})
    # The timeout turns a wrapper that never returns (a heartbeat loop it
    # failed to stop) into a failure instead of a hung suite.
    return subprocess.run([BASH, str(WRAPPER), *args], env=env, input=stdin,
                          capture_output=True, text=True, timeout=60)


def assert_released(world):
    slot_dir = world.home / "slots"
    assert not slot_dir.exists() or list(slot_dir.iterdir()) == []
    run_dir = world.home / "run"
    assert not run_dir.exists() or list(run_dir.iterdir()) == []


@pytest.fixture
def fakes(world):
    return Fakes(world)


@posix_only
@pytest.mark.parametrize("claudecode", [None, "", "0", "true"])
def test_the_wrapper_refuses_outside_claude_code(world, fakes, claudecode):
    proc = run_wrapper(world, fakes, "-p", "task", claudecode=claudecode)
    assert proc.returncode == 64
    assert "CLAUDECODE=1 is not set" in proc.stderr
    assert fakes.calls() == [] and proc.stdout == ""


@posix_only
@pytest.mark.parametrize("flag", [["--output-format", "text"], ["--output-format=stream-json"]])
def test_the_wrapper_refuses_a_caller_output_format(world, fakes, flag):
    proc = run_wrapper(world, fakes, *flag, "-p", "task")
    assert proc.returncode == 64
    assert "refusing --output-format" in proc.stderr
    assert fakes.calls() == []


@posix_only
def test_a_closed_lane_exits_75_and_never_runs_claude(world, fakes):
    world.write_usage(usage_doc(ON_PACE))
    proc = run_wrapper(world, fakes, "-p", "task")
    assert proc.returncode == 75
    assert proc.stderr.startswith("claude-credit: lane closed (on pace: ")
    assert proc.stderr.rstrip().endswith("; use the Agent tool instead")
    assert fakes.calls() == [] and proc.stdout == ""
    assert not (fakes.dir / "signs.txt").exists()


@posix_only
def test_a_busy_lane_exits_75(world, fakes):
    for pid in (11, 12):
        subprocess.run([PYTHON, str(LANE / "slots.py"), "acquire", "--pid", str(pid)],
                       env=world.env(), check=True, capture_output=True)
    proc = run_wrapper(world, fakes, "-p", "task")
    assert proc.returncode == 75
    assert "lane busy (2/2 slots)" in proc.stderr
    assert fakes.calls() == []
    assert len(list((world.home / "slots").iterdir())) == 2


@posix_only
@pytest.mark.parametrize("auth, shown", [
    ({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
      "subscriptionType": "max"}, "claude.ai subscription"),
    ({"loggedIn": True, "authMethod": "mystery_method", "apiProvider": "firstParty"},
     "mystery_method"),
    ({"loggedIn": False, "authMethod": "none"}, "none, not logged in"),
    ({"loggedIn": True, "authMethod": "oauth_token", "subscriptionType": "pro"},
     "oauth_token subscription"),
    ({"loggedIn": True, "authMethod": "api_key", "apiProvider": "bedrock"}, "api_key via bedrock"),
    ("not json at all", "unparseable"),
])
def test_an_unconfirmed_auth_method_exits_75_without_spending(world, fakes, auth, shown):
    if isinstance(auth, str):
        (fakes.dir / "auth.json").write_text(auth)
    else:
        fakes.auth(auth)
    proc = run_wrapper(world, fakes, "-p", "task")
    assert proc.returncode == 75, proc.stderr
    assert f"could not confirm API-credit auth ({shown}); not spending" in proc.stderr
    assert fakes.calls() == ["auth status --json"]
    assert world.ledger_entries() == []
    assert_released(world)


@posix_only
def test_the_success_path(world, fakes):
    proc = run_wrapper(world, fakes, "--model", "example-small", "-p", "say hello")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "hello from the child\n"
    left = 200 / 3 - 0.42
    assert proc.stderr == f"lane: api · $0.42 · ${left:.2f} left this week\n"

    calls = fakes.calls()
    assert calls[0] == "auth status --json"
    assert calls[1] == "--model example-small -p say hello --output-format json"

    entries = world.ledger_entries()
    assert len(entries) == 1
    assert entries[0]["cost_usd"] == 0.42
    assert entries[0]["session_id"] == "s-parent" and entries[0]["model"] == "example-small"
    assert entries[0]["grant_expires"] == "2026-11-07T00:00:00Z"

    child = fakes.child_env()
    assert child["ANTHROPIC_FEDERATION_RULE_ID"] == "fdrl_EXAMPLE01"
    assert child["ANTHROPIC_ORGANIZATION_ID"] == "00000000-0000-4000-8000-000000000000"
    assert child["ANTHROPIC_SERVICE_ACCOUNT_ID"] == "svac_EXAMPLE01"
    assert child["ANTHROPIC_WORKSPACE_ID"] == "wrkspc_EXAMPLE01"
    assert child["ANTHROPIC_IDENTITY_TOKEN_FILE"].endswith("/assertion.jwt")
    assert (fakes.dir / "token-seen.txt").read_text().count(".") == 2
    for name in LEAKY:
        assert name not in child, name
    assert_released(world)


@posix_only
def test_the_prompt_can_come_on_stdin(world, fakes):
    proc = run_wrapper(world, fakes, "-p", stdin="from stdin\n")
    assert proc.returncode == 0, proc.stderr
    assert fakes.calls()[1] == "-p --output-format json"


@posix_only
def test_spend_all_shows_in_the_status_line(world, fakes):
    world.write_usage(usage_doc([window("seven_day", "overall", 99, 0.57)]))
    proc = run_wrapper(world, fakes, "-p", "task")
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == "lane: api · $0.42 · spend-all\n"


@posix_only
@pytest.mark.parametrize("result, code", [
    ({"type": "result", "subtype": "success", "is_error": True,
      "result": "Credit balance is too low to access the Anthropic API.", "total_cost_usd": 0}, 1),
    ({"type": "result", "subtype": "error_during_execution", "is_error": True,
      "error": "billing_error", "result": "", "total_cost_usd": 0}, 1),
    ({"type": "result", "subtype": "success", "is_error": True,
      "result": "API Error: 402 payment required", "total_cost_usd": 0.01}, 0),
])
def test_a_billing_error_closes_the_lane_and_exits_76(world, fakes, result, code):
    fakes.result(result, code)
    proc = run_wrapper(world, fakes, "-p", "task")
    assert proc.returncode == 76, proc.stderr
    assert proc.stderr.startswith("claude-credit: API credit unavailable (")
    assert "lane closed until 2026-11-05T08:00:00Z; use the Agent tool" in proc.stderr
    marker = json.loads((world.home / "closed-until.json").read_text())
    assert marker["until"] == "2026-11-05T08:00:00Z"
    assert world.verdict()["open"] is False
    assert_released(world)


@posix_only
def test_any_other_child_failure_passes_its_exit_code_through(world, fakes):
    fakes.result({"type": "result", "subtype": "error_max_turns", "is_error": True,
                  "result": "", "total_cost_usd": 0.05}, 3)
    proc = run_wrapper(world, fakes, "-p", "task")
    assert proc.returncode == 3
    assert not (world.home / "closed-until.json").exists()
    assert [e["cost_usd"] for e in world.ledger_entries()] == [0.05]
    assert_released(world)


@posix_only
def test_a_failed_signature_exits_75_without_running_claude(world, fakes):
    (fakes.dir / "signer").write_text("#!/usr/bin/env bash\nexit 1\n")
    proc = run_wrapper(world, fakes, "-p", "task")
    assert proc.returncode == 75
    assert "could not sign" in proc.stderr
    assert fakes.calls() == []
    assert_released(world)


def test_the_billing_classifier_reads_json_fields_loosely():
    assert lane.classify({"is_error": True, "result": "spend limit reached"}, "", 1)[0] == "billing"
    assert lane.classify({"is_error": True, "result": "Insufficient credit"}, "", 1)[0] == "billing"
    assert lane.classify(None, "Error: 403 billing_error", 1)[0] == "billing"
    assert lane.classify({"is_error": True, "api_error_status": 402}, "", 1)[0] == "billing"
    assert lane.classify({"is_error": True, "result": "tool failed"}, "", 2)[0] == "error"
    assert lane.classify(SUCCESS, "", 0) == ("ok", 0.42, "")


# =================================================================================
# The hooks
# =================================================================================

SESSION = "s-hook-01"


def hook_input(transcript="/home/example/.claude/projects/-work/s.jsonl", event="SessionStart"):
    return json.dumps({"session_id": SESSION, "cwd": "/work", "hook_event_name": event,
                       "transcript_path": transcript})


def run_hook(world, script, payload, path=None, **extra):
    env = world.env(CLAUDE_PROJECT_DIR="/work", **extra)
    if path is not None:
        env["PATH"] = path
    proc = subprocess.run([BASH, str(LANE / script)], input=payload, env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    return proc.stdout


def state(world):
    path = world.home / "sessions" / f"{SESSION}.state"
    return path.read_text().split() if path.exists() else None


@posix_only
def test_a_non_claude_harness_gets_nothing(world):
    codex = hook_input(transcript="/home/example/.codex/sessions/s.jsonl")
    assert run_hook(world, "on-session-start.sh", codex) == ""
    assert state(world) is None
    assert run_hook(world, "on-prompt.sh", codex) == ""
    assert state(world) is None


@posix_only
def test_a_closed_lane_starts_silently_and_records_its_state(world):
    world.write_usage(usage_doc(ON_PACE))
    assert run_hook(world, "on-session-start.sh", hook_input()) == ""
    assert state(world) == ["closed", str(int((NOW + dt.timedelta(minutes=15)).timestamp()))]


@posix_only
def test_an_open_lane_starts_with_valid_json(world):
    out = run_hook(world, "on-session-start.sh", hook_input())
    answer = json.loads(out)
    assert answer["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    text = answer["hookSpecificOutput"]["additionalContext"]
    assert 2 <= len(text.splitlines()) <= 4
    assert "week 64% used at 57% elapsed (overall)" in text
    assert 'claude-credit -p "<task>"' in text and "--model" in text
    assert "75" in text and "76" in text and "Agent tool" in text and "2 slots" in text
    assert state(world)[0] == "open"


@posix_only
def test_the_prompt_fast_path_starts_no_python(world, tmp_path):
    sessions = world.home / "sessions"
    sessions.mkdir()
    (sessions / f"{SESSION}.state").write_text(f"closed {int(NOW.timestamp()) + 60}\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    tripped = tmp_path / "python-ran"
    for name in ("python3", "python"):
        stub = stubs / name
        stub.write_text(f"#!/bin/sh\ntouch '{tripped}'\nexit 97\n")
        stub.chmod(0o755)
    out = run_hook(world, "on-prompt.sh", hook_input(event="UserPromptSubmit"),
                   path=f"{stubs}{os.pathsep}{clean_path()}")
    assert out == ""
    assert not tripped.exists()


@posix_only
def test_a_state_flip_is_announced_once(world):
    sessions = world.home / "sessions"
    sessions.mkdir()
    (sessions / f"{SESSION}.state").write_text(f"closed {int(NOW.timestamp()) - 1}\n")
    payload = hook_input(event="UserPromptSubmit")
    answer = json.loads(run_hook(world, "on-prompt.sh", payload))
    assert answer["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    text = answer["hookSpecificOutput"]["additionalContext"]
    assert text.startswith("API-credit lane now OPEN: week 64% used")
    assert "\n" not in text
    assert run_hook(world, "on-prompt.sh", payload) == ""  # fast path
    later = NOW + dt.timedelta(minutes=16)
    world.write_usage(usage_doc(AHEAD, now=later))
    late = {"CLAUDE_CREDIT_NOW": iso(later), "CLAUDE_CREDIT_NOW_EPOCH": str(int(later.timestamp()))}
    assert run_hook(world, "on-prompt.sh", payload, **late) == ""  # still open: silent
    later2 = later + dt.timedelta(minutes=16)
    late2 = {"CLAUDE_CREDIT_NOW": iso(later2),
             "CLAUDE_CREDIT_NOW_EPOCH": str(int(later2.timestamp()))}
    world.write_usage(usage_doc([window("seven_day", "overall", 10, 0.6, now=later2)], now=later2))
    closed = json.loads(run_hook(world, "on-prompt.sh", payload, **late2))
    assert closed["hookSpecificOutput"]["additionalContext"].startswith(
        "API-credit lane now CLOSED: on pace")


@posix_only
def test_session_start_removes_week_old_state_files(world):
    sessions = world.home / "sessions"
    sessions.mkdir()
    old, recent = sessions / "s-old.state", sessions / "s-recent.state"
    for path in (old, recent):
        path.write_text("closed 0\n")
    os.utime(old, (NOW.timestamp() - 8 * 86400,) * 2)
    os.utime(recent, (NOW.timestamp() - 6 * 86400,) * 2)
    run_hook(world, "on-session-start.sh", hook_input())
    assert not old.exists() and recent.exists()


# =================================================================================
# hooks/claude-code.json and the manifests
# =================================================================================


def claude_handlers():
    config = json.loads(CLAUDE_HOOKS.read_text(encoding="utf-8"))
    for event, groups in config["hooks"].items():
        for group in groups:
            for handler in group["hooks"]:
                yield event, group, handler


def test_claude_code_json_holds_the_lane_hooks():
    config = json.loads(CLAUDE_HOOKS.read_text(encoding="utf-8"))
    assert set(config) == {"hooks"}
    assert set(config["hooks"]) == {"SessionStart", "UserPromptSubmit"}
    matchers = {event: group.get("matcher") for event, group, _ in claude_handlers()}
    assert set(matchers["SessionStart"].split("|")) == {"startup", "resume", "clear", "compact"}


def test_every_lane_handler_is_a_shell_form_script_with_a_timeout():
    found = list(claude_handlers())
    assert len(found) == 2
    prefix = '"${CLAUDE_PLUGIN_ROOT}"/'
    for event, _, handler in found:
        assert handler["type"] == "command", event
        assert handler["command"].startswith(prefix + "hooks/credit-lane/"), handler
        assert "commandWindows" not in handler  # Codex never loads this file
        target = PLUGIN / handler["command"][len(prefix):]
        assert target.is_file(), target
        if os.name != "nt":
            assert os.access(target, os.X_OK), target
        assert isinstance(handler["timeout"], int) and 0 < handler["timeout"] <= 30


def test_no_lane_handler_runs_through_a_bare_bash():
    config = json.loads(CLAUDE_HOOKS.read_text(encoding="utf-8"))
    assert check_consistency._bare_bash_hooks(config) == 0


def test_only_the_claude_manifest_names_claude_code_json():
    claude = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert claude["hooks"] == "./hooks/claude-code.json"
    root = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    assert "hooks" not in root


def test_the_lane_executables_are_executable():
    if os.name == "nt":
        pytest.skip("POSIX file modes")
    for path in (WRAPPER, LANE / "on-session-start.sh", LANE / "on-prompt.sh"):
        assert os.access(path, os.X_OK), path


def test_the_example_config_is_valid_and_invented(tmp_path):
    example = json.loads((LANE / "config.example.json").read_text(encoding="utf-8"))
    (tmp_path / "config.json").write_text(json.dumps(example))
    config = gate.load_config(str(tmp_path))
    assert config["slots"] == 2
    assert example["wif"]["issuer"].endswith(".example.com")
    for key in ("service_account_id", "federation_rule_id", "workspace_id"):
        assert "EXAMPLE" in example["wif"][key]
