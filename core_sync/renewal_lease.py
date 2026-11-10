"""One admission per refused generation and native n8n job. No login or timer."""
import datetime as dt
import json
import os
from pathlib import Path
from uuid import UUID

def reserve(directory, intent, job, generation, last, now, refused, human_active):
    identity = str(UUID(job['job_id']))
    if (identity != intent.get('job') or job['status'] != 'requested' or
            generation < 1 or human_active or refused != [True, True] or
            not dt.timedelta(minutes=30) <= now-last <= dt.timedelta(days=365)):
        raise ValueError('native_recovery_held')
    data = {'job':identity,'generation':generation,'reserved_at':now.isoformat()}
    # Generation is the idempotency key even when two job IDs point at the same cookie.
    path = Path(directory)/f'generation-{generation}.json'
    fd = os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(data,f);f.flush();os.fsync(f.fileno())
    parent=os.open(str(directory),os.O_RDONLY)
    try:os.fsync(parent)
    finally:os.close(parent)
    return data
