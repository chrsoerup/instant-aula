# instant-aula — Handoff

## What this is

Turns Aula (the Danish school parent-communication app) into two low-noise push-notification touchpoints instead of a constant notification flood:

- **Weekly digest** — Saturdays 08:00, the upcoming week's schedule + homework as a plain-text summary.
- **Must-read alerts** — every 2 hours, new messages and school-flagged important posts only.

Fully deterministic Python — no LLM/AI in production. That was tried first (local Ollama) and deliberately removed: every job turned out to have a reliable non-AI signal (date grouping, the teacher's own text formatting, Aula's own `is_important` flag), so removing the LLM made things faster and more reliable with no loss of quality. (A narrower, optional LLM pass was later reintroduced just for the "Husk" reminder highlights in the weekly digest — see `highlights.py` — but the core deterministic approach stands.)

## Current status: mid-migration to Home Assistant, BLOCKED on a decision

- Repo: `github.com/chrsoerup/instant-aula` (private).
- **Still actually running (live) via the old path**: Windows Scheduled Tasks + WSL. Do not disable/delete `InstantAulaWeeklyDigest` / `InstantAulaUrgentCheck` yet — the Home Assistant add-on isn't installed and running yet, so disabling the old path first would stop delivery entirely.
- **Target architecture** (code is done, add-on isn't installed yet): a local **Home Assistant Add-on** on a Home Assistant Green (`192.168.87.153`, hostname `homeassistant.local`) — always on, so it'll keep running through weekends and PC shutdowns (see decision #2 below), pushing via the HA Companion app instead of email (see decision #5).
- **All code changes are done, committed, and pushed to `main`**: `config.yaml`, `Dockerfile`, `run.sh` (repo root), `src/instant_aula/ha_notify.py` (new), plus edits to `config.py`/`weekly_digest.py`/`urgent_check.py`/`notify_failure.py`, `emailer.py` deleted. See git log around commit `dbab7ad`.
- **What's actually blocking progress**: Home Assistant Supervisor won't discover the add-on. Full detail and the pending decision are in "Home Assistant add-on installation — blocked" below.
- **Read `README.md` in the repo first** for setup steps, architecture, and file-by-file details — this doc is the "why" and current-state summary, not a duplicate of that.

## Home Assistant add-on installation — blocked

Setup so far, on the Green (HAOS 18.2 / Supervisor 2026.08.0 / HA Core 2026.9.0):

1. ✅ Samba share and Terminal & SSH apps installed and running (note: "Add-ons" was renamed "Apps" as of HA 2026.2, and "Advanced Mode" was removed entirely as of HA 2026.6 — don't look for either under those old names).
2. ✅ Windows-to-Green Samba file copy **doesn't work from this laptop**: SMB port 445 is blocked outbound (confirmed via `Test-NetConnection -Port 445` — ICMP ping succeeds, port 445 doesn't), almost certainly a corporate policy on this managed laptop. Worked around entirely via the Terminal & SSH add-on's browser-based web terminal (Info tab > "Open Web UI") instead, which rides over the existing HTTPS connection to HA and needs no extra firewall exceptions.
3. ✅ Cloned the repo directly from GitHub onto the Green via that web terminal (`git clone` with a GitHub fine-grained personal access token as the password). Note: had to set the token's **Contents permission to "Read and write"**, not read-only — GitHub's fine-grained tokens throw a misleading "write access not granted" error on a plain `clone` if only read is granted.
4. ❌ **The clone landed at `/addons/local/instant-aula`, which Supervisor no longer scans.** Confirmed via the actual Supervisor source (`gh search code` against `home-assistant/supervisor`): the local-apps path moved to a new internal constant `APPS_LOCAL = "apps/local"`, but the Samba/Terminal & SSH add-ons (already on their latest versions, 12.10.0 / 10.4.0) still only mount the old `/addons` path — `/apps` doesn't exist in either container. This looks like a genuine gap in the still-recent (2026.2–2026.8) "Add-ons → Apps" rename, not a mistake on our end. `ha apps reload` / `ha store reload` / `ha apps info local_instant_aula` all confirm Supervisor has no knowledge of it.
5. ❌ Tried registering the repo as a custom Apps repository instead (Settings > Apps > Repositories, and equivalently `ha store add <url>`) — this is the mechanism that still demonstrably works (it's how the built-in Samba/SSH apps load). But it only accepts a plain git URL; credentials embedded in the URL (`https://user:token@github.com/...`) get silently stripped by both the UI and the CLI before the clone runs, so it fails for a **private** repo specifically with "could not read Username for 'https://github.com': No such device or address".

**Decision needed to unblock, presented to the user, awaiting an answer:**
- **Option A (recommended, simplest):** make the `instant-aula` GitHub repo public, then redo step 5 above (Settings > Apps > Repositories > add `https://github.com/chrsoerup/instant-aula`, no credentials needed for a public repo) — this uses the exact same on-device Docker build (`config.yaml`/`Dockerfile`/`run.sh` at repo root) already committed, no code changes needed. No secrets are committed anywhere in the repo (SMTP was removed; MitID/HA credentials only ever come from `.env` or the add-on's own Options UI, both gitignored/runtime-only) — the trade-off is that the automation source code and this handoff doc's narrative become world-readable.
- **Option B:** keep the repo private, instead build a container image locally and push it to a registry (e.g. GHCR), then point `config.yaml`'s (currently absent) `image:` key at it instead of building on-device. More setup work (needs a multi-arch build, e.g. `docker buildx --platform linux/arm64`), and if that image is made public instead, the source is still extractable from its layers anyway — so this only meaningfully preserves privacy if paired with a private, authenticated registry pull, which adds further complexity Supervisor doesn't have obvious first-class UI for.

**Once unblocked**, remaining steps are exactly steps 4–9 in README.md's "Running as a Home Assistant Add-on" section: install the app, fill in Options (`aula_mitid_username`, `aula_auth_method`, `ha_notify_service`), start it, do the one-time interactive MitID login via `docker exec` (or the web terminal), verify a push notification arrives, *then* disable the Windows Scheduled Tasks.

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
