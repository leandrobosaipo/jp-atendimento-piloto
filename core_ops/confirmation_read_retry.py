"""Patch the measured Dell initial-confirmation loop before applying a release."""
import hashlib


BASE = 'ab6bf85b234dc76e21e905d2df927cfcdfcd98349bb3a55b76996ab8a4868da4'
ANCHOR = "if(stored?.status==='scheduled'){if(['initial_policy_paused_or_changed'"
RETRY = """if(stored?.status==='scheduled'){
      const readFailure=error.message==='agenda_readback_failed'||error.name==='AbortError'||error.name==='TimeoutError';
      const failures=stored.preflight_read_failures||0;
      if(readFailure&&!stored.attempted_at&&!stored.message_id&&failures<2&&initialWindow(stored)==='due'){
        stored.preflight_read_failures=failures+1;
        stored.last_preflight_read_error=error.message;
        stored.updated_at=new Date().toISOString();
        after.version++;save(after);continue;
      }
      if(['initial_policy_paused_or_changed'"""


def patch(source):
    if hashlib.sha256(source).hexdigest() != BASE or source.count(ANCHOR.encode()) != 1:
        raise ValueError('confirmation_runtime_changed')
    return source.replace(ANCHOR.encode(), RETRY.encode(), 1)
