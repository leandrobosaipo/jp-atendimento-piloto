"""Fixture-only retention planning, SQLite recovery and local operational alerts.

No deletion, quarantine, scheduler, network transport or production adapter exists.
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

UTC = dt.timezone.utc
MARKER = 'jp-fictional-lab-v1\n'
COVERAGE = {
    'included': ['one explicitly selected SQLite database per backup'],
    'excluded': ['Chatwoot/PostgreSQL', 'Redis', 'attachments and object storage',
                 'Langflow/n8n state', 'Evolution/Mac Mini', 'external/offsite backups',
                 'credentials and configuration'],
    'cross_database_atomic': False,
    'production_ready': False,
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def instant(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None, 'Explicit timezone is required')
    return parsed.astimezone(UTC)


def annual_cutoff(anchor):
    """Calendar anniversary in UTC; Feb 29 maps to Feb 28, not 365 days."""
    try:
        return anchor.replace(year=anchor.year - 1)
    except ValueError:
        return anchor.replace(year=anchor.year - 1, day=28)


def scoped(root, path):
    root = Path(root).resolve(strict=True)
    marker = root / '.fictional-lab'
    require(not marker.is_symlink() and marker.read_text() == MARKER,
            'Expected explicit fictional laboratory marker')
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    # Reject symlinks even when their current target happens to be inside the lab.
    require(candidate.is_relative_to(root), 'Path must be inside laboratory')
    require(not any(p.is_symlink() for p in [candidate, *candidate.parents]),
            'Symlink paths are not accepted')
    resolved = candidate.resolve()
    require(resolved != root and resolved.is_relative_to(root), 'Path escapes laboratory')
    return resolved


@contextlib.contextmanager
def read_db(path, immutable=False):
    # Immutable is only for authenticated standalone snapshots, never live sources.
    uri = Path(path).as_uri() + '?mode=ro' + ('&immutable=1' if immutable else '')
    with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        conn.execute('PRAGMA query_only=ON')
        yield conn


def no_sidecars(path):
    for suffix in ('-wal', '-shm', '-journal'):
        sidecar = Path(str(path) + suffix)
        require(not sidecar.exists() and not sidecar.is_symlink(),
                'SQLite sidecar is not allowed: ' + suffix)


def logical_digest(conn):
    h = hashlib.sha256()
    for statement in conn.iterdump():
        h.update(statement.encode('utf-8'))
        h.update(b'\n')
    return h.hexdigest()


def integrity(conn):
    require(conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)],
            'SQLite integrity check failed')
    require(not conn.execute('PRAGMA foreign_key_check').fetchall(),
            'SQLite foreign key check failed')


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def exclusive_file(path):
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)


def save_json(path, value):
    with os.fdopen(exclusive_file(path), 'w') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def backup(root, source, destination, apply=False):
    src, dest = scoped(root, source), scoped(root, destination)
    require(src.is_file(), 'Source database missing')
    require(not dest.exists() and dest.parent.is_dir(), 'Destination must be new with existing parent')
    result = {'operation': 'backup', 'dry_run': not apply, 'coverage': COVERAGE}
    with read_db(src) as conn:
        integrity(conn)
        if not apply:
            return result
        dest.mkdir(mode=0o700)  # Exclusive directory reservation; failures remain for investigation.
        target = dest / 'snapshot.sqlite3'
        os.close(exclusive_file(target))
        with contextlib.closing(sqlite3.connect(target)) as out:
            # Only this new private destination has a single writer. Keep its
            # WAL index in memory; some VFS builds otherwise leave a -shm file.
            require(out.execute('PRAGMA locking_mode=EXCLUSIVE').fetchone()[0] == 'exclusive',
                    'Cannot isolate snapshot connection')
            conn.backup(out)
            # Backup inherits WAL mode. Finalize a standalone DELETE-mode file
            # before hashing, and explicitly close all handles before publication.
            out.commit()
            require(out.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete',
                    'Cannot finalize standalone snapshot')
            integrity(out)
            logical = logical_digest(out)
    no_sidecars(target)
    with open(target, 'rb') as stream:
        os.fsync(stream.fileno())
    manifest = {**result, 'version': 1, 'created_at': dt.datetime.now(UTC).isoformat(),
                'file': target.name, 'sha256': digest(target), 'bytes': target.stat().st_size,
                'logical_sha256': logical, 'journal_mode': 'delete',
                'consistency': 'SQLite backup API, includes committed WAL; single database only'}
    save_json(dest / 'manifest.json', manifest)
    return manifest


def verify_bundle(root, bundle):
    bundle = scoped(root, bundle)
    manifest = scoped(root, bundle / 'manifest.json')
    data = json.loads(manifest.read_text())
    require(data.get('version') == 1 and data.get('file') == 'snapshot.sqlite3', 'Unsupported manifest')
    target = scoped(root, bundle / data['file'])
    no_sidecars(target)
    require(data.get('journal_mode') == 'delete', 'Expected standalone snapshot')
    require(data.get('bytes') == target.stat().st_size and data.get('sha256') == digest(target),
            'Snapshot integrity digest mismatch')
    with read_db(target, immutable=True) as conn:
        integrity(conn)
        require(conn.execute('PRAGMA journal_mode').fetchone()[0] == 'delete',
                'Snapshot must not depend on WAL')
        require(logical_digest(conn) == data.get('logical_sha256'),
                'Snapshot logical digest mismatch')
    no_sidecars(target)
    return target, data


def restore(root, bundle, destination, apply=False):
    target, manifest = verify_bundle(root, bundle)
    dest = scoped(root, destination)
    require(not dest.exists() and dest.parent.is_dir(), 'Restore destination must be new')
    no_sidecars(dest)
    result = {'operation': 'restore', 'dry_run': not apply, 'sha256': manifest['sha256'],
              'coverage': COVERAGE}
    if apply:
        # Reserve exclusively; never replace a running/existing database.
        with os.fdopen(exclusive_file(dest), 'wb') as out, open(target, 'rb') as src:
            for chunk in iter(lambda: src.read(1024 * 1024), b''):
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        require(digest(dest) == manifest['sha256'], 'Restored digest mismatch')
        no_sidecars(dest)
        with read_db(dest, immutable=True) as conn:
            integrity(conn)
            require(logical_digest(conn) == manifest['logical_sha256'],
                    'Restored logical digest mismatch')
        no_sidecars(dest)
    return result


def plan(root, source, kind, anchor, holds_file):
    source, holds_file = scoped(root, source), scoped(root, holds_file)
    holds = json.loads(holds_file.read_text())
    require(isinstance(holds, dict) and set(holds) == {'version', 'holds'} and holds['version'] == 1,
            'Explicit legal holds inventory required')
    require(isinstance(holds['holds'], list) and all(isinstance(x, str) for x in holds['holds']),
            'Legal holds must be record IDs (empty inventory must be explicit)')
    anchor = instant(anchor)
    cutoff = annual_cutoff(anchor)
    sql = {'integration': 'SELECT req,stamp,state FROM events',
           'panel': 'SELECT id,stamp,status FROM turns'}
    require(kind in sql, 'Unsupported retention adapter')
    result = {'operation': 'expiration-plan-only', 'policy_status': 'proposed-not-authorized',
              'anchor': anchor.isoformat(), 'cutoff_exclusive': cutoff.isoformat(),
              'calendar': 'UTC anniversary; Feb 29 maps to Feb 28',
              'timestamp_basis': 'original event stamp; NOT last activity/closure',
              'coverage': COVERAGE, 'eligible': [], 'preserved': [],
              'purge_enabled': False, 'legal_holds_sha256': digest(holds_file)}
    with read_db(source) as conn:
        conn.execute('BEGIN')
        integrity(conn)
        for rid, stamp, state in conn.execute(sql[kind]):
            reason = None
            if rid in holds['holds']:
                reason = 'legal-hold'
            elif state != ('done' if kind == 'integration' else 'complete'):
                reason = 'unfinished-or-uncertain'
            try:
                when = (dt.datetime.fromtimestamp(stamp, UTC) if kind == 'panel'
                        else instant(stamp + '+00:00' if len(stamp) == 19 else stamp))
                if when >= cutoff:
                    reason = reason or 'recent-or-future'
            except (ValueError, TypeError, OverflowError, OSError):
                reason = reason or 'invalid-timestamp'
            if reason:
                result['preserved'].append({'id': rid, 'reason': reason})
            else:
                result['eligible'].append(rid)
    # No records, parts, quota ledgers, receipts, or backups are altered.
    return result


def alerts(root, bundle, anchor, max_age_hours=24):
    require(max_age_hours > 0, 'Maximum backup age must be positive')
    now = instant(anchor)
    notices = []
    try:
        _, manifest = verify_bundle(root, bundle)
        age = (now - instant(manifest['created_at'])).total_seconds() / 3600
        if age < 0:
            notices.append('backup-clock-in-future')
        elif age > max_age_hours:
            notices.append('backup-stale')
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
        notices.append('backup-missing-or-invalid')
    return {'operation': 'local-alert-check', 'alerts': notices,
            'coverage': COVERAGE, 'transport': 'stdout-only', 'healthy': not notices,
            'scope': 'single SQLite backup validity and freshness only'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab-root', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('backup', 'restore'):
        p = sub.add_parser(name)
        p.add_argument('--source', required=True)
        p.add_argument('--destination', required=True)
        p.add_argument('--apply', action='store_true', help='Write new fixture-only output; default is dry-run')
    p = sub.add_parser('plan')
    p.add_argument('--source', required=True)
    p.add_argument('--kind', required=True, choices=['integration', 'panel'])
    p.add_argument('--anchor', required=True)
    p.add_argument('--holds', required=True)
    p = sub.add_parser('alerts')
    p.add_argument('--source', required=True)
    p.add_argument('--anchor', required=True)
    p.add_argument('--max-age-hours', type=float, default=24)
    a = parser.parse_args(argv)
    try:
        if a.command in ('backup', 'restore'):
            result = globals()[a.command](a.lab_root, a.source, a.destination, a.apply)
        elif a.command == 'plan':
            result = plan(a.lab_root, a.source, a.kind, a.anchor, a.holds)
        else:
            result = alerts(a.lab_root, a.source, a.anchor, a.max_age_hours)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2 if result.get('healthy') is False else 0
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc), 'production_ready': False}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
