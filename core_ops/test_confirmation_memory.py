import os
import resource
from pathlib import Path
import subprocess
import tempfile
import unittest

from confirmation_memory import patch, transform

ROOT=Path(__file__).resolve().parent


class ConfirmationMemory(unittest.TestCase):
    def test_release_refuses_uninspected_runtime(self):
        with self.assertRaisesRegex(ValueError,'confirmation_runtime_changed'):
            patch('unrecognized source')

    def test_memory_and_attempt_contracts(self):
        source=(ROOT/'fixtures/confirmation-memory-source.txt').read_text()
        fixed=transform(source)
        with tempfile.TemporaryDirectory(prefix='jp-memory-contract-') as folder:
            before=Path(folder)/'before.txt';before.write_text(source)
            after=Path(folder)/'after.txt';after.write_text(fixed)
            env={**os.environ,'FIXTURE_CONCURRENT_READS':'2','FIXTURE_TEMP_ROOT':folder}
            def run(file,case=None):
                return subprocess.run(['node','--max-old-space-size=256',str(ROOT/'confirmation_memory_fixture.mjs'),str(file)],
                    env={**env,**({'FIXTURE_PATCHED':'1'} if file==after else {}),**({'FIXTURE_CASE':case} if case else {})},
                    capture_output=True,text=True,timeout=30,
                    preexec_fn=lambda:resource.setrlimit(resource.RLIMIT_CORE,(0,0)))
            baseline=run(before)
            self.assertNotEqual(baseline.returncode,0)
            self.assertIn('heap out of memory',baseline.stderr)
            for case in [None,'unknown','expired','no-sync','uncertain']:
                with self.subTest(case=case):
                    result=run(after,case)
                    self.assertEqual(result.returncode,0,result.stdout[-1500:]+result.stderr[-1500:])
                    self.assertIn('"fixture_passed":true',result.stdout)


if __name__=='__main__':unittest.main()
