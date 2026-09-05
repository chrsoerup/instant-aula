# instant-aula — Handoff

## What this is

Turns Aula (the Danish school parent-communication app) into two low-noise push-notification touchpoints instead of a constant notification flood:

- **Weekly digest** — Saturdays 08:00, the upcoming week's schedule + homework as a plain-text summary.
- **Must-read alerts** — every 2 hours, new messages and school-flagged important posts only.

Fully deterministic Python — no LLM/AI in production. That was tried first (local Ollama) and deliberately removed: every job turned out to have a reliable non-AI signal (date grouping, the teacher's own text formatting, Aula's own `is_important` flag), so removing the LLM made things faster and more reliable with no loss of quality. (A narrower, optional LLM pass was later reintroduced just for the "Husk" reminder highlights in the weekly digest — see `highlights.py` — but the core deterministic approach stands.)

## Current status: mid-migration to Home Assistant, unblocked, app installation in progress

- Repo: `github.com/chrsoerup/instant-aula` — **now public** (see "Repo went public" below for why, and exactly what was scrubbed from history first).
- Repo layout changed: the actual app (`config.yaml`, `Dockerfile`, `run.sh`, `pyproject.toml`, `uv.lock`, `src/`, `scripts/`, `.env.example`) now lives in the `instant-aula/` subdirectory, with a `repository.yaml` at the repo root — required so Home Assistant Supervisor recognizes this as a valid apps repository (see "Home Assistant app installation" below for why).
- **Still actually running (live) via the old path**: Windows Scheduled Tasks + WSL, but **broken since the SMTP→HA-push code switch** — see "Known limitations" below, this is now urgent, not just "old path still active."
- **Target architecture**: a local **Home Assistant App** (formerly "add-on") on a Home Assistant Green (`192.168.87.153`, hostname `homeassistant.local`) — always on, so it'll keep running through weekends and PC shutdowns (see decision #2 below), pushing via the HA Companion app instead of email (see decision #5).
- **All code changes are done, committed, and pushed to `main`.** Note the history was rewritten once (old commit hashes like `dbab7ad`/`dcc3044` no longer exist) to scrub two personal email addresses and a committed `state/state.json` before making the repo public — if you have an old local clone with those hashes, re-clone rather than trying to reconcile.
- **Read `README.md` in the repo first** for setup steps, architecture, and file-by-file details — this doc is the "why" and current-state summary, not a duplicate of that.

## Home Assistant app installation — in progress

Setup so far, on the Green (HAOS 18.2 / Supervisor 2026.08.0 / HA Core 2026.9.0):

1. ✅ Terminal & SSH app installed and running (note: "Add-ons" was renamed "Apps" as of HA 2026.2, and "Advanced Mode" was removed entirely as of HA 2026.6 — don't look for either under those old names).
2. ❌→(worked around) Windows-to-Green Samba file copy **doesn't work from this laptop**: SMB port 445 is blocked outbound (confirmed via `Test-NetConnection -Port 445` — ICMP ping succeeds, port 445 doesn't), almost certainly a corporate policy on this managed laptop. Not needed anyway once the repository-based install (step 5 below) is used — the Samba app is optional.
3. ❌→(abandoned) Cloning the repo directly onto the device (via the Terminal & SSH web terminal) and relying on Supervisor's old `/addons/local/` auto-scan **does not work in this Supervisor version**. Confirmed via the actual Supervisor source (`gh search code` against `home-assistant/supervisor`): the local-apps path moved to a new internal constant `APPS_LOCAL = "apps/local"`, but the Terminal & SSH app (already on its latest version, 10.4.0) still only mounts the old `/addons` path — `/apps` doesn't exist in its container. This looks like a genuine gap in the still-recent (2026.2–2026.8) "Add-ons → Apps" rename, not a mistake on our end.
4. ✅ Registering the repo as a custom Apps repository (Settings > Apps > store view > **⋮ menu > Repositories**, or equivalently `ha store add <url>`) **is the correct, working mechanism** — it's how the built-in Terminal & SSH/Samba apps load. Two things had to be true for it to work:
   - The repo must be **public** — credentials embedded in the URL (`https://user:token@github.com/...`) get silently stripped by both the UI and the CLI before the clone runs, so it fails for a private repo with "could not read Username for 'https://github.com': No such device or address". This is why the repo is now public (see below).
   - The repo needs a **`repository.yaml`** at its root, with the actual app in a **subdirectory** (not at repo root) — confirmed against `home-assistant/apps-example`. Supervisor's "not a valid app repository" error on the first attempt was because `config.yaml` was sitting at repo root with no `repository.yaml`. Fixed by moving the app into `instant-aula/` and adding `repository.yaml`.
