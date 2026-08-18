# instant-aula

Turns Aula's flood of notifications into two things:

- **A weekly digest** — one email, once a week, with the school week (Meebook weekplan + calendar) as a table: day, schedule, notes/homework.
- **Must-read alerts** — new messages and school-flagged important posts are emailed as soon as they show up; everything else (routine notifications, un-flagged posts) is left alone.

Both are fully deterministic Python — no LLM involved. The source data (Meebook's weekplan text, Aula's own `is_important` flag on posts, and the fact that messages are personally addressed by a teacher rather than broadcast) already carries the signal needed; no summarization or judgment call turned out to be necessary.

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

Two cron jobs — adjust `crontab -e`, using absolute paths since cron runs with a minimal environment:

```cron
# Weekly digest, Monday 07:00
0 7 * * 1 cd /home/cs/github/instant-aula && /home/cs/.local/bin/uv run python -m instant_aula.weekly_digest >> logs/weekly_digest.log 2>&1

# Must-read check, every 2 hours
0 */2 * * * cd /home/cs/github/instant-aula && /home/cs/.local/bin/uv run python -m instant_aula.urgent_check >> logs/urgent_check.log 2>&1
```

**WSL note:** cron only runs while this WSL instance is up, and WSL doesn't start cron automatically. Either start it once per session (`sudo service cron start`, or add it to your shell profile) or have a Windows Scheduled Task wake WSL on the same cadence if you need this to be reliable while your PC is on but WSL isn't already running.

## How it works

- `aula_cli.py` shells out to the `aula` CLI with `--output json` rather than importing its internals directly, since those are explicitly called out as subject to change.
- `weekly_digest.py` calls `aula weekly-summary --provider meebook`, groups calendar events and Meebook weekplan notes by date in Python (parsing both the ISO calendar timestamps and Meebook's Danish day labels like "mandag 17. aug."), splits weekplan text on the teacher's own `___` section breaks into bullets, and renders an HTML table — all without touching an LLM, so the teacher's original wording is preserved exactly.
- `urgent_check.py` calls `aula messages --unread` and `aula posts`; every new unread message is forwarded as-is, and posts are alerted only when Aula's own `is_important` flag is set.
- Both scripts are safe to re-run: `state/state.json` ensures items are never re-alerted once seen.

## Known limitations

- MitID auth is interactive on first login and whenever the refresh token expires — this can't be made fully unattended.
- This relies on an unofficial, reverse-engineered API; if Aula changes its backend, `aula` CLI commands may break until the upstream project catches up.
- Un-flagged posts and notifications (photo uploads, presence changes, etc.) are never surfaced, even if genuinely important — there's no LLM safety net for content the school forgot to mark important. If that turns out to be a real gap in practice, an LLM-based fallback (Ollama is already installed) could be reintroduced for that narrower case.
