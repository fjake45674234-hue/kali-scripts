#!/bin/bash
# Ensures the xAI API key is set in .env even if Windows sync clears it.
# Run on gateway startup via cron or systemd.

ENV="/home/kali/.hermes/.env"
XAI_KEY="<your-xai-api-key-here>"

current=$(grep "^XAI_API_KEY=" "$ENV" 2>/dev/null | cut -d= -f2)

if [ -z "$current" ] || [ ${#current} -lt 10 ]; then
    echo "[$(date)] ensure_keys: XAI_API_KEY was cleared — restoring"
    if grep -q "^XAI_API_KEY=" "$ENV"; then
        sed -i "s|^XAI_API_KEY=.*|XAI_API_KEY=$XAI_KEY|" "$ENV"
    else
        echo "XAI_API_KEY=$XAI_KEY" >> "$ENV"
    fi
    source "$ENV"
    echo "[$(date)] ensure_keys: key restored, rebuilding LiteLLM container"
    docker stop litellm && docker rm litellm
    docker run -d --name litellm --restart always \
      -p 127.0.0.1:4000:4000 \
      -v /home/kali/.hermes/litellm_config.yaml:/app/config.yaml \
      -e XAI_API_KEY="$XAI_API_KEY" \
      ghcr.io/berriai/litellm:main-latest \
      --config /app/config.yaml --port 4000 2>/dev/null
    echo "[$(date)] ensure_keys: LiteLLM rebuilt"
else
    echo "[$(date)] ensure_keys: XAI_API_KEY OK (${#current} chars)"
fi
