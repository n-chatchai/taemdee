#!/bin/bash
# Start dev server at https://taemdee.local
# Requires: brew install mkcert && mkcert -install && mkcert taemdee.local && echo "127.0.0.1  taemdee.local" | sudo tee -a /etc/hosts

CERT="taemdee.local.pem"
KEY="taemdee.local-key.pem"

if [ ! -f "ssl/$CERT" ] || [ ! -f "ssl/$KEY" ]; then
    echo "SSL certs not found in ssl/ directory. Run:"
    echo "  mkdir -p ssl"
    echo "  mkcert -install"
    echo "  mkcert -cert-file ssl/$CERT -key-file ssl/$KEY taemdee.local"
    exit 1
fi

uv run uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 443 \
    --ssl-certfile "ssl/$CERT" \
    --ssl-keyfile "ssl/$KEY" \
    --reload \
    --workers 2
