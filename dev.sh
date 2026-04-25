#!/bin/bash
# Start dev server at https://taemdee.local
# Requires: brew install mkcert && mkcert -install && mkcert taemdee.local && echo "127.0.0.1  taemdee.local" | sudo tee -a /etc/hosts

CERT="taemdee.local.pem"
KEY="taemdee.local-key.pem"

if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
    echo "SSL certs not found. Run:"
    echo "  mkcert -install"
    echo "  mkcert taemdee.local"
    exit 1
fi

uv run uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 443 \
    --ssl-certfile "$CERT" \
    --ssl-keyfile "$KEY" \
    --reload \
    --workers 2
