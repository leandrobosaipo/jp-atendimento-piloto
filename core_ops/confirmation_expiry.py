"""Preserve the last hold reason when a confirmation window closes."""
import hashlib


SOURCE_SHA256 = "a618c01934dafd450ca027a1d7b9035fa197fa3c63817b29b6ab344d0e8541ff"
OLD = """      if(!policyChanged){if(['scheduled','blocked'].includes(last.status)&&!last.attempted_at&&initialWindow(last,now)==='expired'){last.status='skipped';last.blocked_reason='scheduled_time_expired';last.updated_at=new Date(now).toISOString();}continue;}"""
NEW = """      if(!policyChanged){
        if(['scheduled','blocked'].includes(last.status)&&!last.attempted_at&&initialWindow(last,now)==='expired'){
          if(last.status==='blocked'&&last.blocked_reason)last.blocked_reason_before_expiry=last.blocked_reason;
          last.status='skipped';last.blocked_reason='scheduled_time_expired';last.updated_at=new Date(now).toISOString();
        }
        continue;
      }"""


def patch(source):
    if hashlib.sha256(source.encode()).hexdigest() != SOURCE_SHA256 or source.count(OLD) != 1:
        raise ValueError("unexpected_confirmation_module")
    return source.replace(OLD, NEW, 1)
