import importlib.util
import json
from pathlib import Path
import unittest


SOURCE = Path(__file__).with_name("knowledge_editor.py")
SPEC = importlib.util.spec_from_file_location("knowledge_editor", SOURCE)
editor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(editor)


KB = {"version": "fixture-1", "entries": [{"id": "sample", "title": "Sample", "text": "Synthetic fact.",
       "source": "fixture", "keywords": ["sample"]}]}
INSTRUCTIONS = {"version": "operator-fixture-1", "text": "Resposta curta e verdadeira."}


class KnowledgeEditorTests(unittest.TestCase):
    def test_valid_document_is_draft_only(self):
        result = editor.handle({"op": "validate", "document": {"knowledge": KB, "instructions": INSTRUCTIONS}})
        self.assertEqual(result, {"status": "ok", "knowledge_version": "fixture-1",
                                  "instructions_version": "operator-fixture-1", "entry_count": 1,
                                  "publish_requires_confirmation": True})

    def test_rejects_missing_version_and_extra_document_data(self):
        with self.assertRaisesRegex(editor.Refused, "invalid_instruction_version"):
            editor.validate_instructions({"version": "wrong space", "text": "x"})
        with self.assertRaisesRegex(editor.Refused, "invalid_document"):
            editor.validate_document({"knowledge": KB, "instructions": INSTRUCTIONS, "extra": True})

    def test_default_rules_include_schedule_and_emoji_guard(self):
        default = json.loads(Path(__file__).with_name("default_operator_instructions.json").read_text())
        checked = editor.validate_instructions(default)
        self.assertIn("👍", checked["text"])
        self.assertIn("leitura oficial completa", checked["text"].lower())


if __name__ == "__main__":
    unittest.main()
