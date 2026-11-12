import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import reply_capture as capture


DIRECT_JID = "5565999999999@s.whatsapp.net"


class ReplyCaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.queue = sqlite3.connect(":memory:")
        self.queue.execute("CREATE TABLE receipts(seq INTEGER, id TEXT)")
        self.queue.execute("INSERT INTO receipts VALUES(0, 'baseline')")
        self.database = capture.connect(Path(self.directory.name) / "replies.sqlite3")
        self.assertEqual(capture.init(self.queue, self.database), 0)

    def tearDown(self):
        self.database.close()
        self.queue.close()
        self.directory.cleanup()

    def append(self, record_id):
        sequence = self.queue.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM receipts").fetchone()[0]
        self.queue.execute("INSERT INTO receipts VALUES(?, ?)", (sequence, record_id))
        return sequence

    @staticmethod
    def event(message_id, message, *, instance=capture.INSTANCE, jid=DIRECT_JID,
              from_me=False, alt=None):
        key = {"id": message_id, "remoteJid": jid, "fromMe": from_me}
        if alt is not None:
            key["remoteJidAlt"] = alt
        return {
            "instance": instance,
            "event": "messages.upsert",
            "data": {"key": key, "message": message},
        }

    @staticmethod
    def reader(records):
        return lambda record_id: ({"receivedAt": "2026-11-12T12:00:00-04:00"}, records[record_id])

    def captured(self):
        return self.database.execute(
            "SELECT message_id, kind, disposition, quoted_id, address_hashes "
            "FROM captured ORDER BY seq, envelope_pos, item_pos"
        ).fetchall()

    def cursor(self):
        return self.database.execute("SELECT seq, record_id FROM cursor WHERE slot=1").fetchone()

    def test_captures_direct_text_emoji_and_reaction_removal_with_quote(self):
        self.append("text")
        self.append("emoji")
        self.append("reaction")
        removal_id = self.append("removal")
        records = {
            "text": self.event("direct-text", {"conversation": "Confirmo"}),
            "emoji": self.event("emoji-text", {"extendedTextMessage": {"text": "✅"}}),
            "reaction": self.event("reaction", {"reactionMessage": {"key": {"id": "sent-1"}, "text": "👍"}}),
            "removal": self.event("removal", {"reactionMessage": {"key": {"id": "sent-1"}, "text": ""}}),
        }

        result = capture.tick(self.queue, self.database, self.reader(records))

        self.assertEqual(result["watermark"], removal_id)
        self.assertEqual(result["processed"], 4)
        self.assertEqual(result["kinds"], {"text": 1, "emoji_text": 1, "reaction": 2})
        rows = self.captured()
        self.assertEqual([row[:4] for row in rows], [
            ("direct-text", "text", "possible_confirmation", ""),
            ("emoji-text", "emoji_text", "possible_confirmation", ""),
            ("reaction", "reaction", "possible_confirmation", "sent-1"),
            ("removal", "reaction", "removed", "sent-1"),
        ])
        for _, _, _, _, aliases in rows:
            self.assertEqual(json.loads(aliases), [capture.digest(DIRECT_JID)])
        self.assertEqual(self.cursor(), (removal_id, "removal"))

    def test_ignores_group_other_instance_and_outbound_messages(self):
        self.append("group")
        self.append("other-instance")
        final_seq = self.append("outbound")
        records = {
            "group": self.event("group", {"conversation": "sim"}, jid="120363000000@g.us"),
            "other-instance": self.event("other", {"conversation": "sim"}, instance="another-project"),
            "outbound": self.event("outbound", {"conversation": "sim"}, from_me=True),
        }

        result = capture.tick(self.queue, self.database, self.reader(records))

        self.assertEqual(result["kinds"], {})
        self.assertEqual(self.captured(), [])
        self.assertEqual(self.cursor(), (final_seq, "outbound"))

    def test_processes_batch_and_real_idempotency_then_changed_review(self):
        self.append("batch")
        records = {
            "batch": [
                self.event("same-id", {"conversation": "sim"}),
                self.event("other-id", {"conversation": "oi"}),
                self.event("same-id", {"conversation": "sim"}),
            ],
        }

        first = capture.tick(self.queue, self.database, self.reader(records))
        again = capture.tick(self.queue, self.database, self.reader(records))
        self.assertEqual(first["kinds"], {"text": 2, "duplicate": 1})
        self.assertEqual(again["processed"], 0)
        self.assertEqual(len(self.captured()), 2)

        changed_seq = self.append("changed")
        records["changed"] = self.event("same-id", {"conversation": "não"})
        changed = capture.tick(self.queue, self.database, self.reader(records))

        self.assertEqual(changed["watermark"], changed_seq)
        self.assertEqual(changed["kinds"], {"changed_review": 1})
        rows = self.captured()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1][:3], ("same-id", "text", "changed_review"))
        self.assertEqual(self.cursor(), (changed_seq, "changed"))

    def test_holds_cursor_on_diverged_receipt(self):
        self.queue.execute("UPDATE receipts SET id='replaced-baseline' WHERE seq=0")

        with self.assertRaisesRegex(RuntimeError, "receiver_cursor_diverged"):
            capture.tick(self.queue, self.database, lambda _record_id: self.fail("reader must not run"))

        self.assertEqual(self.cursor(), (0, "baseline"))
        self.assertEqual(self.captured(), [])

    def test_rolls_back_batch_and_holds_cursor_when_read_fails(self):
        self.append("first")
        second_seq = self.append("broken")
        first = self.event("first", {"conversation": "sim"})

        def reader(record_id):
            if record_id == "broken":
                raise OSError("simulated read failure")
            return {"receivedAt": "2026-11-12T12:00:00-04:00"}, first

        with self.assertRaisesRegex(OSError, "simulated read failure"):
            capture.tick(self.queue, self.database, reader)

        self.assertEqual(self.cursor(), (0, "baseline"))
        self.assertEqual(self.captured(), [])
        self.assertGreater(second_seq, 0)


if __name__ == "__main__":
    unittest.main()
