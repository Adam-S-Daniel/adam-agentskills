# 0022. Install the newer pin when one lock descends from the other

- **Status:** Accepted (amends case (b) of [0007](0007-install-the-union-of-every-discovered-lock.md))
- **Date:** 2026-10-31
- **Deciders:** Adam Daniel, on 2026-10-31

## Context

[ADR 0007](0007-install-the-union-of-every-discovered-lock.md) has
`.claude/hooks/skills-bootstrap.sh` install the union of every `skills.lock` a
multi-repo session discovers. Its case (b) covers one name pinned at different
digests by different locks. Neither row installs, and the verdict names the
locks. ADR 0005 and ADR 0007 both refused a tiebreak, because between two
pinned locks nothing seemed to make one more authoritative.

The fleet's commonest case (b) is not two registries disagreeing. It is one
registry and a delay:

1. A change to a skill merges in this registry, and this registry's own
   `skills.lock` re-pins in the same change, so its ref and that skill's digest
   move at once.
2. Each consumer repo's lock still pins the older commit of the same registry
   until a bumper re-pins it, which takes hours or days.
3. Every session that attaches this registry plus any lagging consumer sees
   the edited skill at two digests. Under case (b) it installs neither.

The longer the registry is edited, the more often this happens, and it hits
exactly the skills that just changed. From 2026-10-29 to 2026-10-31,
`skills-doctor` was missing from project sessions that attached this registry
alongside consumers whose locks had not caught up. That is the skill a reader
would use to diagnose the missing install.

The two locks are not peers in that state. Both pin commits of one
repository, and that repository's history records that one commit came after
the other: the older pin is an ancestor of the newer one.

## Decision

When every row that names a destination comes from the same registry (the same
normalized URL), for the same `<bundle>/<skill>` key, at two or more different
full 40-hex commits, and no lock contributes two rows for that name, the hook
installs the row from the lock whose ref descends from every other contesting
ref, provided that ref is also reachable from the registry's default branch.
The winning bytes are verified against the winning lock's own digest by
the ordinary install path. No digest from a losing lock is used for the name.

Ancestry is decided after the fetch. Sources are `--depth 1`, so for a
candidate descendant the hook deepens that checkout with `--shallow-since`. The
cutoff is the oldest other contesting commit's committer time minus one day.
The deepen goes through `run_git`, under the same 60-second fetch budget and
process-group kill as every other fetch. Then
`git merge-base --is-ancestor <older> <candidate>` must succeed for every other
ref. Candidates are tried newest-first by committer time, and at most one can
pass. If the candidate's commit time is more than 90 days after the oldest
other pin's, the hook does not fetch across that gap and the candidate fails.

**The winner must also be on the registry's default branch.** Descending from
the other pins is not enough on its own. GitHub serves every object in a
repository's fork network by SHA through the upstream URL. So a lock can pin an
unmerged pull-request or fork commit whose parent is the honest pin: it
fetches, it descends from the honest pin, and on descent alone it would win.
The hook therefore fetches the remote's `HEAD` (asked for by name, not a
hardcoded `main`) into a separate scratch repository, bounded by
`--shallow-since` at the winner's commit time minus one day, under the same
budget. It then requires `git merge-base --is-ancestor <winner> <that HEAD>`.
Only objects reachable from that HEAD arrive in that repository, so a winner
that is missing there is not on the default branch, and the contest fails
closed.

Everything else stays case (b), with the same notice, verdict text, `dup`
handling and removal as before:

- the rows come from different registries, or have different keys;
- a ref is not a full commit SHA;
- one source pins two digests for the name;
- the newest pin is not reachable from the registry's default branch (an
  unmerged pull-request or fork commit, or a default-branch fetch that fails);
- the refs have diverged;
- more than one ref is "newest";
- ancestry cannot be established: a fetch fails, the budget is spent, the
  older commit is not within the bounded history, or the pins are more than 90
  days apart by committer time;
- any lock contributes two rows for the name (case (c)).

A resolved name counts once in `N/M`, as case (a) does. It is reported in a
note on an `— OK` verdict, not as a problem:
`installed the newer pin of 1 skill where one lock is behind another on the same registry (alpha from <newer repo> over <behind repo>)`.

## Consequences

