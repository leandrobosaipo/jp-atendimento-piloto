import unittest
from install_same_day_reschedule_stage import inject
class InstallerTest(unittest.TestCase):
 def test_patches_only_reply_execute_4414148(self):
  source="serveCapture(capturedVisitHelpers);async function x(){if(stage==='reply-execute'){const confirmation=await runCapturedConfirmation(capturedVisitHelpers);return n8nStageResponse({confirmation:confirmation.status,attempted:0});}}"
  result=inject(source);self.assertIn('runCapturedReschedule(capturedVisitHelpers,rescheduleHelpers)',result);self.assertIn('createSameDayRescheduleHelpers',result);self.assertIn('rescheduleFeedback',result)
 def test_rejects_unrecognized_stage(self):
  with self.assertRaises(ValueError):inject("const confirmation=await runCapturedConfirmation(capturedVisitHelpers);")
if __name__=='__main__':unittest.main()
