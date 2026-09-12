import tempfile
import unittest
from pathlib import Path

from advertpreneur_cli.budget import BudgetTracker


class BudgetTests(unittest.TestCase):
    def test_records_and_limits(self):
        with tempfile.TemporaryDirectory() as td:
            tracker = BudgetTracker(Path(td) / "usage.json", daily_limit=1.0, task_limit=0.5)
            tracker.record("x", 100, 20, 0.25)
            self.assertAlmostEqual(tracker.daily_cost(), 0.25)
            self.assertTrue(tracker.can_request()[0])
            tracker.record("x", 100, 20, 0.25)
            self.assertFalse(tracker.can_request()[0])


if __name__ == "__main__":
    unittest.main()
