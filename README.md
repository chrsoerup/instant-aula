# instant-aula

Turns Aula's flood of notifications into two things:

- **A weekly digest** — one email, once a week, summarizing the school week (Meebook weekplan + calendar).
- **Urgent-only alerts** — new messages/notifications/posts are checked periodically and you're only emailed if a local LLM judges them genuinely urgent.

All reading and summarizing of Aula content happens locally via [Ollama](https://ollama.com/) — nothing but the final digest/alert text leaves the machine, and only via email to yourself.

Built on the community-maintained, unofficial [`aula`](https://github.com/nickknissen/aula) CLI (Aula has no official API). Auth is via MitID — treat this like giving a script your login.

## Setup

1. **Install [uv](https://docs.astral.sh/uv/)** if you don't have it. `uv` will provision the required Python 3.14 automatically — no manual interpreter install needed.

2. **Install [Ollama](https://ollama.com/)** and pull a model:
   ```bash
   ollama pull llama3.1:8b
   ```
   Any instruction-following model works; change `OLLAMA_MODEL` in `.env` if you use a different one.

3. **Configure:**
   ```bash
   cp .env.example .env
   ```
   Fill in `AULA_MITID_USERNAME`, the `SMTP_*` fields (an app password if your provider requires one), and `SMTP_TO` (your own address).

4. **Install dependencies:**
   ```bash
   uv sync
   ```

5. **First run (interactive)** — approve the MitID login when prompted (a QR code is printed to the terminal to scan with the MitID app):
   ```bash
   uv run python -m instant_aula.weekly_digest
   ```
   This should send you a digest email. Tokens are then cached by the `aula` CLI at `~/.config/aula/tokens.json` and refreshed automatically — subsequent runs shouldn't need another interactive login (until the refresh token itself eventually expires, at which point you'll need to re-approve once).

6. **Baseline the urgent-check state** so it doesn't try to classify (and alert on) your entire existing backlog:
   ```bash
   uv run python -m instant_aula.urgent_check
   ```
   The first run only records current messages/notifications/posts to `state/state.json`; it won't send any alerts. Run it a second time to confirm it's now a no-op ("No new urgent items.") when nothing has changed.

## Scheduling

Two cron jobs — adjust `crontab -e`, using absolute paths since cron runs with a minimal environment:

```cron
# Weekly digest, Monday 07:00
0 7 * * 1 cd /home/cs/github/instant-aula && /home/cs/.local/bin/uv run python -m instant_aula.weekly_digest >> logs/weekly_digest.log 2>&1

# Urgent check, every 2 hours
0 */2 * * * cd /home/cs/github/instant-aula && /home/cs/.local/bin/uv run python -m instant_aula.urgent_check >> logs/urgent_check.log 2>&1
```

**WSL note:** cron only runs while this WSL instance is up, and WSL doesn't start cron automatically. Either start it once per session (`sudo service cron start`, or add it to your shell profile) or have a Windows Scheduled Task wake WSL on the same cadence if you need this to be reliable while your PC is on but WSL isn't already running.

## How it works

- `aula_cli.py` shells out to the `aula` CLI with `--output json` rather than importing its internals directly, since those are explicitly called out as subject to change.
- `weekly_digest.py` calls `aula weekly-summary --provider meebook`, hands the whole JSON blob to the local model with a summarization prompt, and emails the result.
- `urgent_check.py` calls `aula messages --unread`, `aula notifications`, and `aula posts`; for anything not already recorded in `state/state.json`, it asks the local model to classify urgency against a fixed rubric (school closures, safety/pickup issues within ~24h, explicit action needed within ~24h — everything else is routine) and only emails what comes back urgent.
- Both scripts are safe to re-run: `state/state.json` ensures items are never re-classified or re-alerted once seen.

## Known limitations

- MitID auth is interactive on first login and whenever the refresh token expires — this can't be made fully unattended.
- This relies on an unofficial, reverse-engineered API; if Aula changes its backend, `aula` CLI commands may break until the upstream project catches up.
- The `messages` JSON output doesn't include sender name (only thread subject + message body) — a library limitation, not something worked around here.
