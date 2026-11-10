"""Durable, lab-only mutation coordinator. No HTTP endpoints or production adapter.

Adapters are trusted code: they must implement server-side atomic revision/slot
checks and operation-ID lookup, not emulate them with a check followed by write.
A timeout after dispatch is UNKNOWN, never safe to repeat with another ID.
"""
from dataclasses import dataclass
import hashlib
import json
import os
import re
import sqlite3
import stat
from typing import Protocol


class Rejected(Exception):
    """Adapter guarantees no mutation was attempted/committed."""


class SessionExpired(Rejected):
    pass


@dataclass(frozen=True)
class Contract:
    version: str
    environment: str = 'laboratory'
    validated: bool = False
    atomic_preconditions: bool = False
    operation_lookup: bool = False


class Adapter(Protocol):
    contract: Contract

    def inspect(self, request: dict) -> dict:
        """Fresh session, ownership, revision, availability and plan snapshot."""

    def mutate(self, operation_id: str, request: dict, revision: str) -> str:
        """Atomic preconditioned write. Return entity ID; Rejected means no write."""

    def observe(self, operation_id: str, entity_id: str | None) -> dict:
        """Read exact operation ID, never search similar client/time records.

        Return operation_id, entity_id, tenant_id, client_id, action,
        payload_digest and confirmed=True only for durable applied operations.
        """


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value) is not None


def validate(request):
    if not isinstance(request, dict) or set(request) != {'tenant_id', 'client_id', 'action', 'payload', 'consent'}:
        raise ValueError('invalid_request')
    if not all(_identifier(request[k]) for k in ('tenant_id', 'client_id')):
        raise ValueError('invalid_identity')
    action, payload = request['action'], request['payload']
    fields = {
        'create': {'service_id', 'professional_id', 'starts_at'},
        'change': {'appointment_id', 'expected_revision', 'service_id', 'professional_id', 'starts_at'},
        'cancel': {'appointment_id', 'expected_revision'},
        'join_club': {'plan_id', 'terms_version', 'terms_digest', 'amount_cents', 'currency', 'billing_mode', 'enrollment_only'},
    }
    if action not in fields or not isinstance(payload, dict) or set(payload) != fields[action]:
        raise ValueError('invalid_payload')
    for key in ('service_id', 'professional_id', 'appointment_id', 'expected_revision', 'plan_id', 'terms_version'):
        if key in payload and not _identifier(payload[key]):
            raise ValueError('invalid_identity')
    if 'starts_at' in payload:
        import datetime
        try:
            stamp = datetime.datetime.fromisoformat(payload['starts_at'])
            if stamp.tzinfo is None: raise ValueError()
        except (TypeError, ValueError):
            raise ValueError('invalid_time') from None
    if action == 'join_club':
        if (not isinstance(payload['terms_digest'], str) or not re.fullmatch('[a-f0-9]{64}', payload['terms_digest'])
                or type(payload['amount_cents']) is not int or payload['amount_cents'] < 0
                or payload['currency'] != 'BRL' or payload['billing_mode'] not in ('monthly', 'annual', 'one_time')
                or payload['enrollment_only'] is not True):
            raise ValueError('commercial_conditions_required')
    consent = request['consent']
    bound = {k: request[k] for k in ('tenant_id', 'client_id', 'action', 'payload')}
    if (not isinstance(consent, dict) or set(consent) != {'explicit', 'request_digest', 'evidence_id'}
            or consent['explicit'] is not True or consent['request_digest'] != digest(bound)
            or not _identifier(consent['evidence_id'])):
        raise ValueError('explicit_bound_consent_required')


