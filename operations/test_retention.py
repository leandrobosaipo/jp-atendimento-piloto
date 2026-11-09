import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from operations import retention as r


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '.fictional-lab').write_text(r.MARKER)
        self.db = self.root / 'events.sqlite3'
        self.conn = sqlite3.connect(self.db)
        self.addCleanup(self.conn.close)
        self.conn.executescript('''PRAGMA journal_mode=WAL;
          CREATE TABLE events(req TEXT PRIMARY KEY,stamp TEXT,state TEXT);
          CREATE TABLE parts(req TEXT,content TEXT);
          CREATE TABLE turns(id TEXT,stamp INTEGER,status TEXT);
        ''')
        self.conn.executemany('INSERT INTO events VALUES(?,?,?)', [
            ('old', '2024-11-08 12:00:00', 'done'),
            ('boundary', '2025-11-09 00:00:00', 'done'),
            ('recent', '2026-11-08 12:00:00', 'done'),
            ('held', '2024-01-01 00:00:00', 'done'),
            ('pending', '2024-01-01 00:00:00', 'prepared'),
            ('bad', 'bad timestamp', 'done'),
            ('future', '2027-01-01 00:00:00', 'done'),
        ])
        self.conn.execute("INSERT INTO parts VALUES('old','fictional attachment')")
        self.conn.commit()
        self.holds = self.root / 'holds.json'
        self.holds.write_text(json.dumps({'version': 1, 'holds': ['held']}))

    def backup(self):
        return r.backup(self.root, self.db, 'bundle', True)

    def test_backup_wal_and_restore_full_database(self):
        manifest = self.backup()
        self.assertEqual(manifest['coverage']['production_ready'], False)
        r.restore(self.root, 'bundle', 'restored.sqlite3', True)
        with r.read_db(self.root / 'restored.sqlite3') as conn:
            self.assertEqual(conn.execute('SELECT * FROM events').fetchall(),
                             self.conn.execute('SELECT * FROM events').fetchall())
            self.assertEqual(conn.execute('SELECT * FROM parts').fetchall(),
                             [('old', 'fictional attachment')])
        self.assertEqual((self.root / 'restored.sqlite3').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / 'bundle').stat().st_mode & 0o777, 0o700)

    def test_dry_runs_create_nothing(self):
        r.backup(self.root, self.db, 'bundle')
        self.assertFalse((self.root / 'bundle').exists())
        self.backup()
        r.restore(self.root, 'bundle', 'restored.sqlite3')
        self.assertFalse((self.root / 'restored.sqlite3').exists())

    def test_no_overwrite(self):
        self.backup()
        with self.assertRaises(ValueError):
            self.backup()
        with self.assertRaises(ValueError):
            r.restore(self.root, 'bundle', self.db, True)

    def test_tamper_detected_before_restore(self):
        self.backup()
        with open(self.root / 'bundle/snapshot.sqlite3', 'ab') as f:
            f.write(b'tamper')
        with self.assertRaises(ValueError):
            r.restore(self.root, 'bundle', 'new.sqlite3', True)
        self.assertFalse((self.root / 'new.sqlite3').exists())

    def test_manifest_cannot_escape(self):
        self.backup()
        p = self.root / 'bundle/manifest.json'
        data = json.loads(p.read_text()); data['file'] = '../events.sqlite3'
        p.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            r.verify_bundle(self.root, 'bundle')

    def test_scope_and_symlink_protection(self):
        for target in ['../outside', '/tmp/outside']:
            with self.assertRaises(ValueError):
                r.backup(self.root, self.db, target, True)
        (self.root / 'alias').symlink_to(self.db)
        with self.assertRaises(ValueError):
            r.backup(self.root, 'alias', 'bundle', True)

    def test_missing_fixture_marker(self):
        (self.root / '.fictional-lab').write_text('production')
        with self.assertRaises(ValueError):
            self.backup()

    def test_plan_conservative_and_read_only(self):
        before = self.conn.execute('SELECT * FROM events').fetchall()
        result = r.plan(self.root, self.db, 'integration', '2026-11-09T00:00:00Z', self.holds)
        self.assertEqual(result['eligible'], ['old'])
        preserved = {x['id']: x['reason'] for x in result['preserved']}
        self.assertEqual(preserved['held'], 'legal-hold')
        self.assertEqual(preserved['pending'], 'unfinished-or-uncertain')
        self.assertEqual(preserved['bad'], 'invalid-timestamp')
        self.assertEqual(preserved['boundary'], 'recent-or-future')
        self.assertFalse(result['purge_enabled'])
        self.assertEqual(before, self.conn.execute('SELECT * FROM events').fetchall())
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM parts').fetchone(), (1,))

    def test_panel_uncertain_and_current_preserved(self):
        self.conn.executemany('INSERT INTO turns VALUES(?,?,?)', [
            ('a', 0, 'complete'), ('b', 0, 'uncertain'),
            ('c', int(r.instant('2026-11-08T00:00:00Z').timestamp()), 'complete')])
        self.conn.commit()
        result = r.plan(self.root, self.db, 'panel', '2026-11-09T00:00:00Z', self.holds)
        self.assertEqual(result['eligible'], ['a'])
        self.assertEqual(len(result['preserved']), 2)

    def test_calendar_leap_not_365_days(self):
        self.assertEqual(r.annual_cutoff(r.instant('2024-02-29T12:30:00Z')),
                         r.instant('2023-02-28T12:30:00Z'))
        self.assertEqual(r.annual_cutoff(r.instant('2025-03-01T00:00:00Z')),
                         r.instant('2024-03-01T00:00:00Z'))
        with self.assertRaises(ValueError):
            r.instant('2026-11-09T00:00:00')

    def test_holds_inventory_required(self):
        self.holds.write_text('{}')
        with self.assertRaises(ValueError):
            r.plan(self.root, self.db, 'integration', '2026-11-09T00:00:00Z', self.holds)

    def test_corrupt_database_rejected(self):
        (self.root / 'bad.sqlite3').write_bytes(b'not sqlite')
        with self.assertRaises(sqlite3.Error):
            r.backup(self.root, 'bad.sqlite3', 'bundle', True)
        self.assertFalse((self.root / 'bundle').exists())

    def test_backup_alerts_fresh_stale_missing_future(self):
        manifest = self.backup()
        now = r.instant(manifest['created_at'])
        for days, expected in [(0, []), (2, ['backup-stale']), (-1, ['backup-clock-in-future'])]:
            result = r.alerts(self.root, 'bundle', (now + dt.timedelta(days=days)).isoformat())
            self.assertEqual(result['alerts'], expected)
        self.assertEqual(r.alerts(self.root, 'absent', now.isoformat())['alerts'],
                         ['backup-missing-or-invalid'])

    def test_cli_dry_run_and_exit_status(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = r.main(['--lab-root', str(self.root), 'backup', '--source', str(self.db),
                           '--destination', 'bundle'])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())['dry_run'])
        with contextlib.redirect_stdout(io.StringIO()):
            code = r.main(['--lab-root', str(self.root), 'alerts', '--source', 'missing',
                           '--anchor', '2026-11-09T00:00:00Z'])
        self.assertEqual(code, 2)

    def test_restore_refuses_orphaned_live_wal(self):
        self.backup()
        dest = self.root / 'restored.sqlite3'
        alien = sqlite3.connect(dest)
        try:
            alien.execute('PRAGMA journal_mode=WAL')
            alien.execute('CREATE TABLE items(value INTEGER)')
            alien.execute('INSERT INTO items VALUES(999)')
            alien.commit()
            dest.unlink()  # Reproduce missing main file with unrelated WAL still open.
            self.assertTrue(Path(str(dest) + '-wal').exists())
            for apply in (False, True):
                with self.assertRaisesRegex(ValueError, 'sidecar'):
                    r.restore(self.root, 'bundle', dest, apply)
                self.assertFalse(dest.exists())
        finally:
            alien.close()

    def test_restore_refuses_every_sidecar_and_dangling_symlink(self):
        self.backup()
        for suffix in ('-wal', '-shm', '-journal'):
            for symlink in (False, True):
                dest = self.root / ('restored' + suffix + str(symlink))
                sidecar = Path(str(dest) + suffix)
                if symlink:
                    sidecar.symlink_to(self.root / 'missing-target')
                else:
                    sidecar.write_bytes(b'')
                with self.assertRaisesRegex(ValueError, 'sidecar'):
                    r.restore(self.root, 'bundle', dest, True)
                self.assertFalse(dest.exists())

    def test_bundle_rejects_unhashed_wal_contents(self):
        self.backup()
        target = self.root / 'bundle/snapshot.sqlite3'
        writer = sqlite3.connect(target)
        try:
            writer.execute('PRAGMA journal_mode=WAL')
            # Record the hash before committing WAL-only changes, reproducing
            # a manifest that authenticates the main file but not its sidecars.
            manifest_path = self.root / 'bundle/manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['sha256'] = r.digest(target)
            manifest_path.write_text(json.dumps(manifest))
            writer.execute("INSERT INTO events VALUES('wal-only','2024-01-01','done')")
            writer.commit()
            self.assertEqual(manifest['sha256'], r.digest(target))
            with self.assertRaisesRegex(ValueError, 'sidecar'):
                r.verify_bundle(self.root, 'bundle')
            with self.assertRaisesRegex(ValueError, 'sidecar'):
                r.restore(self.root, 'bundle', 'restored.sqlite3', True)
            self.assertFalse((self.root / 'restored.sqlite3').exists())
        finally:
            writer.close()

    def test_snapshot_is_standalone_and_connection_closes(self):
        self.backup()
        target = self.root / 'bundle/snapshot.sqlite3'
        r.no_sidecars(target)
        with r.read_db(target) as conn:
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone(), ('delete',))
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute('SELECT 1')
        r.verify_bundle(self.root, 'bundle')
        r.no_sidecars(target)

    def test_logical_integrity_manifest_checked(self):
        self.backup()
        path = self.root / 'bundle/manifest.json'
        manifest = json.loads(path.read_text())
        manifest['logical_sha256'] = '0' * 64
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'logical digest'):
            r.restore(self.root, 'bundle', 'restored.sqlite3', True)
        self.assertFalse((self.root / 'restored.sqlite3').exists())

    def test_read_connection_refuses_mutations(self):
        with r.read_db(self.db) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('DELETE FROM events')


if __name__ == '__main__':
    unittest.main()
