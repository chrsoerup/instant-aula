# instant-aula

Turns Aula's flood of notifications into two things:

- **A weekly digest** — one push notification, once a week, with the school week (Meebook weekplan + calendar) as a day-by-day plain-text summary.
- **Must-read alerts** — new messages and school-flagged important posts are pushed as soon as they show up; everything else (routine notifications, un-flagged posts) is left alone.

Both are mostly deterministic Python — the source data (Meebook's weekplan text, Aula's own `is_important` flag on posts, and the fact that messages are personally addressed by a teacher rather than broadcast) already carries most of the signal needed, with no summarization required for grouping/formatting. No LLM is involved: the digest is the teachers' own wording, grouped by day and subject. An Ollama pass that prepended a "Husk" (remember) list was tried and removed — see "Why there's no LLM summary" below.

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

4. **First login (interactive)** — the `aula` CLI's own QR renderer is hard to scan in most terminals; use the image variant instead:
   ```bash
   uv run python scripts/mitid_login.py --output text -v login
   ```
   It writes `instant_aula_mitid.html` next to the project and prints the path — open that in a browser and scan **both** QR codes on it with the MitID app. The page refreshes itself; don't reload it, and don't scan from a saved copy of the image (MitID rotates the value behind the codes roughly once a second, and both codes have to come from the same rotation). Tokens are then cached at `~/.config/aula/tokens.json` and refreshed automatically — subsequent runs shouldn't need another interactive login (until the refresh token itself eventually expires, at which point you'll need to re-approve once, the same way).

   Doing this first login on a dev machine and then copying `~/.config/aula/tokens.json` into the add-on's `/data/home/.config/aula/` is a legitimate shortcut: the tokens are all the add-on needs, and it avoids spending MitID login attempts (which are rate-limited, and lock the account out after repeated failures) on a container that's harder to iterate in.

5. **Baseline the must-read state** so it doesn't alert on your entire existing backlog once deployed:
   ```bash
   uv run python -m instant_aula.urgent_check
   ```
   The first run only records current messages/posts to `state/state.json`; it won't send any alerts (and won't attempt to notify, so it works without `SUPERVISOR_TOKEN`). Run it a second time to confirm it's now a no-op ("No new must-read items.").

   Note: `uv run python -m instant_aula.weekly_digest` will fetch and print the digest fine locally, but fails at the final `notify(...)` call — that only works with a `SUPERVISOR_TOKEN`, which exists only inside the Home Assistant add-on container.

## Running as a Home Assistant Add-on

Scheduling and delivery both run inside a local Home Assistant Add-on — a Docker container that Home Assistant's own Supervisor builds and runs directly on the Home Assistant device (e.g. a Home Assistant Green), alongside HA Core itself. That device is always on, so this keeps running through weekends and PC shutdowns — unlike the earlier Windows Scheduled Tasks setup this replaced (see "Known limitations" for why a cloud scheduler wasn't used to solve that instead).

