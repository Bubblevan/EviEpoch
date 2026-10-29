import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from evaluate import acceptance, summarize, failed_searches


class QualityGateControls(unittest.TestCase):
    def test_provider_failure_stays_in_source_coverage_and_answer_denominators(self):
        case = {'id': 'synthetic', 'capabilities': ['A1'], 'searches': [
            {'id': 'q', 'evidence_alternatives': [['source:0']]}]}
        rows = failed_searches(case, RuntimeError('synthetic provider failure'), answer=True)
        report = summarize(rows)
        self.assertEqual(report['complete_source_coverage_rate'], 0)
        self.assertEqual(report['source_recall'], 0)
        self.assertEqual(report['proxy_answer_accuracy'], 0)
        self.assertFalse(acceptance(rows, answer=True)['passed'])

    def test_correct_answer_cannot_hide_private_evidence_leak(self):
        row = {'op': 'search', 'case': 'synthetic', 'id': 'q', 'answer_correct': True,
               'leakage': True, 'complete_evidence': True}
        self.assertFalse(acceptance([row], answer=True)['passed'])

    def test_unknown_sufficiency_is_visible_even_when_scored_subset_passes(self):
        rows = [{'op': 'search', 'case': 'synthetic', 'id': str(i), 'seconds': 1,
                 'answer_correct': True, 'leakage': False, 'complete_evidence': value}
                for i, value in enumerate([True, None])]
        summary = summarize(rows)
        self.assertEqual(summary['complete_evidence_scored'], 1)
        self.assertEqual(summary['complete_evidence_unknown'], 1)
        self.assertEqual(summary['complete_evidence_rate'], 1.0)
        self.assertEqual(acceptance(rows, answer=True)['evidence_unknown'], 1)

    def test_ungraded_answers_or_unverified_privacy_cannot_pass_answer_gate(self):
        for row in [{'op': 'search', 'case': 'x', 'id': 'q', 'leakage': False},
                    {'op': 'search', 'case': 'x', 'id': 'q', 'answer_correct': True}]:
            self.assertFalse(acceptance([row], answer=True)['passed'])
        self.assertFalse(acceptance([], answer=True)['passed'])
