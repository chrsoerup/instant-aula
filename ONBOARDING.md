# instant-aula — Handoff

## What this is

Turns Aula (the Danish school parent-communication app) into two low-noise push-notification touchpoints instead of a constant notification flood:

- **Weekly digest** — Saturdays 08:00, the upcoming week's schedule + homework as a plain-text summary.
- **Must-read alerts** — every 2 hours, new messages and school-flagged important posts only.

Fully deterministic Python — no LLM/AI in production. That was tried first (local Ollama) and deliberately removed: every job turned out to have a reliable non-AI signal (date grouping, the teacher's own text formatting, Aula's own `is_important` flag), so removing the LLM made things faster and more reliable with no loss of quality. (A narrower, optional LLM pass was later reintroduced just for the "Husk" reminder highlights in the weekly digest — see `highlights.py` — but the core deterministic approach stands.)

## Current status: live, self-hosted on Home Assistant

- Repo: `github.com/chrsoerup/instant-aula` (private).
- Runs as a local **Home Assistant Add-on** on a Home Assistant Green — always on, so it keeps running through weekends and PC shutdowns (see decision #2 below; this used to run via Windows Scheduled Tasks + WSL, which didn't).
- Delivery: Home Assistant Companion-app push notifications (no email — see decision #5).
- **Read `README.md` in the repo first** for setup steps, architecture, and file-by-file details — this doc is the "why" and current-state summary, not a duplicate of that.

## Decisions worth knowing before changing anything

1. **No LLM by design** — don't reintroduce one without a concrete reason; the deterministic approach was a deliberate simplification, not a shortcut.
2. **GitHub Actions was tried and reverted** early on. It fixed the one real reliability gap of the original Windows-PC setup (jobs don't run while the PC is fully powered off, e.g. weekends) but routed MitID session data and the kid's school data through whichever datacenter GitHub schedules the runner in — confirmed US-based, no EU pinning available on a free/personal plan. Ruled unacceptable for a minor's school data. **This is now resolved properly**: the jobs run as a local Home Assistant Add-on on a Home Assistant Green — self-hosted, always-on hardware on the home network in Denmark, which was the only acceptable shape identified at the time. Don't move this back to a generic hosted runner/cloud scheduler.
3. **MitID login is interactive** (`scripts/mitid_login.py`) — needed on first setup and again whenever the refresh token eventually fully expires. Two QR codes must be scanned **in sequence** (they're two halves of one verification value); scanning only one silently fails in a confusing way. On the add-on, this requires `docker exec`-ing into the container (see README).
4. State (`state/state.json`) and the MitID token cache are stored under the add-on's persistent `/data` volume, not the container's own filesystem — otherwise an add-on rebuild/update would wipe them.
5. **Single delivery channel (Home Assistant push) by explicit choice**, not an oversight — SMTP/email support was removed entirely rather than kept as a fallback. If Home Assistant is down, alerts and failure notices alike have nowhere to go except the add-on's own logs.

## Known limitations

- Single delivery channel — see decision #5.
- Un-flagged posts and routine notifications are never surfaced, even if important — there's no AI safety net for content the school forgot to flag.
- Built on an unofficial, reverse-engineered Aula client (`aula` PyPI package) — could break if Aula changes its backend.

## If something breaks

1. Both jobs push a `[Aula] <job> failed` notice with a full traceback on crash via Home Assistant — check that first, it's usually enough.
2. Logs: the add-on's **Log** tab in Home Assistant (Settings > Add-ons > Instant Aula).
3. If it's an auth failure specifically: `docker exec -it addon_local_instant_aula bash`, then `cd /app && uv run python scripts/mitid_login.py --output text -v login`, scan **both** QR codes, done.
4. Add-on state: Settings > Add-ons > Instant Aula — Start/Stop/Restart and Configuration are all there.
