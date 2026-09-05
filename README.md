# instant-aula

Turns Aula's flood of notifications into two things:

- **A weekly digest** — one push notification, once a week, with the school week (Meebook weekplan + calendar) as a day-by-day plain-text summary.
- **Must-read alerts** — new messages and school-flagged important posts are pushed as soon as they show up; everything else (routine notifications, un-flagged posts) is left alone.

Both are mostly deterministic Python — the source data (Meebook's weekplan text, Aula's own `is_important` flag on posts, and the fact that messages are personally addressed by a teacher rather than broadcast) already carries most of the signal needed, with no summarization required for grouping/formatting. The one exception: the weekly digest also runs the week's notes through a local Ollama model to surface a short "Husk" (remember) list of parent-actionable items — bring gym clothes, bring the "læsemappe", a permission slip due, etc. — a genuine judgment call rather than a formatting job. See `highlights.py`. It's optional and fails soft: if Ollama isn't running, the digest still sends, just without that section.

Delivery is via Home Assistant push notifications (Companion app), running as a local Home Assistant Add-on on an always-on device — see "Running as a Home Assistant Add-on" below.

Built on the community-maintained, unofficial [`aula`](https://github.com/nickknissen/aula) CLI (Aula has no official API). Auth is via MitID — treat this like giving a script your login.

## Local development setup (optional)

For real scheduled runs, skip to "Running as a Home Assistant Add-on" below — this section is only for iterating on the Python code itself from a dev shell.

1. **Install [uv](https://docs.astral.sh/uv/)** if you don't have it. `uv` will provision the required Python 3.14 automatically — no manual interpreter install needed. The app itself lives in the `instant-aula/` subdirectory (a repository can contain multiple Home Assistant apps, each in its own folder) — `cd instant-aula` before running any of the commands below.

2. **Configure:**
   ```bash
   cp .env.example .env
   ```
   Fill in `AULA_MITID_USERNAME`.

3. **Install dependencies:**
   ```bash
   uv sync
   ```

4. **First login (interactive)** — the `aula` CLI's own QR renderer is hard to scan in most terminals; use the block-character variant instead:
   ```bash
   uv run python scripts/mitid_login.py --output text -v login
   ```
   Two QR codes are shown in sequence — scan QR 1, then QR 2, with the MitID app. Tokens are then cached at `~/.config/aula/tokens.json` and refreshed automatically — subsequent runs shouldn't need another interactive login (until the refresh token itself eventually expires, at which point you'll need to re-approve once, the same way).

5. **Baseline the must-read state** so it doesn't alert on your entire existing backlog once deployed:
   ```bash
   uv run python -m instant_aula.urgent_check
   ```
   The first run only records current messages/posts to `state/state.json`; it won't send any alerts (and won't attempt to notify, so it works without `SUPERVISOR_TOKEN`). Run it a second time to confirm it's now a no-op ("No new must-read items.").

   Note: `uv run python -m instant_aula.weekly_digest` will fetch and print the digest fine locally, but fails at the final `notify(...)` call — that only works with a `SUPERVISOR_TOKEN`, which exists only inside the Home Assistant add-on container.

## Running as a Home Assistant Add-on

Scheduling and delivery both run inside a local Home Assistant Add-on — a Docker container that Home Assistant's own Supervisor builds and runs directly on the Home Assistant device (e.g. a Home Assistant Green), alongside HA Core itself. That device is always on, so this keeps running through weekends and PC shutdowns — unlike the earlier Windows Scheduled Tasks setup this replaced (see "Known limitations" for why a cloud scheduler wasn't used to solve that instead).