The app lives in the `instant-aula/` subdirectory, alongside a repo-root `repository.yaml` (Home Assistant apps repositories can contain multiple apps, each in its own subdirectory with a `repository.yaml` describing the repo itself): `config.yaml` (app manifest + configurable options), `Dockerfile` (builds the image — plain `debian:bookworm-slim`, no browser/Playwright needed since `aula` is a plain HTTP client), and `run.sh` (installs a cron schedule inside the container — Mondays 06:00 for the digest, every 2 hours for the urgent check — and reads app Options into the cron jobs' environment). State (`state/state.json`) and the MitID token cache (normally `~/.config/aula/tokens.json`) are pointed at the app's persistent `/data` volume so they survive rebuilds/updates.

Notifications go out via Home Assistant's own Core API (`http://supervisor/core/api/services/notify/<service>`), authenticated with the add-on's auto-injected `SUPERVISOR_TOKEN` — no manually-created long-lived access token needed, thanks to `homeassistant_api: true` in `config.yaml`.

Two Home Assistant terminology/UI changes to know going in: **"Add-ons" was renamed "Apps"** (as of HA 2026.2), and **"Advanced Mode" was removed entirely** (as of HA 2026.6, along with the need for it) — ignore any older instructions that mention either by their old name.

**Setup, in order:**

1. Settings > Apps > App Store: install **Terminal & SSH** (for the one-time MitID login and troubleshooting; also usable to transfer files if your network blocks SMB — see note below).
2. Settings > Apps > (store view) > **⋮ menu > Repositories** > add `https://github.com/chrsoerup/instant-aula`. Supervisor clones it directly — this only works because the repo is public; a private repo can't authenticate through this flow (both the UI and `ha store add` strip any credentials embedded in the URL).
3. Reload the store (⋮ menu, or `ha store reload` from a terminal) — an "Instant Aula" card should now appear.
4. Click it, then **Install**. Building the image takes a few minutes the first time (installing `uv`, syncing Python dependencies).
5. On the **Configuration** tab, fill in `aula_mitid_username`, `aula_auth_method`, and `ha_notify_service`. The last one is the notify service name **without** the `notify.` prefix — find it under Developer Tools > Actions, search "notify"; the paired phone's looks like `mobile_app_<phone>`. A leading `notify.` is stripped if you paste it anyway.

   It accepts a comma-separated list, and `mobile_app_<phone>,persistent_notification` is the recommended value. The phone push is the only thing that reaches you away from home, but iOS shows a 3500-character digest as a two-line preview and the Companion app has no inbox to open it in; `persistent_notification` puts the same text in Home Assistant's own Notifications panel, where it renders in full and stays until dismissed. Delivery is attempted per target, so a typo in one costs a log line rather than the message.
6. **Start** the app.
7. **Get a MitID token into the app.** Two ways, and the first is strongly preferred:

   **a. Log in on a PC and hand the app the token** (no MitID attempt spent inside the container, no `docker` needed). Follow step 4 of local setup to produce `~/.config/aula/tokens.json`, then get that file into Home Assistant's config directory as `instant_aula_tokens.json` — via the Terminal & SSH app's **web terminal** (Info tab > "Open Web UI" — works even if outbound SMB/SSH ports are firewalled, since it rides over the same HTTPS connection as the dashboard):

   ```bash
   # in WSL — note: no -w0, the terminal truncates any single line over 4 KB
   base64 ~/.config/aula/tokens.json | clip.exe
   ```
   ```bash
   # in the HA web terminal: paste, then Enter, then Ctrl+D
   cat > /config/instant_aula_tokens.b64
   tr -d '\r' < /config/instant_aula_tokens.b64 | base64 -d > /config/instant_aula_tokens.json
   rm /config/instant_aula_tokens.b64
   ```

   Put it in `/config`, **not** `/config/www` — anything under `www/` is served to the network. Then **restart the app**: `run.sh` moves the file into `/data/home/.config/aula/tokens.json` (mode 600) and deletes the staged copy, logging `Imported MitID tokens from ...`. Tokens then survive future rebuilds/updates. This is also the re-auth path when the refresh token eventually expires.

   **b. Interactive login inside the container.** Needs `docker exec`, which needs Protection mode off on the Terminal & SSH app — and recent Home Assistant has removed the UI toggle for that (it's not in the Info tab or the ⋮ menu, and `ha addons security` has no `--protected` flag), leaving only a raw Supervisor API call. Assume this route is unavailable and use (a). If you do get a shell in: `cd /app && uv run python scripts/mitid_login.py --output text -v login`, which writes a QR page into HA's `www/` folder and prints its URL (`http://homeassistant.local:8123/local/instant_aula_mitid.html`) — open that on a computer, not on the phone doing the scanning, and scan **both** QR codes on the page. The page refreshes itself; don't reload it.
8. Check the app's **Log** tab for the cron schedule being installed with no `jq` errors, and for `Imported MitID tokens` if you came via 7a. To confirm delivery without waiting for the schedule, set **`run_now`** on the Configuration tab to `urgent`, `digest` or `both`, save, and restart: the chosen job runs once at startup, before cron is installed, and logs `run_now: <job> finished` or a traceback. Set it back to `none` afterwards, or it repeats on every restart. (This option exists because without a container shell — see 7b — there's otherwise no way to trigger a run on demand.)

   Note that `urgent_check`'s *first* run only baselines the current state and deliberately sends nothing; run it twice to see real behaviour. `digest` sends unconditionally, so it's the better one-shot delivery test.
9. Once confirmed working, disable/delete the old `InstantAulaWeeklyDigest` / `InstantAulaUrgentCheck` Windows Scheduled Tasks (PowerShell: `Unregister-ScheduledTask`, or Task Scheduler GUI) — leaving both active alongside the app would double-run and race on `state.json`. Note: these tasks run the *current* code directly from this working copy, not a separate deployment — once this repo's code moved to Home Assistant-only delivery, the old tasks stopped being able to notify at all (they need `SUPERVISOR_TOKEN`, which only exists inside the app's container), so there's likely already a gap in coverage between that change and finishing this setup.

