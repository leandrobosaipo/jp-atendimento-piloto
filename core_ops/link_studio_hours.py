"""Adapt the pinned legacy booking module without replacing its identity and write checks."""
import hashlib

def render(raw):
    if hashlib.sha256(raw).hexdigest() != '3f14c3d1edc79642b28c54e7d0ef5f471b9d06443686ffd738427cea23e0722b':
        raise ValueError('booking_module_changed')
    code=raw.decode()
    edits={
        "import {createHash}": "import {weeklyWindows, closedDates, boundaryDecision, studioTimezone} from '../config/booking-hours.mjs';\nimport {createHash}",
        "timezone: 'America/Cuiaba'":"timezone: studioTimezone",
        "Object.freeze({morning:'last_start',afternoon:'last_start',saturday:'last_start'})":"boundaryDecision",
        next(line.strip() for line in code.splitlines() if line.strip().startswith('weekly_windows:')):"weekly_windows: weeklyWindows,",
        "if (!policy.weekly_windows[weekday]?.length)":"if (closedDates.includes(slot.date) || !policy.weekly_windows[weekday]?.length)",
        "policy.weekly_windows, policy.human_whatsapp":"policy.weekly_windows, closedDates, policy.human_whatsapp",
    }
    for old,new in edits.items():
        if code.count(old)!=1:raise ValueError('booking_interface_changed')
        code=code.replace(old,new)
    return code.encode()