The app lives in the `instant-aula/` subdirectory, alongside a repo-root `repository.yaml` (Home Assistant apps repositories can contain multiple apps, each in its own subdirectory with a `repository.yaml` describing the repo itself): `config.yaml` (app manifest + configurable options), `Dockerfile` (builds the image — plain `debian:bookworm-slim`, no browser/Playwright needed since `aula` is a plain HTTP client), and `run.sh` (installs a cron schedule inside the container — Saturdays 08:00 for the digest, every 2 hours for the urgent check — and reads app Options into the cron jobs' environment). State (`state/state.json`) and the MitID token cache (normally `~/.config/aula/tokens.json`) are pointed at the app's persistent `/data` volume so they survive rebuilds/updates.

Notifications go out via Home Assistant's own Core API (`http://supervisor/core/api/services/notify/<service>`), authenticated with the add-on's auto-injected `SUPERVISOR_TOKEN` — no manually-created long-lived access token needed, thanks to `homeassistant_api: true` in `config.yaml`.

Two Home Assistant terminology/UI changes to know going in: **"Add-ons" was renamed "Apps"** (as of HA 2026.2), and **"Advanced Mode" was removed entirely** (as of HA 2026.6, along with the need for it) — ignore any older instructions that mention either by their old name.

**Setup, in order:**

1. Settings > Apps > App Store: install **Terminal & SSH** (for the one-time MitID login and troubleshooting; also usable to transfer files if your network blocks SMB — see note below).
2. Settings > Apps > (store view) > **⋮ menu > Repositories** > add `https://github.com/chrsoerup/instant-aula`. Supervisor clones it directly — this only works because the repo is public; a private repo can't authenticate through this flow (both the UI and `ha store add` strip any credentials embedded in the URL).
3. Reload the store (⋮ menu, or `ha store reload` from a terminal) — an "Instant Aula" card should now appear.
4. Click it, then **Install**. Building the image takes a few minutes the first time (installing `uv`, syncing Python dependencies).
5. On the **Configuration** tab, fill in `aula_mitid_username`, `aula_auth_method`, and `ha_notify_service` (the paired phone's notify service — find it under Settings > Devices & Services > your phone, or Developer Tools > Actions, search "notify").
6. **Start** the app.
7. One-time interactive MitID login: open the Terminal & SSH app's **web terminal** (Info tab > "Open Web UI" — works even if outbound SMB/SSH ports are firewalled, since it rides over the same HTTPS connection as the dashboard), run `docker exec -it addon_local_instant_aula bash`, then inside the container `cd /app && uv run python scripts/mitid_login.py --output text -v login`. Scan **both** QR codes shown, in sequence, with the MitID app. Tokens are then cached under `/data/home/.config/aula/` and survive future rebuilds/updates.
8. Check the app's **Log** tab for `instant-aula add-on started` with no `jq`/cron errors. To test immediately without waiting for the schedule, `docker exec` in again and run `uv run python -m instant_aula.weekly_digest` (or `.urgent_check`) directly — confirm a push notification arrives and the log shows it sent successfully.
9. Once confirmed working, disable/delete the old `InstantAulaWeeklyDigest` / `InstantAulaUrgentCheck` Windows Scheduled Tasks (PowerShell: `Unregister-ScheduledTask`, or Task Scheduler GUI) — leaving both active alongside the app would double-run and race on `state.json`. Note: these tasks run the *current* code directly from this working copy, not a separate deployment — once this repo's code moved to Home Assistant-only delivery, the old tasks stopped being able to notify at all (they need `SUPERVISOR_TOKEN`, which only exists inside the app's container), so there's likely already a gap in coverage between that change and finishing this setup.

**If SMB (Samba) is blocked on your network** (some corporate-managed laptops block outbound port 445 as policy — check with `Test-NetConnection -ComputerName <device-ip> -Port 445` from PowerShell), skip the Samba app entirely; the repository-based install above never needs it.

**A cloud-hosted scheduler (GitHub Actions) was tried earlier and deliberately reverted**, before this app existed. It solved the weekend-off problem — runners are always on regardless of the local machine's state — but routed the MitID session and the kid's school data through whichever datacenter GitHub happened to schedule the runner in (confirmed US-based, not EU, with no way to pin the region on a personal/free plan). That's not an acceptable trade-off for a minor's school data. Running on self-hosted, always-on hardware on the home network (the Home Assistant device) gets the same "always on" property without that trade-off.

## How it works

- `aula_cli.py` shells out to the `aula` CLI with `--output json` rather than importing its internals directly, since those are explicitly called out as subject to change.
- `weekly_digest.py` calls `aula weekly-summary --provider meebook`, groups calendar events and Meebook weekplan notes by date in Python (parsing both the ISO calendar timestamps and Meebook's Danish day labels like "mandag 17. aug."), splits weekplan text on the teacher's own `___` section breaks into bullets, and renders a plain-text summary — without touching an LLM, so the teacher's original wording is preserved exactly. It then separately calls `highlights.py`, which sends that same grouped data to a local Ollama model (`llama3.1:8b` by default) asking it to pick out only concrete parent action items, and prepends the result as a "Husk" section.
- `urgent_check.py` calls `aula messages --unread` and `aula posts`; every new unread message is forwarded as-is, and posts are alerted only when Aula's own `is_important` flag is set. Post attachment counts are noted in the push notification text (not downloaded — a notification can't carry file contents; check Aula directly for the file).
- Both scripts are safe to re-run: `state/state.json` ensures items are never re-alerted once seen.
- `ha_notify.py`: pushes notifications via Home Assistant's Core API, using the add-on's auto-injected `SUPERVISOR_TOKEN`. This is the sole delivery channel (no email fallback) — see "Running as a Home Assistant Add-on" above.
- `notify_failure.py`: if either script crashes for any reason (including MitID auth expiring), it pushes a `[Aula] <job> failed` notice with the traceback via the same Home Assistant path — so a broken scheduled run surfaces immediately instead of "I haven't gotten a digest in three weeks." If Home Assistant itself is unreachable, this has nowhere left to go; check the add-on's Log tab in that case.

## Known limitations

- **Single delivery channel by design**: if Home Assistant is down or unreachable, both the digest/alerts and the failure notice about a crash have nowhere to go — visible only in the add-on's own Log tab. This was an explicit trade-off in exchange for not maintaining a second (email) channel.
- MitID auth is interactive on first login and whenever the refresh token expires — this can't be made fully unattended, and requires `docker exec`-ing into the add-on's container to redo (see step 7 above).
- This relies on an unofficial, reverse-engineered API; if Aula changes its backend, `aula` CLI commands may break until the upstream project catches up.
- Un-flagged posts and notifications (photo uploads, presence changes, etc.) are never surfaced, even if genuinely important — there's no LLM safety net for content the school forgot to mark important. If that turns out to be a real gap in practice, an LLM-based fallback (Ollama is already installed) could be reintroduced for that narrower case.
- The MitID app-login QR flow requires scanning **both** QR codes shown, in sequence (they encode two halves of one verification value) — easy to miss, and the failure mode if you only scan one isn't an obvious error message.
