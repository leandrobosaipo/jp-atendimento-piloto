import unittest
from install_reply_ack_executor import patched, HELPERS, STAGE


class StageScopeTests(unittest.TestCase):
    def test_stage_is_initialized_and_scoped_before_capture_executes(self):
        decl = "      const stage=req.url.slice('/api/n8n/stages/'.length),sendStages=new Set(['send-initial','send-reminders','support']);"
        program = patched((HELPERS + '\n' + decl + '\n').encode()).decode()
        self.assertLess(program.index(decl), program.index(STAGE))
        self.assertLess(program.index("n8nReplyWorkflowId"), program.index("await runCapturedConfirmation"))
        self.assertLess(program.index("!n8nStageSendEnabled"), program.index("await runCapturedConfirmation"))
        self.assertIn("finally{if(replyDispatchLease?.executor===req)replyDispatchLease=null;}", program)


if __name__ == "__main__":
    unittest.main()
