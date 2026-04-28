import os
import sys

# Ensure the app module is in the PYTHONPATH so RQ can import functions
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from rq import Connection, Worker

from app.core.redis_queue import redis_conn

if __name__ == "__main__":
    with Connection(redis_conn):
        worker = Worker(["deereach_tasks"])
        worker.work()
