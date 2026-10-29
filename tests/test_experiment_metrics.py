import unittest

from experiments.evidence_metrics import evidence_metrics


class EvidenceMetricTests(unittest.TestCase):
    def test_disclosed_span_ids_do_not_replace_source_ids_for_recall(self):
        groups = [{"source-a", "source-b"}]
        complete, recall = evidence_metrics(groups, {"source-a", "source-b"})
        self.assertTrue(complete)
        self.assertEqual(recall, 1.0)

    def test_partial_and_or_evidence_uses_best_supported_alternative(self):
        groups = [{"source-a", "source-b"}, {"source-c"}]
        complete, recall = evidence_metrics(groups, {"source-a"})
        self.assertFalse(complete)
        self.assertEqual(recall, 0.5)

    def test_unscored_search_has_no_evidence_metric(self):
        self.assertEqual(evidence_metrics([], {"source-a"}), (None, None))


if __name__ == "__main__":
    unittest.main()
