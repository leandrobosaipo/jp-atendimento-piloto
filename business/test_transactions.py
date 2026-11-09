"""Synthetic adapter tests only; these never contact AppBarber or bill anybody."""
import copy
from concurrent.futures import ThreadPoolExecutor
import os
import tempfile
import threading
import unittest
from business.transactions import Contract, Coordinator, Rejected, SessionExpired, digest


def request(action='create'):
    p = {'service_id':'svc1','professional_id':'staff1','starts_at':'2026-11-10T10:00:00-04:00'}
    if action in ('change','cancel'): p.update(appointment_id='appt1', expected_revision='rev1')
    if action == 'cancel': p = {k:p[k] for k in ('appointment_id','expected_revision')}
    if action == 'join_club':
        p = dict(plan_id='plan1', terms_version='v1', terms_digest=digest('fixture terms'), amount_cents=10000,
                 currency='BRL', billing_mode='monthly', enrollment_only=True)
    r = dict(tenant_id='lab',client_id='fictional1',action=action,payload=p)
    r['consent'] = dict(explicit=True,request_digest=digest(r),evidence_id='consent1')
    return r


class FixtureAdapter:
    contract = Contract('fixture-v1', validated=True, atomic_preconditions=True, operation_lookup=True)
    def __init__(self):
        self.calls = 0
        self.records = {}
        self.failure = None
        self.available = True
        self.changed_terms = False
        self.read_failure = False
        self.wrong_read = False
        self.before_write = None

    def inspect(self, r):
        if self.failure == 'session': raise SessionExpired()
        plan = copy.deepcopy(r['payload'])
        if self.changed_terms: plan['terms_version'] = 'v2'
        return dict(tenant_id=r['tenant_id'],client_id=r['client_id'],revision='rev1',
                    appointment_id='appt1',available=self.available,plan=plan,charges_on_enrollment=False)

    def mutate(self, op, r, revision):
        self.calls += 1
        if self.before_write: self.before_write()
        if self.failure == 'conflict': raise Rejected()
        if self.failure == 'session_write': raise SessionExpired()
        if self.failure == 'lost_before': raise TimeoutError()
        self.records[op] = dict(operation_id=op,entity_id='entity1',tenant_id=r['tenant_id'],
                                client_id=r['client_id'],action=r['action'],payload_digest=digest(r['payload']),confirmed=True)
        if self.failure == 'lost_after': raise TimeoutError()
        return 'entity1'

    def observe(self, op, entity):
        if self.read_failure: raise SessionExpired()
        found = copy.deepcopy(self.records.get(op, {}))
        if self.wrong_read: found['operation_id'] = 'another-op'
        return found


class TransactionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'operations.sqlite')
        self.adapter = FixtureAdapter()
        self.coordinator = Coordinator(self.path, self.adapter)

    def run_op(self, action='create', op='op1', event='event1'):
        return self.coordinator.execute(event, op, request(action))

    def test_create_change_cancel_join(self):
        for i, action in enumerate(('create','change','cancel','join_club')):
            with self.subTest(action=action):
                self.assertEqual(self.run_op(action,str(i),str(i))['state'],'confirmed')
        self.assertEqual(self.adapter.calls,4)

    def test_missing_contract_blocks(self):
        self.adapter.contract = Contract('unverified')
        self.assertEqual(self.run_op()['state'],'blocked')
        self.assertEqual(self.adapter.calls,0)

    def test_production_forbidden(self):
        self.adapter.contract = Contract('v1','production',True,True,True)
        self.assertEqual(self.run_op()['state'],'blocked')

    def test_unavailable(self):
        self.adapter.available=False
        self.assertEqual(self.run_op()['state'],'rejected')
        self.assertEqual(self.adapter.calls,0)

    def test_conflict_after_query(self):
        self.adapter.failure='conflict'
        self.assertEqual(self.run_op()['state'],'rejected')
        self.assertEqual(self.adapter.records,{})

    def test_duplicate_and_restart(self):
        first=self.run_op()
        self.coordinator=Coordinator(self.path,self.adapter)
        self.assertEqual(first,self.run_op())
        self.assertEqual(self.adapter.calls,1)
        self.assertEqual(os.stat(self.path).st_mode & 0o777,0o600)

    def test_id_conflicts(self):
        self.run_op()
        for op,event in [('op1','event2'),('op2','event1')]:
            with self.assertRaisesRegex(ValueError,'identity_conflict'): self.run_op(op=op,event=event)
        with self.assertRaisesRegex(ValueError,'identity_conflict'): self.run_op('cancel')

    def test_concurrent_duplicates(self):
        with ThreadPoolExecutor(max_workers=10) as pool:
            results=list(pool.map(lambda _: self.run_op(),range(20)))
        self.assertEqual(self.adapter.calls,1)
        self.assertTrue(all(r['state'] in ('in_flight','unknown','confirmed') for r in results))
        self.assertEqual(self.run_op()['state'],'confirmed')

    def test_different_operation_blocked_while_inflight(self):
        entered, release = threading.Event(), threading.Event()
        self.adapter.before_write=lambda: (entered.set(), release.wait(5))
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending=pool.submit(self.run_op)
            self.assertTrue(entered.wait(5))
            try: self.assertEqual(self.run_op(op='op2',event='event2')['state'],'blocked')
            finally: release.set()
            self.assertEqual(pending.result()['state'],'confirmed')

    def test_unknown_never_retries_and_blocks_new_id(self):
        self.adapter.failure='lost_before'
        self.assertEqual(self.run_op()['state'],'unknown')
        self.coordinator=Coordinator(self.path,self.adapter)
        self.assertEqual(self.run_op()['state'],'unknown')
        self.assertEqual(self.run_op(op='new',event='new')['state'],'blocked')
        self.assertEqual(self.coordinator.reconcile('op1')['state'],'unknown')
        self.assertEqual(self.adapter.calls,1)

    def test_lost_response_reconciles_exact_operation(self):
        self.adapter.failure='lost_after'
        self.assertEqual(self.run_op()['state'],'unknown')
        self.adapter.wrong_read=True
        self.assertEqual(self.coordinator.reconcile('op1')['state'],'unknown')
        self.adapter.wrong_read=False
        self.assertEqual(self.coordinator.reconcile('op1')['state'],'confirmed')
        self.assertEqual(self.adapter.calls,1)

    def test_readback_session_expired(self):
        self.adapter.read_failure=True
        self.assertEqual(self.run_op()['state'],'unknown')
        self.adapter.read_failure=False
        self.assertEqual(self.coordinator.reconcile('op1')['state'],'confirmed')

    def test_session_expired_no_write(self):
        self.adapter.failure='session'
        self.assertEqual(self.run_op()['reason'],'session_expired')
        self.assertEqual(self.adapter.calls,0)

    def test_session_expired_at_write(self):
        self.adapter.failure='session_write'
        self.assertEqual(self.run_op()['state'],'rejected')
        self.assertEqual(self.adapter.records,{})

    def test_consent_required_and_bound(self):
        for change in ('explicit','payload'):
            r=request()
            if change=='explicit': r['consent']['explicit']=False
            else: r['payload']['service_id']='other'
            with self.assertRaisesRegex(ValueError,'consent'): self.coordinator.execute('e','o',r)
        self.assertEqual(self.adapter.calls,0)

    def test_club_terms_missing(self):
        r=request('join_club'); del r['payload']['terms_version']
        with self.assertRaises(ValueError): self.coordinator.execute('e','o',r)
        self.assertEqual(self.adapter.calls,0)

    def test_club_terms_changed(self):
        self.adapter.changed_terms=True
        self.assertEqual(self.run_op('join_club')['state'],'rejected')
        self.assertEqual(self.adapter.calls,0)

    def test_club_unknown(self):
        self.adapter.failure='lost_after'
        self.assertEqual(self.run_op('join_club')['state'],'unknown')
        self.assertEqual(self.run_op('join_club',op='new',event='new')['state'],'blocked')
        self.assertEqual(self.coordinator.reconcile('op1')['state'],'confirmed')
        self.assertEqual(self.adapter.calls,1)

    def test_club_charge_blocked(self):
        original=self.adapter.inspect
        self.adapter.inspect=lambda r: dict(original(r),charges_on_enrollment=True)
        self.assertEqual(self.run_op('join_club')['state'],'rejected')
        self.assertEqual(self.adapter.calls,0)

    def test_wrong_entity_readback(self):
        original=self.adapter.observe
        self.adapter.observe=lambda o,e: dict(original(o,e),entity_id='another')
        self.assertEqual(self.run_op()['state'],'unknown')

    def test_crashed_process_inflight_reconciles_without_write(self):
        def crash(): raise KeyboardInterrupt()
        self.adapter.before_write=crash
        with self.assertRaises(KeyboardInterrupt): self.run_op()
        self.coordinator=Coordinator(self.path,self.adapter)
        self.assertEqual(self.run_op()['state'],'in_flight')
        self.assertEqual(self.coordinator.reconcile('op1')['state'],'unknown')
        self.assertEqual(self.adapter.calls,1)

    def test_database_symlink_does_not_touch_target(self):
        victim = os.path.join(self.tmp.name, 'victim')
        with open(victim, 'wb') as handle: handle.write(b'')
        os.chmod(victim, 0o644)
        alias = os.path.join(self.tmp.name, 'alias.sqlite')
        os.symlink(victim, alias)
        with self.assertRaises((OSError, ValueError)): Coordinator(alias, self.adapter)
        self.assertEqual(os.stat(victim).st_size, 0)
        self.assertEqual(os.stat(victim).st_mode & 0o777, 0o644)

    def test_sidecar_symlinks_preserved_and_rejected(self):
        victim = os.path.join(self.tmp.name, 'sidecar-victim')
        with open(victim, 'wb') as handle: handle.write(b'preserve me')
        os.chmod(victim, 0o644)
        for suffix in ('-journal', '-wal', '-shm'):
            with self.subTest(suffix=suffix):
                sidecar = self.path + suffix
                os.symlink(victim, sidecar)
                try:
                    with self.assertRaises(ValueError): Coordinator(self.path, self.adapter)
                    with self.assertRaises(ValueError): self.run_op()
                    with open(victim, 'rb') as handle: self.assertEqual(handle.read(), b'preserve me')
                    self.assertEqual(os.stat(victim).st_mode & 0o777, 0o644)
                    self.assertTrue(os.path.islink(sidecar))
                finally: os.unlink(sidecar)

    def test_hardlinked_database_rejected_without_chmod(self):
        victim = os.path.join(self.tmp.name, 'hardlink-victim')
        with open(victim, 'wb') as handle: handle.write(b'')
        os.chmod(victim, 0o600)
        alias = os.path.join(self.tmp.name, 'hardlink.sqlite')
        os.link(victim, alias)
        with self.assertRaises(ValueError): Coordinator(alias, self.adapter)
        self.assertEqual(os.stat(victim).st_size, 0)

    def test_nonprivate_directory_rejected(self):
        directory = os.path.join(self.tmp.name, 'public')
        os.mkdir(directory, 0o755)
        path = os.path.join(directory, 'db.sqlite')
        with self.assertRaises(ValueError): Coordinator(path, self.adapter)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(os.stat(directory).st_mode & 0o777, 0o755)

    def test_symlink_directory_rejected(self):
        alias = os.path.join(self.tmp.name, 'linked-directory')
        os.symlink(self.tmp.name, alias)
        with self.assertRaises(ValueError): Coordinator(os.path.join(alias, 'new.sqlite'), self.adapter)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, 'new.sqlite')))

    def test_existing_nonprivate_file_not_repaired(self):
        victim = os.path.join(self.tmp.name, 'insecure.sqlite')
        with open(victim, 'wb') as handle: handle.write(b'')
        os.chmod(victim, 0o644)
        with self.assertRaises(ValueError): Coordinator(victim, self.adapter)
        self.assertEqual(os.stat(victim).st_size, 0)
        self.assertEqual(os.stat(victim).st_mode & 0o777, 0o644)

    def test_replaced_database_rejected(self):
        saved = self.path + '.original'
        os.rename(self.path, saved)
        with open(self.path, 'wb') as handle: handle.write(b'')
        os.chmod(self.path, 0o600)
        with self.assertRaisesRegex(ValueError, 'replaced'): self.run_op()
        self.assertEqual(os.stat(self.path).st_size, 0)

    def test_fifo_database_rejected_without_blocking(self):
        path = os.path.join(self.tmp.name, 'fifo.sqlite')
        os.mkfifo(path, 0o600)
        with self.assertRaises(ValueError): Coordinator(path, self.adapter)

if __name__=='__main__': unittest.main()
