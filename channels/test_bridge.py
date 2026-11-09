import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from bridge import Event, Held, Outbox, canonical, digest, evolution_event, instagram_events, exception_summary, exception_destination


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.sqlite3'
        self.box = Outbox(self.path)
        self.event = Event('whatsapp', 'fixture', '5511111111111@s.whatsapp.net', 'native-1', 'Olá')
        self.key = self.box.receive(self.event)
        self.assets = [{'id': f'{g}-{i}', 'group': g, 'sha256': hashlib.sha256(f'{g}{i}'.encode()).hexdigest(), 'filename': f'{g}-{i}.jpg', 'mime': 'image/jpeg'} for g in ['servicos', 'clube'] for i in range(3)]

    def send(self, event, payload):
        return {'id': digest(payload)[:16], 'account': event['account'], 'destination': event['sender']}

    def verifier(self, event, native, payload):
        return {'id': native, 'account': event['account'], 'destination': event['sender'], 'payload_sha256': digest(payload), 'status': 'delivered'}

    def transport(self, func=None):
        class Transport: pass
        t = Transport(); t.send = func or self.send
        return t

    def test_event_replay_conflict_and_channel_scope(self):
        self.assertEqual(self.box.receive(self.event), self.key)
        with self.assertRaisesRegex(Held, 'identity_conflict'):
            self.box.receive(Event('whatsapp', 'fixture', self.event.sender, 'native-1', 'Alterado'))
        self.assertNotEqual(self.box.receive(Event('instagram', 'fixture', 'person', 'native-1', 'Olá')), self.key)

    def test_three_variants_each_and_replay_reservation(self):
        for group in ('servicos', 'clube'):
            observed = []
            for i in range(3):
                key = self.box.receive(Event('whatsapp', 'fixture', self.event.sender, f'{group}-{i}', 'Tabela'))
                parts = self.box.prepare(key, ['*R$ 35,00*'], image_group=group, assets=self.assets, choose=lambda candidates: candidates[i])
                observed.append(json.loads(parts[-1]['payload'])['asset']['id'])
                self.assertEqual(parts, self.box.prepare(key, ['*R$ 35,00*'], image_group=group, assets=self.assets, choose=lambda _: self.fail('replay reselected')))
            self.assertEqual(len(set(observed)), 3)

    def test_variant_validation_and_plan_conflict(self):
        with self.assertRaisesRegex(Held, 'three_approved'):
            self.box.prepare(self.key, ['ok'], image_group='servicos', assets=self.assets[:2])
        self.box.prepare(self.key, ['ok'])
        with self.assertRaisesRegex(Held, 'plan_conflict'): self.box.prepare(self.key, ['changed'])

    def test_bounds_and_format(self):
        for texts in ([], ['x' * 251], ['*broken'], ['ok'] * 11):
            with self.assertRaises(Held): self.box.prepare(self.key, texts)
        parts = self.box.prepare(self.key, ['Valor: *R$ 35,00*.', 'Quer ver horários?'])
        self.assertEqual(json.loads(parts[0]['payload'])['text'], 'Valor: *R$ 35,00*.')

    def test_accepted_is_not_delivered_and_order(self):
        self.box.prepare(self.key, ['Primeira.', 'Segunda.'])
        self.assertEqual(self.box.advance(self.key, self.transport())['status'], 'accepted_not_delivered')
        with self.assertRaisesRegex(Held, 'no_resend'): self.box.advance(self.key, self.transport())
        self.box.confirm(self.key, 1, self.verifier)
        self.box.advance(self.key, self.transport())
        self.box.confirm(self.key, 2, self.verifier)
        self.assertEqual(self.box.advance(self.key, self.transport())['status'], 'done')
        self.assertTrue(all(p['state'] == 'delivered' for p in self.box.parts(self.key)))

    def test_content_destination_account_mismatch(self):
        self.box.prepare(self.key, ['ok']); self.box.advance(self.key, self.transport())
        for field, value in [('payload_sha256', 'bad'), ('destination', 'other'), ('account', 'other'), ('id', 'other'), ('status', 'sent')]:
            def bad(e, n, p): return {**self.verifier(e, n, p), field: value}
            with self.assertRaises(Held): self.box.confirm(self.key, 1, bad)
        self.assertEqual(self.box.parts(self.key)[0]['state'], 'accepted')

    def test_timeout_holds_after_restart_and_new_event(self):
        calls = []
        def lost(e, p): calls.append(p); raise TimeoutError()
        self.box.prepare(self.key, ['one', 'two'])
        with self.assertRaisesRegex(Held, 'unknown'): self.box.advance(self.key, self.transport(lost))
        box = Outbox(self.path)
        with self.assertRaisesRegex(Held, 'no_resend'): box.advance(self.key, self.transport(lost))
        second = box.receive(Event('whatsapp', 'fixture', self.event.sender, 'next', 'Mais'))
        box.prepare(second, ['reply'])
        with self.assertRaisesRegex(Held, 'prior_event'): box.advance(second, self.transport(lost))
        self.assertEqual(len(calls), 1)

    def test_concurrent_workers_single_send(self):
        self.box.prepare(self.key, ['ok'])
        calls = []
        def send(e, p): calls.append(p); return self.send(e, p)
        def attempt(_):
            try: return Outbox(self.path).advance(self.key, self.transport(send))
            except Held: return None
        with ThreadPoolExecutor(6) as pool: list(pool.map(attempt, range(6)))
        self.assertEqual(len(calls), 1)

    def test_native_id_reuse_is_held(self):
        self.box.prepare(self.key, ['one', 'two'])
        t = self.transport(lambda e, p: {'id': 'same', 'account': e['account'], 'destination': e['sender']})
        self.box.advance(self.key, t); self.box.confirm(self.key, 1, self.verifier)
        with self.assertRaisesRegex(Held, 'unknown'): self.box.advance(self.key, t)
        self.assertEqual(self.box.parts(self.key)[1]['state'], 'sending')

    def test_private_file_and_symlink(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        other = Path(self.tmp.name) / 'linked'; other.symlink_to(self.path)
        with self.assertRaises(Held): Outbox(other)

    def test_hardlink_and_symlink_ancestor_rejected(self):
        hard = Path(self.tmp.name) / 'hard'
        os.link(self.path, hard)
        with self.assertRaisesRegex(Held, 'private_database'): Outbox(hard)
        parent = Path(self.tmp.name) / 'parent-link'
        parent.symlink_to(self.tmp.name, target_is_directory=True)
        with self.assertRaisesRegex(Held, 'unsafe_database_path'): Outbox(parent / 'new.sqlite3')
        self.assertFalse((Path(self.tmp.name) / 'new.sqlite3').exists())

    def test_evolution_normalizer(self):
        body = {'event': 'messages.upsert', 'instance': 'fixture', 'data': {'key': {'id': 'native-1', 'remoteJid': self.event.sender, 'fromMe': False}, 'message': {'conversation': 'Olá'}}}
        with self.assertRaisesRegex(Held, 'unauthenticated'): evolution_event(body, expected_instance='fixture')
        self.assertEqual(evolution_event(body, expected_instance='fixture', authenticated=True), self.event)
        body['data']['key']['fromMe'] = True
        with self.assertRaises(Held): evolution_event(body, expected_instance='fixture', authenticated=True)
        body['data']['key'].update(fromMe=False, remoteJid='123@g.us')
        with self.assertRaises(Held): evolution_event(body, expected_instance='fixture', authenticated=True)

    def test_instagram_hmac_and_account(self):
        body = {'object': 'instagram', 'entry': [{'id': 'account', 'messaging': [{'sender': {'id': 'customer'}, 'recipient': {'id': 'account'}, 'message': {'mid': 'native', 'text': 'Olá'}}]}]}
        secret = b'synthetic-existing-secret'
        def check(value, account='account'):
            raw = canonical(value).encode(); sig = 'sha256=' + hmac.new(secret, raw, hashlib.sha256).hexdigest()
            return instagram_events(raw, sig, app_secret=secret, expected_account=account)
        self.assertEqual(check(body)[0].channel, 'instagram')
        with self.assertRaisesRegex(Held, 'wrong_account'): check(body, 'wrong')
        raw = canonical(body).encode()
        with self.assertRaisesRegex(Held, 'bad_signature'): instagram_events(raw, 'sha256=wrong', app_secret=secret, expected_account='account')
        body['entry'][0]['messaging'][0]['message']['is_echo'] = True
        self.assertEqual(check(body), [])

    def test_instagram_media_not_silently_lost(self):
        body = {'object':'instagram','entry':[{'id':'account','messaging':[{'sender':{'id':'customer'},'recipient':{'id':'account'},'message':{'mid':'id','attachments':[]}}]}]}
        raw = canonical(body).encode(); secret = b'synthetic-existing-secret'
        sig = 'sha256=' + hmac.new(secret, raw, hashlib.sha256).hexdigest()
        with self.assertRaisesRegex(Held, 'requires_review'): instagram_events(raw,sig,app_secret=secret,expected_account='account')

    def test_exception_not_routine_and_minimized(self):
        args = dict(channel='instagram', name='Cliente Fictício', summary='Pedido não resolvido.', decision='Qual serviço usar?', reason='unresolved_request')
        self.assertEqual(set(exception_summary(**args)), {'channel','name','summary','decision','reason'})
        with self.assertRaises(Held): exception_summary(**{**args,'reason':'appointment_confirmed'})
        with self.assertRaises(Held): exception_summary(**{**args,'summary':'x\nHistórico completo'})

    def test_group_fresh_verified_identity_required(self):
        now = dt.datetime.now(dt.timezone.utc)
        cfg = {'mode':'exceptions_only','group_identity_verified':True,'members_authorized':True,'group_id':'123-456@g.us'}
        self.assertEqual(exception_destination(cfg, verified_at=now, now=now), '123-456@g.us')
        for changed in ({'mode':'disabled'},{'members_authorized':False},{'group_id':'person@s.whatsapp.net'}):
            with self.assertRaises(Held): exception_destination({**cfg,**changed},verified_at=now,now=now)
        with self.assertRaises(Held): exception_destination(cfg, verified_at=now-dt.timedelta(days=2), now=now)


if __name__ == '__main__': unittest.main()