class Coordinator:
    def __init__(self, path, adapter: Adapter):
        self.path, self.adapter = os.path.abspath(os.fspath(path)), adapter
        # Caller provisions a private directory. Never follow links or chmod an
        # existing path: it may point at an unrelated file, including a hardlink.
        self._check_directory()
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            self._check_file(info)
            self._identity = (info.st_dev, info.st_ino)
        finally:
            os.close(fd)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS operations (
                  operation_id TEXT PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
                  request_digest TEXT NOT NULL, request TEXT NOT NULL,
                  state TEXT NOT NULL, entity_id TEXT, reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS locks (
                  resource TEXT PRIMARY KEY, operation_id TEXT NOT NULL);
            ''')

    @staticmethod
    def _check_file(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError('unsafe_ledger_file')

    def _check_directory(self):
        directory = os.path.dirname(self.path)
        current = os.path.sep
        # Reject symlink ancestors as well as a symlink leaf. The existing
        # directory must be private; no implicit chmod or filesystem repair.
        for part in directory.split(os.path.sep):
            if not part:
                continue
            current = os.path.join(current, part)
            info = os.lstat(current)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('unsafe_ledger_directory')
            # Shared ancestors must prevent removal of someone else's entry.
            if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
                raise ValueError('unsafe_ledger_ancestor')
        info = os.lstat(directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('private_ledger_directory_required')

    def _check_storage(self):
        self._check_directory()
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            self._check_file(info)
            if (info.st_dev, info.st_ino) != self._identity:
                raise ValueError('ledger_file_replaced')
        finally:
            os.close(fd)
        # SQLite may open/recover sidecars even before the first query. Never
        # remove or repair suspicious files; preserve them and fail closed.
        for suffix in ('-journal', '-wal', '-shm'):
            try:
                info = os.lstat(self.path + suffix)
            except FileNotFoundError:
                continue
            self._check_file(info)

    def _db(self):
        self._check_storage()
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    def _contract(self):
        c = self.adapter.contract
        return (c.validated is True and c.atomic_preconditions is True and c.operation_lookup is True
                and c.environment == 'laboratory' and bool(c.version))

    @staticmethod
    def _result(row):
        # Public results contain no client identifiers, request, raw error or credentials.
        return {'operation_id': row['operation_id'], 'state': row['state'], 'reason': row['reason']}

    def _finish(self, op, state, reason, entity_id=None):
        with self._db() as db:
            db.execute("UPDATE operations SET state=?,reason=?,entity_id=COALESCE(?,entity_id) WHERE operation_id=? AND state NOT IN ('confirmed','rejected')", (state, reason, entity_id, op))
            if state in ('confirmed', 'rejected'):
                db.execute('DELETE FROM locks WHERE operation_id=?', (op,))
            return self._result(db.execute('SELECT * FROM operations WHERE operation_id=?', (op,)).fetchone())

    def execute(self, event_id, operation_id, request):
        validate(request)
        if not _identifier(event_id) or not _identifier(operation_id): raise ValueError('invalid_operation_identity')
        # Freeze a caller-owned mutable request before any external adapter call.
        request = json.loads(json.dumps(request))
        fingerprint = digest(request)
        event_key = digest([request['tenant_id'], event_id])
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT * FROM operations WHERE operation_id=? OR event_key=?', (operation_id, event_key)).fetchall()
            if rows:
                if len(rows) != 1 or rows[0]['operation_id'] != operation_id or rows[0]['event_key'] != event_key or rows[0]['request_digest'] != fingerprint:
                    raise ValueError('operation_identity_conflict')
                return self._result(rows[0])
            if not self._contract():
                return {'operation_id': operation_id, 'state': 'blocked', 'reason': 'validated_lab_contract_required'}
            resources = [digest([request['tenant_id'], request['client_id'], 'client'])]
            if 'appointment_id' in request['payload']:
                resources.append(digest([request['tenant_id'], request['payload']['appointment_id'], 'appointment']))
            if any(db.execute('SELECT 1 FROM locks WHERE resource=?', (r,)).fetchone() for r in resources):
                return {'operation_id': operation_id, 'state': 'blocked', 'reason': 'unresolved_operation'}
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?)', (operation_id, event_key, fingerprint, json.dumps(request), 'in_flight', None, 'dispatch_or_reconciliation_pending'))
            db.executemany('INSERT INTO locks VALUES (?,?)', [(r, operation_id) for r in resources])
        # Persist in_flight before any attempt: crash/restart never resubmits it.
        try:
            snapshot = self.adapter.inspect(json.loads(json.dumps(request)))
            if snapshot.get('tenant_id') != request['tenant_id'] or snapshot.get('client_id') != request['client_id'] or not _identifier(snapshot.get('revision')):
                raise Rejected('identity_or_revision_unverified')
            p = request['payload']
            if request['action'] in ('change', 'cancel') and (snapshot.get('appointment_id') != p['appointment_id'] or snapshot['revision'] != p['expected_revision']):
                raise Rejected('appointment_changed')
            if request['action'] in ('create', 'change') and snapshot.get('available') is not True:
                raise Rejected('slot_unavailable')
            if request['action'] == 'join_club' and (snapshot.get('plan') != p or snapshot.get('charges_on_enrollment') is not False):
                raise Rejected('commercial_conditions_changed_or_charge_required')
        except SessionExpired:
            return self._finish(operation_id, 'rejected', 'session_expired')
        except Exception:
            return self._finish(operation_id, 'rejected', 'preflight_failed')
        try:
            entity_id = self.adapter.mutate(operation_id, json.loads(json.dumps(request)), snapshot['revision'])
            if not _identifier(entity_id):
                return self._finish(operation_id, 'unknown', 'invalid_mutation_receipt')
        except SessionExpired:
            return self._finish(operation_id, 'rejected', 'session_expired')
        except Rejected:
            return self._finish(operation_id, 'rejected', 'atomic_precondition_rejected')
        except Exception:
            return self._finish(operation_id, 'unknown', 'mutation_outcome_unknown')
        self._finish(operation_id, 'unknown', 'confirmation_pending', entity_id)
        return self.reconcile(operation_id)

    def reconcile(self, operation_id):
        """Read only: the original operation ID is the sole reconciliation key."""
        with self._db() as db:
            row = db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
        if row is None: raise ValueError('unknown_operation_id')
        if row['state'] in ('confirmed', 'rejected'): return self._result(row)
        if not self._contract(): return self._result(row)
        request = json.loads(row['request'])
        try:
            observed = self.adapter.observe(operation_id, row['entity_id'])
            valid = (observed.get('confirmed') is True and observed.get('operation_id') == operation_id
                     and _identifier(observed.get('entity_id'))
                     and (row['entity_id'] is None or row['entity_id'] == observed['entity_id'])
                     and all(observed.get(k) == request[k] for k in ('tenant_id', 'client_id', 'action'))
                     and observed.get('payload_digest') == digest(request['payload']))
            if valid:
                return self._finish(operation_id, 'confirmed', 'readback_verified', observed['entity_id'])
        except Exception:
            pass
        return self._finish(operation_id, 'unknown', 'readback_unverified')
