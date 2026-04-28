import redis
from rq import Queue

from app.core.config import settings

# Shared Redis connection and RQ Queue instance
redis_conn = redis.from_url(settings.redis_url)
task_queue = Queue("deereach_tasks", connection=redis_conn)
