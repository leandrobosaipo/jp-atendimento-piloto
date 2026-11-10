"""Synthetic ledgers only. Every provider call is a local double; network is forbidden."""
import datetime as dt
import hashlib
import hmac
import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from gateway import Gateway, DAY_LIMIT, MONTH_ALERTS, MONTH_LIMIT, RESERVE, TRIAL_LIMIT, Refused
from test_gateway import KB


def uid():
    return str(uuid.uuid4())


class MonthlyBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "synthetic.sqlite3"
        self.now = dt.datetime(2026, 11, 8, 12, tzinfo=dt.timezone.utc)
        self.sent = []
        self.g = self.open_gateway()
        self.addCleanup(lambda: self.g.db.close())
        forbidden = patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No network in budget tests"))
        forbidden.start()
        self.addCleanup(forbidden.stop)

    def fake(self, payload, key):
        self.sent.append(payload)
        return {"status": "completed", "usage": {"input_tokens": 540, "output_tokens": 20}, "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(
                {"answer": "Synthetic hours.", "decision": "answer", "source_ids": ["hours"]})}]}]}

    def uncertain(self, payload, key):
        self.sent.append(payload)
        raise TimeoutError("Synthetic lost response")

    def open_gateway(self, send=None):
        return Gateway(self.path, KB, "offline-fixture", send or self.fake, clock=lambda: self.now)

    def seed(self, charge, day="2026-11-07", status="complete"):
        rid = uid()
        stamp = int(dt.datetime.fromisoformat(day + "T12:00:00+00:00").timestamp())
        self.g.db.execute("INSERT INTO turns(id,session,day,stamp,hash,question,status,charge,answer,sources) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (rid, uid(), day, stamp, "fixture-hash", "Synthetic", status, charge, "Synthetic", "[]"))
        return rid

    def ask(self):
        return {"op": "ask", "session": uid(), "id": uid(), "text": "What are the hours?"}

    def info(self):
        return self.g.handle({"op": "info"})

    def test_approved_values_and_existing_pilot_limits_unchanged(self):
        info = self.info()
        self.assertEqual(info["monthly_micro_usd"], 10_000_000)
        self.assertEqual(MONTH_ALERTS, (5_000_000, 8_000_000))
        self.assertEqual((info["daily_micro_usd"], info["trial_micro_usd"]), (50_000, 250_000))
        self.assertEqual(info["budget_month"], "2026-11")
        self.assertEqual(info["budget_timezone"], "UTC")
        self.assertEqual(info["budget_alerts"], [])
        self.seed(TRIAL_LIMIT)
        with self.assertRaisesRegex(Refused, "^budget_limit$"):
            self.g.handle(self.ask())
        self.assertFalse(self.sent)

    def test_daily_cap_is_not_lifted(self):
        self.seed(DAY_LIMIT, day="2026-11-08")
        with self.assertRaisesRegex(Refused, "^budget_limit$"):
            self.g.handle(self.ask())
        self.assertFalse(self.sent)

    def test_thresholds_are_durable_once_per_month(self):
        self.seed(MONTH_ALERTS[0] - 1)
        self.assertFalse(self.info()["budget_alerts"])
        self.seed(1)
        first = self.info()["budget_alerts"]
        self.assertEqual([a["threshold_micro_usd"] for a in first], [5_000_000])
        self.seed(3_000_000)
        both = self.info()["budget_alerts"]
        self.assertEqual([a["threshold_micro_usd"] for a in both], list(MONTH_ALERTS))
        self.assertEqual(both[0], first[0])
        self.g.db.close()
        self.g = self.open_gateway()
        self.assertEqual(self.info()["budget_alerts"], both)
        self.assertEqual(self.g.db.execute("SELECT count(*) FROM budget_alerts").fetchone()[0], 2)
        self.assertFalse(self.sent)

    def test_alert_records_reservation_atomically_before_provider_and_survives_refund(self):
        self.seed(5_000_000 - RESERVE)
        def inspect(payload, key):
            observer = self.open_gateway()
            try:
                info = observer.handle({"op": "info"})
                self.assertEqual(info["month_admission_micro_usd"], 5_000_000)
                self.assertEqual(len(info["budget_alerts"]), 1)
                self.assertEqual(observer.db.execute("SELECT count(*) FROM turns WHERE status='pending'").fetchone()[0], 1)
            finally:
                observer.db.close()
            return self.fake(payload, key)
        self.g.send = inspect
        message = self.ask()
        with patch("gateway.TRIAL_LIMIT", 100_000_000):
            self.g.handle(message)
        info = self.info()
        self.assertLess(info["month_admission_micro_usd"], 5_000_000)
        self.assertEqual(info["budget_alerts"][0]["observed_micro_usd"], 5_000_000)
        self.assertTrue(self.g.handle(message)["replayed"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.info()["budget_alerts"], info["budget_alerts"])

    def test_exact_cap_admits_once_and_uncertainty_blocks_next_attempt(self):
        self.seed(MONTH_LIMIT - RESERVE)
        self.g.send = self.uncertain
        message = self.ask()
        with patch("gateway.TRIAL_LIMIT", 100_000_000):
            with self.assertRaisesRegex(Refused, "^held_uncertain$"):
                self.g.handle(message)
            with self.assertRaisesRegex(Refused, "^monthly_budget_limit$"):
                self.g.handle(self.ask())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.info()["month_admission_micro_usd"], MONTH_LIMIT)
        self.g.db.close()
        self.g = self.open_gateway()
        with self.assertRaisesRegex(Refused, "^held_uncertain$"):
            self.g.handle(message)
        self.assertEqual(len(self.sent), 1)

    def test_insufficient_headroom_has_no_provider_call_or_turn(self):
        self.seed(MONTH_LIMIT - RESERVE + 1)
        with patch("gateway.TRIAL_LIMIT", 100_000_000):
            with self.assertRaisesRegex(Refused, "^monthly_budget_limit$"):
                self.g.handle(self.ask())
        self.assertFalse(self.sent)
        self.assertEqual(self.g.db.execute("SELECT count(*) FROM turns").fetchone()[0], 1)

    def test_concurrent_admission_cannot_overspend(self):
        self.seed(MONTH_LIMIT - RESERVE)
        barrier = threading.Barrier(2)
        def run():
            gateway = self.open_gateway(self.uncertain)
            try:
                barrier.wait(timeout=5)
                try:
                    gateway.handle(self.ask())
                except Refused as error:
                    return str(error)
            finally:
                gateway.db.close()
        with patch("gateway.TRIAL_LIMIT", 100_000_000), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertCountEqual(results, ["held_uncertain", "monthly_budget_limit"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.info()["month_admission_micro_usd"], MONTH_LIMIT)

    def test_month_rollover_preserves_prior_pending_and_uncertain(self):
        self.seed(5_000_000)
        self.seed(RESERVE, status="pending")
        self.seed(RESERVE, status="uncertain")
        self.info()
        self.now = dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)
        self.seed(8_000_000, day="2026-12-01")
        info = self.info()
        self.assertEqual(info["budget_month"], "2026-12")
        self.assertEqual(info["month_reserved_micro_usd"], 8_000_000)
        self.assertEqual(info["prior_uncertain_micro_usd"], 2 * RESERVE)
        self.assertEqual(info["month_admission_micro_usd"], 8_000_000 + 2 * RESERVE)
        self.assertEqual(len(info["budget_alerts"]), 2)
        self.assertEqual(self.g.db.execute("SELECT count(*) FROM budget_alerts").fetchone()[0], 3)
        self.assertEqual(self.g.db.execute("SELECT count(*) FROM turns").fetchone()[0], 4)

    def test_prior_uncertainty_blocks_new_month_even_with_no_current_spend(self):
        self.seed(MONTH_LIMIT, day="2026-10-31", status="uncertain")
        with patch("gateway.TRIAL_LIMIT", 100_000_000):
            with self.assertRaisesRegex(Refused, "^monthly_budget_limit$"):
                self.g.handle(self.ask())
        self.assertEqual(self.info()["month_reserved_micro_usd"], 0)
        self.assertEqual(self.info()["prior_uncertain_micro_usd"], MONTH_LIMIT)
        self.assertFalse(self.sent)

    def test_year_boundary_and_offset_clock_use_utc(self):
        self.seed(100, day="2026-12-31")
        self.seed(200, day="2027-01-01")
        self.now = dt.datetime.fromisoformat("2026-12-31T23:30:00-04:00")
        info = self.info()
        self.assertEqual(info["budget_month"], "2027-01")
        self.assertEqual(info["month_reserved_micro_usd"], 200)
        self.assertEqual(info["total_reserved_micro_usd"], 300)
        self.now = dt.datetime(2027, 1, 1)
        with self.assertRaisesRegex(Refused, "^invalid_clock$"):
            self.info()

    def test_additive_migration_keeps_legacy_ledger_bytes_and_replay(self):
        message = self.ask()
        response = self.g.handle(message)
        self.seed(RESERVE, status="uncertain")
        before = [tuple(row) for row in self.g.db.execute("SELECT * FROM turns ORDER BY rowid")]
        self.g.db.execute("DROP TABLE budget_alerts")  # Reproduce the legacy schema only in this fixture.
        self.g.db.close()
        self.g = self.open_gateway()
        self.info()
        self.assertEqual([tuple(row) for row in self.g.db.execute("SELECT * FROM turns ORDER BY rowid")], before)
        replay = self.g.handle(message)
        self.assertEqual(replay["reply"], response["reply"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.sent), 1)

    def test_process_interruption_retains_pending_reservation_and_alert(self):
        self.seed(5_000_000 - RESERVE)
        def interrupted(*_):
            raise KeyboardInterrupt("Synthetic termination after reservation")
        self.g.send = interrupted
        message = self.ask()
        with patch("gateway.TRIAL_LIMIT", 100_000_000):
            with self.assertRaises(KeyboardInterrupt):
                self.g.handle(message)
        self.g.db.close()
        self.g = self.open_gateway()
        self.assertEqual(self.g.db.execute("SELECT status FROM turns WHERE id=?", (message["id"],)).fetchone()[0], "pending")
        with self.assertRaisesRegex(Refused, "^held_uncertain$"):
            self.g.handle(message)
        self.assertEqual(self.info()["month_admission_micro_usd"], 5_000_000)
        self.assertEqual(len(self.info()["budget_alerts"]), 1)
        self.assertFalse(self.sent)

    def test_failed_alert_write_rolls_back_reservation_before_provider(self):
        self.seed(5_000_000 - RESERVE)
        self.g.db.execute("CREATE TRIGGER fail_alert BEFORE INSERT ON budget_alerts BEGIN SELECT RAISE(ABORT,'synthetic write failure'); END")
        with patch("gateway.TRIAL_LIMIT", 100_000_000):
            with self.assertRaises(sqlite3.IntegrityError):
                self.g.handle(self.ask())
        self.assertEqual(self.g.db.execute("SELECT count(*) FROM turns").fetchone()[0], 1)
        self.assertEqual(self.g.db.execute("SELECT count(*) FROM budget_alerts").fetchone()[0], 0)
        self.assertFalse(self.sent)

    def test_local_answers_continue_at_cap_without_paid_calls(self):
        self.seed(MONTH_LIMIT)
        self.assertEqual(self.g.handle(dict(self.ask(), text="Oi"))["mode"], "local")
        self.g.context_key = b"synthetic-hmac"
        message = dict(self.ask(), op="integrated", context={"local_reply": "Synthetic approved fact.", "local_decision": "answer"})
        raw = json.dumps({k: message[k] for k in ("id", "session", "text", "context")}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        message["signature"] = hmac.new(self.g.context_key, raw, hashlib.sha256).hexdigest()
        self.assertEqual(self.g.handle(message)["mode"], "local")
        self.assertFalse(self.sent)
        self.assertEqual(self.info()["month_admission_micro_usd"], MONTH_LIMIT)


if __name__ == "__main__":
    unittest.main()
