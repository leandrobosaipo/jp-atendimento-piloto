"""Controlled panel-only cutover. Never restore a live database or change permissions."""
import fcntl
from contextlib import closing
import hashlib
import json
import os
import pathlib
import pwd
import stat
import sqlite3
import subprocess
import time

STATE = pathlib.Path('/var/lib/atendimento-piloto-integracao/events.sqlite3')
LEDGER = pathlib.Path('/var/lib/atendimento-piloto-ai/trial.sqlite3')
UNITS = ('atendimento-piloto-integracao.service', 'atendimento-piloto-ai.socket', 'atendimento-piloto-ai.service')
PROTECTED = (pathlib.Path('/etc/atendimento-piloto-ai'), pathlib.Path('/etc/atendimento-piloto-integracao'),
             *(pathlib.Path('/etc/systemd/system') / n for n in UNITS),
             *(pathlib.Path('/etc/systemd/system') / (n + '.d') for n in UNITS))
MONTHLY = {'budget_month', 'budget_timezone', 'monthly_micro_usd', 'month_reserved_micro_usd',
           'prior_uncertain_micro_usd', 'month_admission_micro_usd', 'budget_alerts'}


def demand(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def record(path, value):
    # Exclusive creation preserves the original evidence on every failure/retry.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
        json.dump(value, f, sort_keys=True); f.flush(); os.fsync(f.fileno())


def database_audit(path, limits=None, *, allow_human_holds=False):
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        demand(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'Invalid SQLite database')
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        result = {}
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            query = 'SELECT * FROM ' + quoted + ' ORDER BY rowid'
            rows = db.execute(query + (' LIMIT ?' if limits and name in limits else ''),
                              (limits[name]['count'],) if limits and name in limits else ()).fetchall()
            result[name] = {'count': len(rows), 'sha256': digest(json.dumps(rows, sort_keys=True).encode())}
        if 'events' in names:
            unfinished = "state!='done'"
            if allow_human_holds and db.execute("SELECT 1 FROM events WHERE state='human' LIMIT 1").fetchone():
                unfinished += " AND NOT (state='human' AND coalesce(reason,'')='human_ownership' AND incoming IS NOT NULL)"
            demand(not db.execute("SELECT 1 FROM events WHERE "+unfinished+" LIMIT 1").fetchone(), 'Unfinished integration events')
        if 'turns' in names:
            demand(not db.execute("SELECT 1 FROM turns WHERE status='pending' LIMIT 1").fetchone(), 'Pending AI request')
        return result


def audit():
    result = {'events': database_audit(STATE), 'ledger': database_audit(LEDGER)}
    demand('events' in result['events'] and 'turns' in result['ledger'], 'Missing state tables')
    return result


def invariant(pre, post, allow_new=False):
    demand(pre['events'] == post['events'], 'Integration data changed')
    # Admission flock covers integration. A direct panel request may arrive after
    # reopening AI: retain its new rows, while proving every original row survives.
    actual = database_audit(LEDGER, pre['ledger']) if allow_new else post['ledger']
    demand({k: v for k, v in pre['ledger'].items() if k != 'budget_alerts'} ==
           {k: v for k, v in actual.items() if k != 'budget_alerts'}, 'AI turns changed')
    if 'budget_alerts' in pre['ledger']:
        demand(actual.get('budget_alerts') == pre['ledger']['budget_alerts'], 'Existing budget alerts changed')


def snapshot(source, target):
    demand(not target.exists(), 'Snapshot already exists')
    with closing(sqlite3.connect(f'file:{source}?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        demand(dst.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'Invalid SQLite backup')
    with target.open('rb') as f: os.fsync(f.fileno())
    # Prove the snapshot is self-contained, without relying on any WAL sidecar.
    with closing(sqlite3.connect(f'file:{target}?immutable=1', uri=True)) as check:
        demand(check.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'Snapshot requires a sidecar')
    return database_audit(target)


def protected(paths=PROTECTED):
    result = {}
    for root in paths:
        if not root.exists():
            result[str(root)] = None
            continue
        for p in [root, *sorted(root.rglob('*'))] if root.is_dir() else [root]:
            demand(not p.is_symlink(), 'Protected symlink requires reconciliation: ' + str(p))
            st = p.stat()
            result[str(p)] = {'mode': st.st_mode, 'uid': st.st_uid, 'gid': st.st_gid,
                              'sha256': digest(p.read_bytes()) if p.is_file() else None}
    return result


def runtime(states, wait=False):
    uid = pwd.getpwnam('delladmin').pw_uid
    paths = {pathlib.Path('/run/atendimento-piloto-ai.sock'): (stat.S_IFSOCK, 0o600)}
    if states[UNITS[0]]:
        paths.update({pathlib.Path('/run/atendimento-piloto-integracao'): (stat.S_IFDIR, 0o700),
                      pathlib.Path('/run/atendimento-piloto-integracao/panel.sock'): (stat.S_IFSOCK, 0o700)})
    # Type=simple becomes active before bind; wait only for existence, never repair.
    if wait:
        for _ in range(30):
            if all(p.exists() for p in paths): break
            time.sleep(.1)
    result = {}
    for p, (kind, mode) in paths.items():
        demand(p.exists() and not p.is_symlink(), 'Missing or unsafe runtime path: ' + str(p))
        st = p.stat()
        demand(stat.S_IFMT(st.st_mode) == kind and stat.S_IMODE(st.st_mode) == mode and st.st_uid == uid
               and not os.listxattr(p), 'Nonstandard runtime ownership, mode or ACL: ' + str(p))
        result[str(p)] = {'mode': st.st_mode, 'uid': st.st_uid, 'gid': st.st_gid}
    return result


def file_state(path):
    demand(not path.is_symlink() and path.is_file(), 'Unsafe installed code')
    s = path.stat()
    return {'sha256': digest(path.read_bytes()), 'mode': s.st_mode, 'uid': s.st_uid, 'gid': s.st_gid, 'inode': s.st_ino}


def write_code(path, data):
    # Keep mode, owner, ACLs and inode. Services are stopped; backups cover partial writes.
    with path.open('r+b') as f:
        f.write(data); f.truncate(); f.flush(); os.fsync(f.fileno())


def active(unit):
    p = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True, timeout=30)
    state = p.stdout.strip()
    demand(state in ('active', 'inactive'), 'Unit not in a stable state: ' + unit)
    return state == 'active'


def stop(run):
    # Close activation first, then terminate only the named services.
    errors = []
    for unit in UNITS:
        try: run('systemctl', 'stop', unit)
        except BaseException as exc: errors.append(str(exc))
    demand(not errors, 'Could not stop ingress: ' + '; '.join(errors))


def restore(run, states):
    # Integration Wants=AI socket, so stop unwanted units after all starts.
    errors = []
    for unit in (UNITS[1], UNITS[2], UNITS[0]):
        if states[unit]:
            try: run('systemctl', 'start', unit)
            except BaseException as exc: errors.append(str(exc))
    for unit in UNITS:
        if not states[unit]:
            try: run('systemctl', 'stop', unit)
            except BaseException as exc: errors.append(str(exc))
    demand(not errors, 'Service restoration failed: ' + '; '.join(errors))
    demand(all(active(u) == states[u] for u in UNITS), 'Service state differs from original')


def legacy_info(before, after, allow_new=False):
    demand(after.get('status') == 'ok', 'Gateway postcheck failed')
    counters = {'day_reserved_micro_usd', 'total_reserved_micro_usd', 'daily_requests'}
    for key, value in before.items():
        if key in MONTHLY:
            continue
        if allow_new and key in counters:
            demand(isinstance(after.get(key), int) and after[key] >= value, 'Legacy usage decreased')
        else:
            demand(after.get(key) == value, 'Legacy info invariant changed')


def check_info(before, after, allow_new=False):
    demand(after.get('status') == 'ok', 'Gateway postcheck failed')
    legacy_info(before, after, allow_new)
    demand(MONTHLY <= after.keys() and after['budget_timezone'] == 'UTC' and after['monthly_micro_usd'] == 10_000_000,
           'Monthly budget fields missing or invalid')
    demand(after['month_admission_micro_usd'] == after['month_reserved_micro_usd'] + after['prior_uncertain_micro_usd'],
           'Monthly accounting mismatch')


def cutover(args, receipts, files, run, info, root, src):
    """Caller has verified clean source, guest identity and previous installed source."""
    integration_receipts = root / 'private/integration-install'
    # Capture all private integration evidence, without advancing its source pointer.
    preserved = lambda: protected((*PROTECTED, integration_receipts, root / 'private/integration-inbox',
                                  pathlib.Path('/usr/local/libexec/atendimento-piloto-integracao'),
                                  pathlib.Path('/usr/local/share/cockpit/conversaspiloto')))
    old = {}; new = {}; metadata = {}
    for name, path in files.items():
        expected = subprocess.check_output(['git', '-c', 'safe.directory=' + str(root), 'show',
                                            args.from_commit + ':assistant_panel/' + name], cwd=root)
        demand(path.read_bytes() == expected and not path.is_symlink(), 'Installed release drift: ' + name)
        old[name] = expected; new[name] = (src / name).read_bytes(); metadata[name] = file_state(path)
    # Integration code must not change in this panel-only deployment.
    demand(not run('git', 'diff', args.from_commit, args.source_commit, '--', 'integration'), 'Integration source changed')
    pointer = receipts / 'installed-current.json'
    if pointer.exists() or pointer.is_symlink():
        demand(not pointer.is_symlink(), 'Unsafe source pointer')
        st = pointer.stat()
        demand(st.st_mode & 0o777 == 0o600 and st.st_uid == os.geteuid() and st.st_gid == os.getegid()
               and not os.listxattr(pointer), 'Source pointer metadata requires reconciliation')
    pointer_before = pointer.read_bytes() if pointer.exists() else None
    states = {u: active(u) for u in UNITS}
    demand(states[UNITS[1]], 'AI socket must be active for verified local info')
    runtime_before = runtime(states)
    with STATE.open('rb') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        target = None; touched = False; succeeded = False; safe_to_restore = True
        try:
            before = info(); pre = audit(); private = preserved()
            time.sleep(1)
            demand(audit() == pre and info() == before, 'Activity during quiet window')
            if not args.apply:
                return {'plan': 'controlled panel-only maintenance', 'audit': pre}
            # Each attempt gets distinct immutable evidence, including resolved rollbacks.
            target = receipts / ('update-' + args.source_commit[:12] + '-maintenance-' + str(time.time_ns()))
            target.mkdir(mode=0o700)
            record(target / 'intent.json', {'source': args.source_commit, 'previous': args.from_commit,
                   'before': before, 'audit': pre, 'files': metadata, 'services': states, 'protected': private,
                   'runtime': runtime_before})
            stop(run)
            demand(audit() == pre and preserved() == private, 'Activity before cutover')
            snapshots = {'events': snapshot(STATE, target / 'events.sqlite3'), 'ledger': snapshot(LEDGER, target / 'trial.sqlite3')}
            demand(snapshots == pre and audit() == pre, 'Snapshot consistency failure')
            record(target / 'snapshots.json', {'audit': snapshots, 'sha256': {n: digest((target / n).read_bytes()) for n in ('events.sqlite3', 'trial.sqlite3')}})
            for i, (name, data) in enumerate(old.items()):
                with (target / ('code-' + str(i))).open('xb') as f:
                    f.write(data); f.flush(); os.fsync(f.fileno())
            touched = True
            for name, path in files.items():
                if old[name] != new[name]: write_code(path, new[name])
            demand(audit() == pre and preserved() == private, 'State changed during stopped cutover')
            run('systemctl', 'start', UNITS[1])
            after = info(); post = audit()
            record(target / 'postcheck.json', {'info': after, 'audit': post, 'files': {n: file_state(p) for n, p in files.items()}})
            check_info(before, after, allow_new=True); invariant(pre, post, allow_new=True)
            demand(preserved() == private, 'Private configuration drift')
            for name, path in files.items():
                demand(file_state(path) == {**metadata[name], 'sha256': digest(new[name])}, 'Code or metadata drift')
            restore(run, states)
            demand(runtime(states, wait=True) == runtime_before, 'Runtime metadata changed')
            demand(preserved() == private, 'Configuration changed during restart')
            invariant(pre, audit(), allow_new=True)
            result = {'source': args.source_commit, 'previous': args.from_commit, 'before': pre, 'after': post,
                      'integration_source_unchanged': True, 'changed': [n for n in old if old[n] != new[n]]}
            record(target / 'ready.json', result)
            # Pointer is metadata only. Replace without modifying permissions on any existing file.
            pointer = receipts / ('pointer-' + str(time.time_ns()) + '.json')
            record(pointer, {'source': args.source_commit})
            os.replace(pointer, receipts / 'installed-current.json')
            succeeded = True
            return result
        except BaseException as exc:
            # os.replace may have committed before an interrupt/uncertain return.
            # Durable ready + the actual new pointer are the publication boundary.
            if target is not None and (target / 'ready.json').exists():
                try:
                    ready = json.loads((target / 'ready.json').read_bytes())
                    published = json.loads((receipts / 'installed-current.json').read_bytes())
                    committed = (ready.get('source') == args.source_commit and published.get('source') == args.source_commit
                                 and all(p.read_bytes() == new[n] for n, p in files.items()))
                except (OSError, ValueError):
                    committed = False
                if committed:
                    succeeded = True
                    return ready
            if target is not None:
                evidence_error = None
                try:
                    record(target / 'failure.json', {'error': str(exc), 'code_touched': touched})
                except BaseException as write_error:
                    evidence_error = str(write_error)
                try:
                    if touched:
                        stop(run)
                        for name, path in files.items(): write_code(path, old[name])
                    run('systemctl', 'start', UNITS[1])
                    rollback_info = info()
                    legacy_info(before, rollback_info, allow_new=True)
                    restore(run, states)
                    demand(runtime(states, wait=True) == runtime_before, 'Rollback runtime metadata changed')
                    demand(all(file_state(p) == metadata[n] for n, p in files.items()), 'Rollback bytes or metadata differ')
                    invariant(pre, audit(), allow_new=True)
                    demand(preserved() == private, 'Rollback configuration differs')
                    current_pointer = receipts / 'installed-current.json'
                    demand((current_pointer.read_bytes() if current_pointer.exists() else None) == pointer_before,
                           'Source pointer changed; rollback needs reconciliation')
                    # A ready receipt may exist if final pointer publication failed.
                    # Rollback resolution takes precedence in installed_source().
                    record(target / 'rollback.json', {'status': 'resolved', 'source': args.from_commit,
                                                     'failed_source': args.source_commit, 'audit': audit(), 'info': rollback_info,
                                                     'failure_record_error': evidence_error})
                except BaseException as rollback_error:
                    safe_to_restore = False
                    try:
                        stop(run)
                    except BaseException as stop_error:
                        try: record(target / 'stop-failure.json', {'error': str(stop_error)})
                        except BaseException: pass
                    try: record(target / 'rollback-failure.json', {'error': str(rollback_error)})
                    except BaseException: pass
                    raise RuntimeError('Rollback incomplete; reconcile receipt ' + str(target)) from rollback_error
            raise
        finally:
            if not succeeded and safe_to_restore:
                restore(run, states)