**If SMB (Samba) is blocked on your network** (some corporate-managed laptops block outbound port 445 as policy — check with `Test-NetConnection -ComputerName <device-ip> -Port 445` from PowerShell), skip the Samba app entirely; the repository-based install above never needs it.

**A cloud-hosted scheduler (GitHub Actions) was tried earlier and deliberately reverted**, before this app existed. It solved the weekend-off problem — runners are always on regardless of the local machine's state — but routed the MitID session and the kid's school data through whichever datacenter GitHub happened to schedule the runner in (confirmed US-based, not EU, with no way to pin the region on a personal/free plan). That's not an acceptable trade-off for a minor's school data. Running on self-hosted, always-on hardware on the home network (the Home Assistant device) gets the same "always on" property without that trade-off.

## How it works

- `aula_cli.py` shells out to the `aula` CLI with `--output json` rather than importing its internals directly, since those are explicitly called out as subject to change.
- `weekly_digest.py` calls `aula weekly-summary --provider meebook`, groups calendar events and Meebook weekplan notes by date in Python (parsing both the ISO calendar timestamps and Meebook's Danish day labels like "mandag 17. aug."), splits weekplan text on the teacher's own `___` section breaks into bullets, and renders a plain-text summary — without touching an LLM, so the teacher's original wording is preserved exactly. Notes are grouped by day and then by subject, so the subject appears once as a heading (`Dansk:`) rather than as a `[Dansk]` prefix on every line. Calendar events are merged per time slot, because Aula returns one event per *staff assignment* rather than per lesson — a Danish lesson with a resource teacher present arrives as two events with different ids on the same slot (`DAN`/Mette + `RES`/Katrine), and a PE lesson with two teachers as two `IDR` events. Printing one line each made 40 lines describe 26 periods and read as though the slot held two separate lessons; merged, it's `Kl. 09.50-10.35: DAN (Mette Bondesen) + RES (Katrine Rafn Hochreuter)`, with same-titled events collapsing to one entry listing both teachers. The child's name joins that heading only when more than one child has notes that week — with one child it's noise, with two, merging both into a shared `Dansk:` heading would attribute one child's homework to the other.

  It fetches the **current** week, and runs Monday 06:00 for that reason. Fetching next week instead (which a weekend look-ahead would need) reliably returns a timetable with no notes: Aula publishes the calendar a week ahead, but teachers fill in their Meebook weekplan for the week they're in, often referring forward from it ("vi fortsætter i næste uge med…") rather than writing the next week out. Measured 2026-09-14: current week 7 tasks, next week 0, with 40 calendar events either way.

### Why there's no LLM summary

`highlights.py` asked a local Ollama model to reduce the week's notes to a short "Husk" list of parent-actionable items. It's still in the tree but no longer called, because it was measured rather than assumed.

Benchmarked on one real week (2026-09-14), `llama3.1:8b` run twice on identical input:

| | run 1 (146s) | run 2 (94s) |
|---|---|---|
| items returned | 5 | 8 |
| `HUSK LÆSEMAPPE!!` | ✓ | ✓ |
| maths homework (`Side 10-11 i bogen skal laves`) | **missed** | ✓ |
| `Medbring madpakke til trivsel og leg` | — | **invented** |

The madpakke errand appears nowhere in that week's data; the model lifted it from the worked example inside its own prompt. So one run silently dropped real homework and the other manufactured an errand — and if the digest is reduced to that list, both failures are invisible to the reader.

Hardware compounds it: the digest runs on a Home Assistant Green (4 GB RAM, quad-core A55), which cannot load an 8B model at all. Anything that fits there is weaker than the model that produced the table above.

A rules-based extractor (match `husk`/`medbring`/`aflever`/`skal laves`, quote the teacher verbatim) was also prototyped and found every real item, missing only paraphrases like "giv **gerne** besked". If a short list is wanted later, that's the safer basis — it can only quote, never invent. For now the digest sends the notes in full.
- `urgent_check.py` calls `aula messages --unread` and `aula posts`; every new unread message is forwarded as-is, and posts are alerted only when Aula's own `is_important` flag is set. Post attachment counts are noted in the push notification text (not downloaded — a notification can't carry file contents; check Aula directly for the file).
- Both scripts are safe to re-run: `state/state.json` ensures items are never re-alerted once seen.
- `ha_notify.py`: pushes notifications via Home Assistant's Core API, using the add-on's auto-injected `SUPERVISOR_TOKEN`. This is the sole delivery channel (no email fallback) — see "Running as a Home Assistant Add-on" above. It delivers to every service named in `ha_notify_service` (comma-separated), failing only when *all* of them fail, so the phone push surviving is enough for the digest to count as sent.
- `notify_failure.py`: if either script crashes for any reason (including MitID auth expiring), it pushes a `[Aula] <job> failed` notice with the traceback via the same Home Assistant path — so a broken scheduled run surfaces immediately instead of "I haven't gotten a digest in three weeks." If Home Assistant itself is unreachable, this has nowhere left to go; check the add-on's Log tab in that case.

