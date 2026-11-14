import unittest
from harness.cli.evaluate_recall import evaluate


class RecallEvaluationTests(unittest.TestCase):
    def test_retained_work_fixture_quality_and_context_gate(self):
        result=evaluate()
        self.assertEqual(result['cases'],8)
        self.assertGreaterEqual(result['recallAt3'],0.875)
        self.assertGreaterEqual(result['meanReciprocalRank'],0.75)
        self.assertLessEqual(result['maxContextCharacters'],result['contextCharacterLimit'])
        self.assertEqual(result['modelCalls'],0)