- The lag window stops costing the edited skill. A session that attaches the
  registry and a lagging consumer gets the registry's current version of every
  skill the two disagree on. The consumer's other skills are unaffected, since
  they already unioned per name.
- **This tiebreak is not invented.** ADR 0005 refused tiebreaks such as sort
  order, scan order and "the project's own repo wins", because nothing in the
  input makes them true. The registry's merged history orders the two pins. The
  hook proves both that the newer pin descends from the older one and that the
  registry's default branch contains the newer pin, before using either fact.
  Descent alone would not do. Objects served by the registry's URL include
  unmerged fork and pull-request commits, so "the registry served this commit"
  does not mean "the registry merged it". When the hook cannot prove both, it
  does not use either.
- **Integrity is unchanged.** The tiebreak picks a row, not a trust level. The
  winning row is checked against its own lock's digest, so a tampered newer
  lock installs nothing for that name. It does not fall back to the older
  lock's bytes. `test_the_winning_pin_is_still_verified_against_its_own_lock`
  pins this.
- **What still fails closed, and why:**
  - Different registries: their histories do not order each other, even when
    one is a mirror of the other
    (`test_pins_of_two_registries_with_one_history_still_install_neither`).
  - Diverged pins: no history orders them
    (`test_diverged_pins_of_one_registry_still_install_neither`).
  - Several incomparable newest pins: no single descendant exists
    (`test_three_locks_with_two_incomparable_newest_pins_install_neither`).
  - A newer pin the registry never merged: it descends from the honest pin
    but is not on the default branch
    (`test_a_pull_request_commit_descending_from_the_honest_pin_does_not_win`).
  - Ancestry the hook cannot establish
    (`test_ancestry_the_bounded_fetch_cannot_reach_installs_neither`):
    "not shown to be an ancestor" must never become "pick the other one".
  - Pins more than 90 days apart
    (`test_a_contest_wider_than_the_window_installs_neither`): committer dates
    are whatever the author wrote, so a forged ancient date on the older pin
    must not buy a full-history fetch.
- **Session start does more network work in this one case.** The extra
  fetches cover the history between the two pins plus a day, and the
  default branch back to a day before the winner, and only for a contested
  name. The existing budget bounds it. If the budget is spent, the hook falls
  back to case (b) and does not delay the session further.
- **A heuristic sets the window, never the outcome.** The one-day margin
  allows for clock skew between committers. A history with older dates on the
  path between the pins, such as a rebase or a skewed clock, can push the older
  pin outside the window. The outcome is then case (b), never a wrong pick.
- **`skills-doctor` does not model this yet.** Its `read_union` in
  `check_provenance.py` copies the hook's original case (b) and reports a
  resolved name as `deleted-by-the-cross-lock-conflict`. Teaching it requires
  ancestry it cannot compute offline. That change is a skill-content change
  with its own impact entry and eval, so it is left to a follow-up.

## Alternatives considered

- **Keep case (b) unconditionally and shorten the lag** with faster bumpers.
  This narrows the window but cannot close it: a session that starts between
  the registry merge and the bumper's merge still gets neither copy. It also
  makes delivery depend on another repo's automation running on time.
- **Prefer the registry's own lock whenever it is attached.** This is a
  tiebreak by identity, not by fact. It would also pick the registry's lock
  when the registry is the one that is behind, for example on a branch
  checkout.
- **Compare committer dates alone.** Dates are author-controlled and are not
  ordered across diverged branches. Ancestry is the property that actually
  means "newer", and dates are used only to bound the fetch.
- **Fetch full history for every source.** This is simple, but it makes every
  session pay for history that is needed only when two locks disagree.

## References

- [ADR 0005](0005-resolve-hooks-only-from-cwd-and-user-settings.md) and
  [ADR 0007](0007-install-the-union-of-every-discovered-lock.md): the refused
  tiebreak and the union's three cases.
- `.claude/hooks/skills-bootstrap.sh`: "cross-lock destination arbitration"
  (the reader marks a contest) and "cross-lock contests" (bash resolves it).
- Tests in `scripts/test_generate_skills_lock.py`:
  `test_a_lock_behind_another_on_one_registry_installs_the_newer_pin`,
  `test_three_locks_on_one_registry_install_the_unique_newest_pin`, and the
  fail-closed tests named above.