## Known limitations

- **Single delivery channel by design**: if Home Assistant is down or unreachable, both the digest/alerts and the failure notice about a crash have nowhere to go — visible only in the add-on's own Log tab. This was an explicit trade-off in exchange for not maintaining a second (email) channel.
- MitID auth is interactive on first login and whenever the refresh token expires — this can't be made fully unattended, and requires `docker exec`-ing into the add-on's container to redo (see step 7 above).
- This relies on an unofficial, reverse-engineered API; if Aula changes its backend, `aula` CLI commands may break until the upstream project catches up.
- Un-flagged posts and notifications (photo uploads, presence changes, etc.) are never surfaced, even if genuinely important — there's no LLM safety net for content the school forgot to mark important.
- The MitID app-login QR flow requires scanning **both** QR codes (they encode two halves of one verification value, which MitID rotates about once a second) — easy to miss, and the failure mode if you only scan one, or scan two halves from different rotations, isn't an obvious error message. Both codes are therefore rendered into a single self-refreshing page; scanning from a stale or manually saved copy of the image will not work.
- MitID rate-limits and eventually blocks an account after repeated failed or abandoned login attempts, so a broken login loop is expensive to debug. Prefer getting a successful login on a dev machine and copying `tokens.json` in (see step 4 of local setup). An account that ends up blocked has to be unblocked via mitid.dk self-service or MitID support.
