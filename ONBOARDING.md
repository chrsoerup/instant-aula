# instant-aula — Handoff

## What this is

Turns Aula (the Danish school parent-communication app) into two low-noise email touchpoints instead of a constant notification flood:

- **Weekly digest** — Saturdays 08:00, the upcoming week's schedule + homework as a table (day / schema / notes).
- **Must-read alerts** — every 2 hours, new messages and school-flagged important posts only (with attachments).

Fully deterministic Python — no LLM/AI in production. That was tried first (local Ollama) and deliberately removed: every job turned out to have a reliable non-AI signal (date grouping, the teacher's own text formatting, Aula's own `is_important` flag), so removing the LLM made things faster and more reliable with no loss of quality.

## Current status: live, local-only

- Repo: `github.com/chrsoerup/instant-aula` (private).
- Runs via two **Windows Scheduled Tasks** (`InstantAulaWeeklyDigest`, `InstantAulaUrgentCheck`), both Enabled. Next digest: 2026-09-05 08:00.
- Recipients: `REDACTED`, `REDACTED`.
- MitID session: authenticated and working as of 2026-08-31.
- **Read `README.md` in the repo first** for setup steps, architecture, and file-by-file details — this doc is the "why" and current-state summary, not a duplicate of that.

## Decisions worth knowing before changing anything

1. **No LLM by design** — don't reintroduce one without a concrete reason; the deterministic approach was a deliberate simplification, not a shortcut.
2. **GitHub Actions was tried and reverted.** It fixed the one real reliability gap (jobs don't run while the PC is fully powered off, e.g. weekends) but routes MitID session data and the kid's school data through whichever datacenter GitHub schedules the runner in — confirmed US-based, no EU pinning available on a free/personal plan. Ruled unacceptable for a minor's school data. **The weekend gap is an accepted trade-off, not a bug to fix by routing through cloud infrastructure.** If this is ever revisited, the only acceptable shape is self-hosted (own always-on hardware in Denmark) or an explicit EU-region VPS — never a generic hosted runner.
3. **MitID login is interactive** (`scripts/mitid_login.py`) — needed on first setup and again whenever the refresh token eventually fully expires. Two QR codes must be scanned **in sequence** (they're two halves of one verification value); scanning only one silently fails in a confusing way.
4. Windows power settings were changed to enable wake timers on battery (`powercfg` `RTCWAKE` — was previously OS-default-disabled on DC power), otherwise `WakeToRun` never actually fires when the laptop is unplugged.

## Known limitations

- Won't run while the PC is genuinely powered off — see decision #2 above.
- Un-flagged posts and routine notifications are never surfaced, even if important — there's no AI safety net for content the school forgot to flag.
- Built on an unofficial, reverse-engineered Aula client (`aula` PyPI package) — could break if Aula changes its backend.

## If something breaks

1. Both jobs email a `[Aula] <job> failed` notice with a full traceback on crash — check that first, it's usually enough.
2. Logs: `logs/weekly_digest.log`, `logs/urgent_check.log` in the repo.
3. If it's an auth failure specifically: re-run `uv run python scripts/mitid_login.py --output text -v login` locally, scan **both** QR codes, done.
4. Task state: `Get-ScheduledTask -TaskName 'InstantAulaWeeklyDigest','InstantAulaUrgentCheck'` in PowerShell.
