#!/usr/bin/env python3
"""slots.py - machine-wide run slots for the API-credit lane (ADR 0018).

At most config.slots (default 2) `claude-credit` runs at once, counted across
Windows and WSL processes, which share credit_home:

  credit_home/slots/slot-<n>.json   {host_kind, pid, started, heartbeat}

Subcommands (the owner is identified by --pid plus this process's host kind):

  acquire --pid P         print the slot number taken; exit 3 with `busy`
                          when every slot is held
  heartbeat <n> --pid P   rewrite the slot's heartbeat; exit 4 when the slot
                          is no longer this owner's
  release <n> --pid P     remove the slot if it is this owner's

A slot is taken by creating its file with O_CREAT|O_EXCL, so two racing
takers cannot both win. A slot whose heartbeat is older than 3 minutes is
stale: a taker first wins slot-<n>.takeover (O_EXCL too), re-reads the slot,
and only then removes and re-creates it, so a fresh slot is never removed by
a taker that read it while it was stale. Liveness is by heartbeat age only:
a pid means nothing across operating systems.

The current time is CLAUDE_CREDIT_NOW when set (tests), else the clock.
Standard library only.
"""

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gate  # noqa: E402

STALE_SECONDS = 180
TAKEOVER_STALE_SECONDS = 60


def host_kind():
    if os.name == "nt" or os.environ.get("MSYSTEM"):
        return "windows"
    return "wsl" if gate._is_wsl() else "linux"


def slot_dir():
    return os.path.join(gate.credit_home(), "slots")


def slot_count():
    try:
        return gate.load_config(gate.credit_home())["slots"]
    except gate.Closed:
        return 2


def _path(n):
    return os.path.join(slot_dir(), f"slot-{n}.json")


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}  # unreadable: judged by its mtime below


def _age(path, data, now):
    beat = gate.parse_time(data.get("heartbeat")) if data else None
    if beat is not None:
        return (now - beat).total_seconds()
    try:
        return now.timestamp() - os.stat(path).st_mtime
    except OSError:
        return 0.0


def _record(pid, now, started=None):
    return json.dumps({"host_kind": host_kind(), "pid": pid,
                       "started": started or gate.iso(now), "heartbeat": gate.iso(now)}) + "\n"


def _create(path, pid, now):
    """O_EXCL create; True when this call made the file."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_record(pid, now))
    return True


def _take_over(path, pid, now):
    lock = path + ".takeover"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        try:
            if now.timestamp() - os.stat(lock).st_mtime > TAKEOVER_STALE_SECONDS:
                os.unlink(lock)  # a taker died mid-takeover; the next run retries
        except OSError:
            pass
        return False
    os.close(fd)
    try:
        data = _read(path)
        if data is not None and _age(path, data, now) <= STALE_SECONDS:
            return False  # someone refreshed or re-took it meanwhile
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return _create(path, pid, now)
    finally:
        try:
            os.unlink(lock)
        except OSError:
            pass


def acquire(pid):
    now = gate.now_utc()
    os.makedirs(slot_dir(), exist_ok=True)
    count = slot_count()
    for n in range(1, count + 1):
        if _create(_path(n), pid, now):
            return n
    for n in range(1, count + 1):
        path = _path(n)
        data = _read(path)
        if data is None:
            if _create(path, pid, now):
                return n
        elif _age(path, data, now) > STALE_SECONDS and _take_over(path, pid, now):
            return n
    return None


def _mine(data, pid):
    return bool(data) and data.get("pid") == pid and data.get("host_kind") == host_kind()


def heartbeat(n, pid):
    path = _path(n)
    data = _read(path)
    if not _mine(data, pid):
        return False
    gate._atomic_write(path, _record(pid, gate.now_utc(), data.get("started")))
    return True


def release(n, pid):
    path = _path(n)
    if _mine(_read(path), pid):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(prog="slots.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("acquire", "heartbeat", "release"):
        cmd = sub.add_parser(name)
        if name != "acquire":
            cmd.add_argument("n", type=int)
        cmd.add_argument("--pid", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "acquire":
            n = acquire(args.pid)
            if n is None:
                print(f"busy ({slot_count()}/{slot_count()} slots)")
                return 3
            print(n)
            return 0
        if args.command == "heartbeat":
            return 0 if heartbeat(args.n, args.pid) else 4
        release(args.n, args.pid)
        return 0
    except (OSError, gate.Closed) as exc:
        print(f"slots.py {args.command}: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
