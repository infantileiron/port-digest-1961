import os,time,logging
from pathlib import Path
import redis
from redis.exceptions import LockError
from src.config.database import Session
from src.services.receiving import process_once
r=redis.Redis.from_url(os.environ['REDIS_URL'])
while True:
 Path('/tmp/receiver-heartbeat').touch()
 try:
  with r.lock('port-digest:receiver',timeout=20,blocking_timeout=0):
   with Session() as s:process_once(s)
 except LockError:pass
 except Exception:logging.exception('receiver tick failed')
 time.sleep(1)
