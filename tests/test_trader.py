import tempfile
from pathlib import Path
import unittest
from trader import retrieve, risk_check, recommend, fill

class PipelineTests(unittest.TestCase):
    def test_approval_does_not_fill(self):
        self.assertEqual(recommend('approval')['state'], 'pending')
    def test_auto_paper_total(self):
        self.assertEqual(fill(recommend('auto'))['total'], 250000)
    def test_limits_and_invalid_inputs(self):
        for price, qty, spent in [(400000,1,0),(1,0,0),(float('nan'),1,0),(1,True,0),(200000,1,900000)]:
            with self.assertRaises(ValueError):
                risk_check(price,qty,spent)
    def test_no_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(retrieve('query',Path(d)), [])
    def test_citation(self):
        self.assertEqual(retrieve('모멘텀')[0]['source'], 'strategy.md')

if __name__ == '__main__':
    unittest.main()
