# kali-scripts

Automation scripts, systemd services, and config snapshots for a [Hermes Agent](https://github.com/NousResearch/hermes-agent) setup running xAI Grok as its sole LLM backend.

## Scripts

### `api_cost_analysis.py`
Parses `~/.hermes/logs/agent.log` for the day, computes xAI API costs, and saves a Markdown report to an Obsidian vault.

**Pricing used:** `$3.00 / $0.75 / $15.00` per 1M tokens (uncached input / cached input / output)

**Output:** `~/Obsidian-Vault/Daily-Briefings/API-Cost-YYYY-MM-DD.md`  
**Ledger:** `~/.hermes/logs/api_cost_ledger.json` (rolling 7-day trend)

**Report includes:**
- Token breakdown (total / cached / uncached / output)
- Cost per component and total saved by caching
- 7-day trend table
- Top sessions by cost
- Cost reduction recommendations (cache hit rate, context size, batching)

Run manually:
```bash
python3 api_cost_analysis.py              # today
python3 api_cost_analysis.py 2026-05-30  # specific date
```

Scheduled via Hermes cron at 23:00 daily with Telegram delivery:
```bash
hermes cron create "0 23 * * *" \
  --name "api-cost-analysis" \
  --deliver telegram \
  --no-agent \
  --script "api_cost_analysis.py"
```

---

### `cron_catchup.py`
Detects Hermes cron jobs that were missed while the system was offline and triggers them on boot.

**Problem it solves:** Hermes' scheduler silently fast-forwards missed jobs after a 2-hour grace window — if the system was off for more than 2 hours when a job was due, the job is skipped until the next cycle. This script runs via systemd right after the gateway starts, wins the race before the first scheduler tick, and fires any missed jobs.

Run manually:
```bash
python3 cron_catchup.py
```

Deploy the companion systemd service for automatic boot-time catchup (see below).

---

### `ensure_keys.sh`
Guards against `~/.hermes/.env` being wiped (e.g. by Windows sync) by checking that `XAI_API_KEY` is present and non-empty. If missing, restores it from the hardcoded fallback and rebuilds the LiteLLM Docker container.

Set your key in the script before deploying:
```bash
XAI_KEY="<your-xai-api-key>"
```

---

## Systemd

### `systemd/user/hermes-cron-catchup.service`
Runs `cron_catchup.py` as a oneshot service after `hermes-gateway.service` on every boot.

**Deploy:**
```bash
cp systemd/user/hermes-cron-catchup.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-cron-catchup.service
```

**Check logs:**
```bash
journalctl --user -u hermes-cron-catchup.service
```

---

## Hermes / LiteLLM Config

### `hermes/litellm_config.yaml`
LiteLLM proxy config stripped to a single model: `xai/grok-4.20-0309-non-reasoning` via `XAI_API_KEY`.

**Deploy:** `cp hermes/litellm_config.yaml ~/.hermes/litellm_config.yaml`  
Then restart the LiteLLM container: `docker restart litellm`

### `hermes/config.yaml`
Hermes agent config with `model.default: grok-4` pointing to the LiteLLM proxy at `localhost:4000`.

**Deploy:** `cp hermes/config.yaml ~/.hermes/config.yaml`

> **Note:** All API keys and secrets are redacted with `<placeholder>` strings. Real keys belong in `~/.hermes/.env` and are never committed.

---

## Setup

1. Copy scripts to `~/.hermes/scripts/` (required for Hermes cron `--script` references):
   ```bash
   cp api_cost_analysis.py cron_catchup.py ~/.hermes/scripts/
   ```

2. Set your `XAI_API_KEY` in `~/.hermes/.env`:
   ```bash
   echo "XAI_API_KEY=xai-..." >> ~/.hermes/.env
   echo "XAI_BASE_URL=https://api.x.ai/v1" >> ~/.hermes/.env
   ```

3. Deploy the LiteLLM config and restart the container:
   ```bash
   cp hermes/litellm_config.yaml ~/.hermes/
   docker restart litellm
   ```

4. Deploy the systemd catchup service:
   ```bash
   cp systemd/user/hermes-cron-catchup.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now hermes-cron-catchup.service
   ```

5. Register the daily cost analysis cron job:
   ```bash
   cp api_cost_analysis.py ~/.hermes/scripts/
   hermes cron create "0 23 * * *" --name "api-cost-analysis" --deliver telegram --no-agent --script "api_cost_analysis.py"
   ```
