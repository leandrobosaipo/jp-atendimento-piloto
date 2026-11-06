import json
import pathlib
import tempfile
import unittest
from update import installed_source

class ReceiptTest(unittest.TestCase):
    def test_original_legacy_current_and_partial(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t)
            def record(f,s):f.write_text(json.dumps({"source":s}))
            record(p/"ready.json","initial");self.assertEqual(installed_source(p),"initial")
            a=p/"update-first";a.mkdir();record(a/"ready.json","first");self.assertEqual(installed_source(p),"first")
            b=p/"update-second";b.mkdir()
            with self.assertRaises(SystemExit):installed_source(p)
            record(b/"ready.json","second");record(p/"installed-current.json","second")
            self.assertEqual(installed_source(p),"second")

if __name__=="__main__":unittest.main()
