import asyncio
import logging
import os
import sys

# Ensure the app module is in the PYTHONPATH so RQ can import functions
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from rq import Worker

from app.core.redis_queue import redis_conn
from app.services.web_push import ensure_vapid_keys

# Surface dispatcher INFO logs ('web_push delivered → customer=...',
# 'Campaign X: dispatching N messages', 'refunding K satang ...') in
# journalctl. Without this the default WARNING+ filter swallows them and
# the only thing the operator sees is RQ's own 'Job OK' line.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

# RQ 2.x dropped the `Connection` context manager — pass the redis client
# straight into the Worker constructor instead.
if __name__ == "__main__":
    # The web app's FastAPI lifespan loads VAPID keys into settings — the
    # worker is a separate Python process that doesn't run any lifespan,
    # so without this call settings.web_push_vapid_* stays empty and
    # _send_web_push falls through to its log-only stub even though keys
    # are already in app_secrets. Re-load them at boot.
    asyncio.run(ensure_vapid_keys())

    worker = Worker(["deereach_tasks"], connection=redis_conn)
    worker.work()
