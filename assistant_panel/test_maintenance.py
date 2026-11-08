"""Offline cutover fixtures. No systemd, API, real credentials or live databases."""
import json
import pathlib
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch
import maintenance as m
from update import installed_source

class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name); self.receipts = self.root / 'receipts'; self.receipts.mkdir()
        m.record(self.receipts / 'ready.json', {'source': 'old'})
        self.src = self.root / 'src'; self.src.mkdir(); (self.src / 'gateway.py').write_bytes(b'new')
        self.code = self.root / 'gateway.py'; self.code.write_bytes(b'old')
        self.state = self.root / 'events.sqlite3'; self.ledger = self.root / 'trial.sqlite3'
        with sqlite3.connect(self.state) as db:
            db.execute('PRAGMA journal_mode=WAL'); db.execute('CREATE TABLE events(req TEXT,state TEXT)')
            db.execute("INSERT INTO events VALUES('one','done')")
            db.execute('CREATE TABLE parts(req TEXT, content TEXT)'); db.execute("INSERT INTO parts VALUES('one','keep')")
        with sqlite3.connect(self.ledger) as db:
            db.execute('PRAGMA journal_mode=WAL'); db.execute('CREATE TABLE turns(id TEXT,status TEXT,charge INTEGER)')
            db.execute("INSERT INTO turns VALUES('one','complete',10)")
        self.args = types.SimpleNamespace(from_commit='old', source_commit='new', apply=True)
        self.states = dict.fromkeys(m.UNITS, True); self.calls = []; self.fail_info = False
        self.oldinfo = {'status':'ok','kb_version':'fixture','total_reserved_micro_usd':10}
        for name, value in [('STATE',self.state),('LEDGER',self.ledger),('PROTECTED',())]:
            p = patch.object(m,name,value); p.start(); self.addCleanup(p.stop)
        for target, kwargs in [('maintenance.active',{'side_effect':lambda u:self.states[u]}),
                               ('maintenance.subprocess.check_output',{'return_value':b'old'}),
                               ('maintenance.time.sleep',{'return_value':None}),
                               ('maintenance.runtime',{'return_value':{}})]:
            p = patch(target,**kwargs); p.start(); self.addCleanup(p.stop)
    def command(self,*args):
        self.calls.append(args)
        if args[0]=='git':return ''
        self.states[args[2]] = args[1]=='start'
        return ''
    def info(self):
        if self.code.read_bytes()==b'old':return self.oldinfo.copy()
        if self.fail_info:raise RuntimeError('injected postcheck failure')
        with sqlite3.connect(self.ledger) as db:
            db.execute('CREATE TABLE IF NOT EXISTS budget_alerts(month TEXT,threshold INTEGER)')
        return {**self.oldinfo,'budget_month':'2026-11','budget_timezone':'UTC','monthly_micro_usd':10_000_000,
                'month_reserved_micro_usd':10,'prior_uncertain_micro_usd':0,'month_admission_micro_usd':10,'budget_alerts':[]}
    def apply(self):
        return m.cutover(self.args,self.receipts,{'gateway.py':self.code},self.command,self.info,self.root,self.src)
    def test_success_snapshot_and_metadata(self):
        before=m.file_state(self.code); pre=m.audit(); self.apply()
        self.assertEqual(installed_source(self.receipts),'new'); self.assertEqual(self.code.read_bytes(),b'new')
        self.assertEqual(m.file_state(self.code),{**before,'sha256':m.digest(b'new')})
        target=next(self.receipts.glob('update-*'))
        self.assertEqual(m.database_audit(target/'events.sqlite3'),pre['events'])
        self.assertEqual(m.database_audit(target/'trial.sqlite3'),pre['ledger'])
        self.assertTrue(all(self.states.values()))
    def test_failed_postcheck_rolls_back_code_and_retains_database(self):
        pre=m.audit(); self.fail_info=True
        with self.assertRaisesRegex(RuntimeError,'injected'):self.apply()
        self.assertEqual(self.code.read_bytes(),b'old'); self.assertEqual(installed_source(self.receipts),'old')
        self.assertEqual(m.audit(),pre); self.assertTrue(all(self.states.values()))
        target=next(self.receipts.glob('update-*')); self.assertTrue((target/'failure.json').exists())
        self.assertTrue((target/'rollback.json').exists())
        self.fail_info=False; self.apply(); self.assertEqual(installed_source(self.receipts),'new')
        self.assertEqual(len(list(self.receipts.glob('update-*'))),2)
    def test_pending_rejected_before_stop(self):
        for status in ['pending']:
            with sqlite3.connect(self.ledger) as db:db.execute('UPDATE turns SET status=?',(status,))
            with self.assertRaisesRegex(RuntimeError,'AI request'):self.apply()
            self.assertEqual(self.code.read_bytes(),b'old'); self.assertFalse(list(self.receipts.glob('update-*')))
            self.assertFalse(any(c[:2]==('systemctl','stop') for c in self.calls))
    def test_unfinished_event_rejected(self):
        with sqlite3.connect(self.state) as db:db.execute("UPDATE events SET state='held'")
        with self.assertRaisesRegex(RuntimeError,'Unfinished'):self.apply()
    def test_backup_includes_uncheckpointed_wal(self):
        with sqlite3.connect(self.ledger) as db:
            db.execute("INSERT INTO turns VALUES('two','complete',20)"); db.commit()
            got=m.snapshot(self.ledger,self.root/'backup.sqlite3')
            self.assertEqual(got,m.database_audit(self.ledger)); self.assertEqual(got['turns']['count'],2)
    def test_repeated_success_rejects_stale_prior_bytes(self):
        self.apply(); calls=len(self.calls)
        with self.assertRaisesRegex(RuntimeError,'drift'):self.apply()
        self.assertEqual(len(self.calls),calls); self.assertEqual(installed_source(self.receipts),'new')
    def test_info_allows_monthly_additions_not_legacy_drift(self):
        self.code.write_bytes(b'new'); after=self.info(); m.check_info(self.oldinfo,after)
        after['total_reserved_micro_usd']=11
        with self.assertRaisesRegex(RuntimeError,'Legacy'):m.check_info(self.oldinfo,after)
    def test_partial_write_rolls_back(self):
        actual=m.write_code; counter=[0]
        def broken(p,data):
            counter[0]+=1
            if counter[0]==1:
                p.write_bytes(b'partial'); raise OSError('write error')
            actual(p,data)
        with patch.object(m,'write_code',side_effect=broken):
            with self.assertRaises(OSError):self.apply()
        self.assertEqual(self.code.read_bytes(),b'old'); self.assertEqual(installed_source(self.receipts),'old')
    def test_inactive_service_restored(self):
        self.states[m.UNITS[2]]=False; self.apply()
        self.assertFalse(self.states[m.UNITS[2]])

    def test_new_turns_preserved_during_postcheck(self):
        actual=self.info
        def arrival():
            result=actual()
            if self.code.read_bytes()==b'new':
                with sqlite3.connect(self.ledger) as db:
                    db.execute("INSERT OR IGNORE INTO turns VALUES('two','complete',20)")
                result['total_reserved_micro_usd']=30
            return result
        self.info=arrival
        self.apply()
        self.assertEqual(m.database_audit(self.ledger)['turns']['count'],2)
        self.assertEqual(installed_source(self.receipts),'new')
    def test_existing_turn_mutation_rolls_back_without_restoring_db(self):
        actual=self.info
        def mutation():
            result=actual()
            if self.code.read_bytes()==b'new':
                with sqlite3.connect(self.ledger) as db:db.execute('UPDATE turns SET charge=50')
            return result
        self.info=mutation
        with self.assertRaisesRegex(RuntimeError,'Rollback incomplete'):self.apply()
        self.assertEqual(self.code.read_bytes(),b'old')
        with sqlite3.connect(self.ledger) as db:self.assertEqual(db.execute('SELECT charge FROM turns').fetchone()[0],50)
        with self.assertRaises(SystemExit):installed_source(self.receipts)
    def test_stable_uncertain_reservation_is_preserved(self):
        with sqlite3.connect(self.ledger) as db:db.execute("UPDATE turns SET status='uncertain'")
        pre=m.audit(); self.apply(); m.invariant(pre,m.audit(),allow_new=True)
    def test_rollback_write_failure_keeps_ingress_stopped(self):
        self.fail_info=True
        actual=m.write_code
        def broken(p,data):
            if data==b'old':raise OSError('rollback write error')
            actual(p,data)
        with patch.object(m,'write_code',side_effect=broken):
            with self.assertRaisesRegex(RuntimeError,'Rollback incomplete'):self.apply()
        self.assertFalse(any(self.states.values()))
        with self.assertRaises(SystemExit):installed_source(self.receipts)
        target=next(self.receipts.glob('update-*'))
        self.assertTrue((target/'rollback-failure.json').exists())
        self.assertFalse((target/'rollback.json').exists())
    def test_failure_receipt_error_does_not_skip_rollback(self):
        self.fail_info=True; actual=m.record
        def broken(path,value):
            if path.name=='failure.json':raise OSError('disk full')
            actual(path,value)
        with patch.object(m,'record',side_effect=broken):
            with self.assertRaisesRegex(RuntimeError,'injected'):self.apply()
        self.assertEqual(self.code.read_bytes(),b'old')
        self.assertEqual(installed_source(self.receipts),'old')
    def test_rollback_receipt_error_leaves_ingress_stopped(self):
        self.fail_info=True; actual=m.record
        def broken(path,value):
            if path.name in ('rollback.json','rollback-failure.json'):raise OSError('disk full')
            actual(path,value)
        with patch.object(m,'record',side_effect=broken):
            with self.assertRaisesRegex(RuntimeError,'Rollback incomplete'):self.apply()
        self.assertEqual(self.code.read_bytes(),b'old'); self.assertFalse(any(self.states.values()))
        with self.assertRaises(SystemExit):installed_source(self.receipts)
    def test_pointer_replace_committed_then_raised_is_reconciled(self):
        actual=m.os.replace
        def uncertain(src,dst):
            actual(src,dst)
            raise OSError('uncertain publication return')
        with patch.object(m.os,'replace',side_effect=uncertain):self.apply()
        self.assertEqual(self.code.read_bytes(),b'new')
        self.assertEqual(installed_source(self.receipts),'new')
        self.assertTrue(all(self.states.values()))
        self.assertFalse(list(self.receipts.glob('update-*/rollback.json')))
    def test_pointer_replace_not_committed_rolls_back(self):
        with patch.object(m.os,'replace',side_effect=OSError('publication failed')):
            with self.assertRaisesRegex(OSError,'publication failed'):self.apply()
        self.assertEqual(self.code.read_bytes(),b'old')
        self.assertEqual(installed_source(self.receipts),'old')
    def test_no_permission_mutation_primitives(self):
        import ast
        tree=ast.parse(pathlib.Path(m.__file__).read_text())
        banned={'chmod','fchmod','chown','fchown','setxattr','removexattr'}
        self.assertFalse([n for n in ast.walk(tree) if isinstance(n,ast.Attribute) and n.attr in banned])

if __name__=='__main__':unittest.main()
