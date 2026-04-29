import logging
import os
import sys

# Ensure the app module is in the PYTHONPATH so RQ can import functions
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from rq import Worker

from app.core.redis_queue import redis_conn

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
    worker = Worker(["deereach_tasks"], connection=redis_conn)
    worker.work()
