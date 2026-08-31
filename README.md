# instant-aula

Turns Aula's flood of notifications into two things:

- **A weekly digest** — one email, once a week, with the school week (Meebook weekplan + calendar) as a table: day, schedule, notes/homework.
- **Must-read alerts** — new messages and school-flagged important posts are emailed as soon as they show up; everything else (routine notifications, un-flagged posts) is left alone.

Both are mostly deterministic Python — the source data (Meebook's weekplan text, Aula's own `is_important` flag on posts, and the fact that messages are personally addressed by a teacher rather than broadcast) already carries most of the signal needed, with no summarization required for grouping/formatting. The one exception: the weekly digest also runs the week's notes through a local Ollama model to surface a short "Husk" (remember) list of parent-actionable items — bring gym clothes, bring the "læsemappe", a permission slip due, etc. — a genuine judgment call rather than a formatting job. See `highlights.py`. It's optional and fails soft: if Ollama isn't running, the digest still sends, just without that section.

Built on the community-maintained, unofficial [`aula`](https://github.com/nickknissen/aula) CLI (Aula has no official API). Auth is via MitID — treat this like giving a script your login.

## Setup

1. **Install [uv](https://docs.astral.sh/uv/)** if you don't have it. `uv` will provision the required Python 3.14 automatically — no manual interpreter install needed.

2. **Configure:**
   ```bash
   cp .env.example .env
   ```
   Fill in `AULA_MITID_USERNAME`, the `SMTP_*` fields (an app password if your provider requires one), and `SMTP_TO` (your own address).

3. **Install dependencies:**
   ```bash
   uv sync
   ```

4. **First login (interactive)** — the `aula` CLI's own QR renderer is hard to scan in most terminals; use the block-character variant instead:
   ```bash
   uv run python scripts/mitid_login.py --output text -v login
   ```
   Two QR codes are shown in sequence — scan QR 1, then QR 2, with the MitID app. Tokens are then cached at `~/.config/aula/tokens.json` and refreshed automatically — subsequent runs shouldn't need another interactive login (until the refresh token itself eventually expires, at which point you'll need to re-approve once, the same way).

5. **Send the first digest:**
   ```bash
   uv run python -m instant_aula.weekly_digest
   ```

6. **Baseline the must-read state** so it doesn't alert on your entire existing backlog:
   ```bash
   uv run python -m instant_aula.urgent_check
   ```
   The first run only records current messages/posts to `state/state.json`; it won't send any alerts. Run it a second time to confirm it's now a no-op ("No new must-read items.") when nothing has changed.

## Scheduling

Scheduling runs via two **Windows Scheduled Tasks** (`InstantAulaWeeklyDigest`, `InstantAulaUrgentCheck`), not WSL's own cron. WSL only runs while actively needed (e.g. a VS Code window keeps it alive) and doesn't start cron automatically on boot — a plain WSL crontab entry silently misses its slot whenever WSL wasn't already up, which is exactly what happened the first time this was scheduled. The Windows tasks invoke `wsl.exe` directly, which boots WSL on demand if it isn't running:

```powershell
# Weekly digest, Saturdays 08:00 (targets the upcoming week)
wsl.exe -d Ubuntu -u cs -- bash -lc "cd ~/github/instant-aula && /home/cs/.local/bin/uv run python -m instant_aula.weekly_digest >> logs/weekly_digest.log 2>&1"

# Must-read check, every 2 hours
wsl.exe -d Ubuntu -u cs -- bash -lc "cd ~/github/instant-aula && /home/cs/.local/bin/uv run python -m instant_aula.urgent_check >> logs/urgent_check.log 2>&1"
```

Manage them via `Get-ScheduledTask`/`Set-ScheduledTask`/`Unregister-ScheduledTask` in PowerShell, or the Task Scheduler GUI. Two default settings had to be overridden after creation, since both would otherwise silently block a laptop from ever running these:

- `DisallowStartIfOnBatteries` / `StopIfGoingOnBatteries` → disabled (the task must run whether or not the laptop is plugged in).
- `WakeToRun` → enabled (so the 08:00 Saturday run actually wakes a sleeping machine instead of getting skipped).

Do **not** also run these via a WSL crontab — with both active, a run where WSL happens to already be up would fire the job twice: two emails, and a real risk of two processes racing on `state/state.json` and corrupting the must-read dedup tracking.

`aula_cli.py` shells out to `uv` via an absolute path (`/home/cs/.local/bin/uv`, overridable via the `UV` env var) rather than relying on `$PATH` — cron/Task Scheduler environments don't include `~/.local/bin` by default, and a bare `"uv"` lookup fails there even though it works fine in an interactive shell. It also retries transient network failures (e.g. DNS not ready right after a machine wakes from sleep) a few times before giving up.

**A cloud-hosted scheduler (GitHub Actions) was tried and deliberately reverted.** It solved the weekend-off problem — runners are always on regardless of the local machine's state — but routes your MitID session and your kid's school data through whichever datacenter GitHub happens to schedule the runner in (confirmed as US-based, not EU, with no way to pin the region on a personal/free plan). That's not an acceptable trade-off for a minor's school data, so this stays local-only. See "Known limitations" for what that costs.

## How it works

- `aula_cli.py` shells out to the `aula` CLI with `--output json` rather than importing its internals directly, since those are explicitly called out as subject to change.
- `weekly_digest.py` calls `aula weekly-summary --provider meebook`, groups calendar events and Meebook weekplan notes by date in Python (parsing both the ISO calendar timestamps and Meebook's Danish day labels like "mandag 17. aug."), splits weekplan text on the teacher's own `___` section breaks into bullets, and renders an HTML table — without touching an LLM, so the teacher's original wording is preserved exactly in the table. It then separately calls `highlights.py`, which sends that same grouped data to a local Ollama model (`llama3.1:8b` by default) asking it to pick out only concrete parent action items, and prepends the result as a "Husk" section above the table.
- `urgent_check.py` calls `aula messages --unread` and `aula posts`; every new unread message is forwarded as-is, and posts are alerted only when Aula's own `is_important` flag is set. Post attachments (e.g. PDFs) are downloaded from their signed URL and attached to the alert email.
- Both scripts are safe to re-run: `state/state.json` ensures items are never re-alerted once seen.
- `notify_failure.py`: if either script crashes for any reason (including MitID auth expiring), it emails a `[Aula] <job> failed` notice with the traceback via the same SMTP path — so a broken scheduled run surfaces immediately instead of "I haven't gotten a digest in three weeks."

## Known limitations

- **The digest/must-read checks simply don't run while the PC is fully powered off** (as opposed to asleep) — e.g. weekends, if that's your habit. Windows Task Scheduler's `WakeToRun` can wake a *sleeping* machine, but nothing software-level can power on a machine that's genuinely off; that needs BIOS-level Wake-on-LAN/RTC alarm support (not configured here) or always-on hardware. A cloud-hosted scheduler would fix this, but was ruled out on privacy grounds (see Scheduling) — deliberately accepted as-is rather than routing a child's school data through infrastructure outside your control.
- MitID auth is interactive on first login and whenever the refresh token expires — this can't be made fully unattended.
- This relies on an unofficial, reverse-engineered API; if Aula changes its backend, `aula` CLI commands may break until the upstream project catches up.
- Un-flagged posts and notifications (photo uploads, presence changes, etc.) are never surfaced, even if genuinely important — there's no LLM safety net for content the school forgot to mark important. If that turns out to be a real gap in practice, an LLM-based fallback (Ollama is already installed) could be reintroduced for that narrower case.
- The MitID app-login QR flow requires scanning **both** QR codes shown, in sequence (they encode two halves of one verification value) — easy to miss, and the failure mode if you only scan one isn't an obvious error message.
