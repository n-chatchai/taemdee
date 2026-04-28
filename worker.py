import os
import sys

# Ensure the app module is in the PYTHONPATH so RQ can import functions
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from rq import Worker

from app.core.redis_queue import redis_conn

# RQ 2.x dropped the `Connection` context manager — pass the redis client
# straight into the Worker constructor instead.
if __name__ == "__main__":
    worker = Worker(["deereach_tasks"], connection=redis_conn)
    worker.work()
