import tempfile
import unittest
from pathlib import Path

from advertpreneur_cli.automation_memory import (
    AutomationMemory,
    MemorySecurityError,
    is_sensitive,
)


class AutomationMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.memory = AutomationMemory(Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_query_memory(self):
        entry = self.memory.save_entry(
            key="checkout_button_selector",
            value="button.btn-checkout-primary",
            category="selectors",
            scope="project",
            confidence=0.95,
        )
        self.assertEqual(entry.key, "checkout_button_selector")
        self.assertEqual(entry.category, "selectors")

        results = self.memory.query(category="selectors")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].value, "button.btn-checkout-primary")

    def test_sensitive_credentials_are_rejected(self):
        self.assertTrue(is_sensitive("Bearer ghp_123456789012345678901234567890123456"))
        self.assertTrue(is_sensitive("sk-12345678901234567890123456789012"))
        self.assertTrue(is_sensitive("password = 'super_secret'"))

        with self.assertRaises(MemorySecurityError):
            self.memory.save_entry(
                key="api_token",
                value="sk-12345678901234567890123456789012",
                category="conventions",
            )

    def test_context_rendering(self):
        self.memory.save_entry(
            key="test_command",
            value="pytest tests/ -v",
            category="commands",
            scope="project",
        )
        ctx = self.memory.context("run test command")
        self.assertIn("pytest tests/ -v", ctx)
        self.assertIn("Learned Project & Automation Memory", ctx)


if __name__ == "__main__":
    unittest.main()
