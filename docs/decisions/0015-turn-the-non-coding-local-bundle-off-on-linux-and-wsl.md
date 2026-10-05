# 0015. Turn the non-coding local bundle off on Linux and WSL, leave it on in Windows

- **Status:** Accepted
- **Date:** 2026-10-05
- **Deciders:** Adam Daniel

## Context

`adam-non-coding-local` carries seven skills (add-from-address,
add-received-from-addresses, fastmail, ocr-pdfs, pdf-ocr-audit, rename-pdfs,
compare-pdfpairs). Their work is Windows, browser and document workflows: a WPF
review tool, a signed-in Fastmail browser session, PDF folders on a Windows
drive. [ADR 0014](0014-retire-the-account-zip-upload-channel.md) put the bundle
on the claude.ai account, so every terminal signed in with it downloads it as
`adam-non-coding-local@synced`; the same bundle can also be installed from this
marketplace as `adam-non-coding-local@adam-agentskills`.

`/skill-doctor` on the owner's WSL home (2026-10-05) measured the bundle at
about 1.3k tokens of always-on skill descriptions per turn, with zero uses in
the previous 7 days. `setup.sh --owner-machine` had said nothing about it
([ADR 0013](0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md)
left it "for the operator to enable"), so on WSL it kept arriving through the
account channel, and `syncClaudeAiSkills: false` does not stop it: that key
covers skills, not plugins.

## Decision

The bundle's default is per OS, and `setup.sh --owner-machine` writes it:

- **Windows:** on. `setup.sh` writes nothing for it and never turns off a copy
  it finds, whichever key it arrived under.
- **Linux, WSL included:** off. `setup.sh` writes `false` for both
  `adam-non-coding-local@synced` and `adam-non-coding-local@adam-agentskills`
  (disabling one source leaves the other loading) and installs nothing.
- **macOS:** untouched. Nothing was measured there, so no decision is made.

The OS comes from `sys.platform` of the interpreter that runs the convergence
block; `AGENTSKILLS_HOST_OS` (`windows`, `macos` or `linux`) overrides it and
exists so the tests do not depend on the machine running them.

## Consequences

- A new WSL or Linux machine of the owner's needs no manual `claude plugin
  disable`; a new Windows machine needs nothing either.
- Like the `@synced` lines for the `-anything-anywhere` plugins, `false` is
  written on every run, so a manual `claude plugin enable` on a Linux machine
  lasts only until `setup.sh --owner-machine` next runs. There is no per-plugin
  override flag: the script has no per-plugin flags, and the one case that
  would want one (using the bundle on Linux) is not a case the owner has.
- The 1.3k-token figure is one measurement of one machine on one date. If the
  skills gain a Linux use, change this table, not the measurement.
- A Windows home that also runs the owner's `-anything-anywhere` plugins is not
  affected; those keep their own lines.

## Alternatives considered

- **Disable by hand on each machine.** That is what was done on the first
  machine, and it is lost on the next one. Rejected: nothing records it.
- **Write `false` on every OS.** Takes the bundle away from the one home that
  uses it. Rejected.
- **`syncClaudeAiPlugins: false`.** Turns off every account plugin in every
  terminal, Anthropic's included ([ADR 0013](0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md)).
  Rejected for the same reason as before.

## How to verify

`scripts/test_setup_settings_convergence.py`: the `non_coding` tests run the
shipped convergence block once per OS and assert on the parsed JSON.