5. ⏳ Next: reload the store, confirm the "Instant Aula" card appears, install, configure Options, start, one-time MitID login — see README.md's numbered steps for the exact sequence.

## Repo went public — what was checked and scrubbed first

The user's bar was **zero exposure**, not "acceptable trade-off." Before flipping visibility:
- Checked full git history (not just current files) for anything sensitive, since a public repo exposes all of it. Found two real personal Gmail addresses (an old `ONBOARDING.md` commit) and a committed `state/state.json` (real Aula message/post IDs from actual account activity, from the brief earlier GitHub Actions experiment).
- Confirmed no actual credentials were ever committed in plaintext: the old (now-deleted) GitHub Actions workflows referenced `AULA_TOKENS_JSON`/`SMTP_PASSWORD`/etc. only via `${{ secrets.X }}` — GitHub secrets are encrypted and never retrievable via git history regardless of repo visibility.
- **Rewrote history** with `git filter-repo` (strip `state/state.json` from every commit + text-replace both email addresses) across all three branches, verified clean with fresh greps, then force-pushed. **Flagged separately**: if `AULA_TOKENS_JSON`/`SMTP_*`/`SECRETS_PAT` secrets are still configured under this repo's Settings > Secrets and variables > Actions from that old experiment, they're unused now and worth deleting as cleanup (not blocking, just hygiene — a stale MitID session token is a standing credential doing nothing useful).
- Only after confirming history was clean did the repo actually go public.
- **Caution for future sessions**: a `git filter-repo` rewrite forces a working-tree reset and changes every commit hash. If another session/clone has uncommitted work at the time, it gets silently wiped (this happened once during this migration — recovered because the other session's edit was reapplied before the force-push). Check for other active sessions/clones before doing this again.

## Decisions worth knowing before changing anything

1. **No LLM by design** — don't reintroduce one without a concrete reason; the deterministic approach was a deliberate simplification, not a shortcut.
2. **GitHub Actions was tried and reverted** early on. It fixed the one real reliability gap of the original Windows-PC setup (jobs don't run while the PC is fully powered off, e.g. weekends) but routed MitID session data and the kid's school data through whichever datacenter GitHub schedules the runner in — confirmed US-based, no EU pinning available on a free/personal plan. Ruled unacceptable for a minor's school data. **The fix in progress** (see "Home Assistant add-on installation" above) is to run the jobs as a local Home Assistant Add-on on a Home Assistant Green instead — self-hosted, always-on hardware on the home network in Denmark, which was the only acceptable shape identified at the time. Don't move this back to a generic hosted runner/cloud scheduler.
3. **MitID login is interactive** (`scripts/mitid_login.py`) — needed on first setup and again whenever the refresh token eventually fully expires. Two QR codes must be scanned **in sequence** (they're two halves of one verification value); scanning only one silently fails in a confusing way. On the add-on, this requires `docker exec`-ing into the container (see README).
4. State (`state/state.json`) and the MitID token cache are stored under the add-on's persistent `/data` volume, not the container's own filesystem — otherwise an add-on rebuild/update would wipe them.
5. **Single delivery channel (Home Assistant push) by explicit choice**, not an oversight — SMTP/email support was removed entirely rather than kept as a fallback. If Home Assistant is down, alerts and failure notices alike have nowhere to go except the add-on's own logs.

## Known limitations

- Single delivery channel — see decision #5.
- Un-flagged posts and routine notifications are never surfaced, even if important — there's no AI safety net for content the school forgot to flag.
- Built on an unofficial, reverse-engineered Aula client (`aula` PyPI package) — could break if Aula changes its backend.

## If something breaks

Note: since the Home Assistant migration isn't live yet (see "Current status" above), the *currently running* setup is still the old Windows Scheduled Tasks + email one — see README.md's git history for how that worked (Task Scheduler, `logs/*.log`, SMTP). The steps below describe the **target** Home Assistant setup, for once it's actually installed and running:

1. Both jobs push a `[Aula] <job> failed` notice with a full traceback on crash via Home Assistant — check that first, it's usually enough.
2. Logs: the add-on's **Log** tab in Home Assistant (Settings > Apps > Instant Aula).
3. If it's an auth failure specifically: `docker exec -it addon_local_instant_aula bash`, then `cd /app && uv run python scripts/mitid_login.py --output text -v login`, scan **both** QR codes, done.
4. Add-on state: Settings > Apps > Instant Aula — Start/Stop/Restart and Configuration are all there.
