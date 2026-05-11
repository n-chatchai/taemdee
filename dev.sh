#!/bin/bash
# Start dev server at https://taemdee.local + https://shop.taemdee.local
# + https://admin.taemdee.local. Cert is wildcard — covers every
# *.taemdee.local subdomain (plus the bare domain) so adding a new
# subdomain doesn't require re-issuing.
#
# First-time setup:
#   brew install mkcert
#   mkcert -install
#   echo "127.0.0.1 taemdee.local shop.taemdee.local admin.taemdee.local" \
#     | sudo tee -a /etc/hosts
#   mkdir -p ssl
#   mkcert -cert-file ssl/taemdee.local.pem -key-file ssl/taemdee.local-key.pem \
#     taemdee.local "*.taemdee.local"
#
# Adding another subdomain (api.taemdee.local etc.) only needs the
# /etc/hosts entry — the cert already covers it.

CERT="taemdee.local.pem"
KEY="taemdee.local-key.pem"

if [ ! -f "ssl/$CERT" ] || [ ! -f "ssl/$KEY" ]; then
    echo "SSL certs not found in ssl/ directory. Run:"
    echo "  mkdir -p ssl"
    echo "  mkcert -install"
    echo "  mkcert -cert-file ssl/$CERT -key-file ssl/$KEY \\"
    echo "    taemdee.local shop.taemdee.local admin.taemdee.local"
    exit 1
fi

# Warn if any of the three subdomains is missing from /etc/hosts so the
# first browser hit doesn't fail with a baffling DNS error.
for host in taemdee.local shop.taemdee.local admin.taemdee.local; do
    if ! grep -q "$host" /etc/hosts; then
        echo "⚠  $host is not in /etc/hosts. Add it with:"
        echo "  echo '127.0.0.1 $host' | sudo tee -a /etc/hosts"
    fi
done

# ADMIN_PIN gates the /admin surface. Empty PIN → /admin/login returns
# 503 so the admin routes can't be reached even with a guessed cookie.
# Override per-shell with ADMIN_PIN=... ./dev.sh.
: "${ADMIN_PIN:=123456}"

ADMIN_PIN="$ADMIN_PIN" \
LOGIN_OTP_SIMULATE=True \
uv run uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 443 \
    --ssl-certfile "ssl/$CERT" \
    --ssl-keyfile "ssl/$KEY" \
    --reload \
    --timeout-graceful-shutdown 1
