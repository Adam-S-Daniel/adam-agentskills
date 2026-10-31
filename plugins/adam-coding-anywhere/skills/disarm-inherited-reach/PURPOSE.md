# Purpose — disarm-inherited-reach

Maintenance context only; never loaded at inference.

## What it is for

Severs a scratch tree's inherited push path to the real repository before
anything runs in it, and warns that a worktree must never be disarmed because it
shares the parent's `.git`.

## Changes

- Prompt-audit fix (adam-agentskills issue #56): the "When to invoke" bullet for
  `git clone` / `git worktree add` now scopes to scratch or throwaway trees. The
  fleet guidance pre-authorizes one worktree per coding session that the session
  pushes from on purpose, so the skill must not tell the agent to stop using it.
  Step 3 ("Worktree — do not disarm it") and the negative-control and
  destructive-path bullets are unchanged and still apply to every tree. The
  frontmatter description was left as is.
