import unittest
import io
from contextlib import redirect_stdout

from prompt_toolkit.document import Document

from advertpreneur_cli.tui import SlashCompleter, TerminalUI


class TuiTests(unittest.TestCase):
    def test_slash_menu_contains_core_commands(self):
        c = SlashCompleter()
        values = [x.text for x in c.get_completions(Document("/"), None)]
        for cmd in (
            "/model",
            "/permissions",
            "/reasoning",
            "/usage",
            "/resume",
            "/compact",
            "/plugins",
            "/skills",
            "/mcp",
            "/login",
            "/logout",
            "/new",
            "/statusline",
            "/bridge",
            "/update",
        ):
            self.assertIn(cmd, values)

    def test_inline_permissions_completion(self):
        c = SlashCompleter()
        values = [x.text for x in c.get_completions(Document("/permissions s"), None)]
        self.assertEqual(values, ["safe"])

    def test_compact_working_footer_keeps_the_joke_and_status_bar(self):
        """Busy external-provider work must retain the same two footer rows as idle."""
        # Construct just the renderer state: PromptSession requires a real Windows
        # console, which is intentionally absent in automated test runs.
        ui = object.__new__(TerminalUI)
        ui.toolbar = lambda: [("class:toolbar", " STATUS BAR ")]
        ui._live_active = True
        ui._live_compact = True
        ui._live_started = 0.0
        ui._live_detail = "main.py"
        ui._live_drawn = False
        ui._joke = "A stable joke row."
        output = io.StringIO()
        with redirect_stdout(output):
            ui._render_live_locked()

        rendered = output.getvalue()
        self.assertIn(ui._joke, rendered)
        self.assertIn("STATUS BAR", rendered)

    def test_detailed_working_footer_keeps_the_joke_row(self):
        ui = object.__new__(TerminalUI)
        ui.toolbar = lambda: [("class:toolbar", " STATUS BAR ")]
        ui._live_active = True
        ui._live_compact = False
        ui._live_started = 0.0
        ui._live_label = "Using tool"
        ui._live_model = "model"
        ui._live_turn = 1
        ui._live_detail = "reading settings.py"
        ui._live_drawn = False
        ui._joke = "A stable joke row."
        output = io.StringIO()
        with redirect_stdout(output):
            ui._render_live_locked()

        self.assertIn(ui._joke, output.getvalue())

    def test_working_card_uses_advertpreneur_ownership_and_hides_model_name(self):
        ui = object.__new__(TerminalUI)
        ui.toolbar = lambda: [("class:toolbar", " STATUS BAR ")]
        ui._live_active = True
        ui._live_compact = False
        ui._live_started = 0.0
        ui._live_label = "Action"
        ui._live_model = "gemini-3.7-flash"
        ui._live_turn = 1
        ui._live_detail = "browser"
        ui._live_drawn = False
        ui._joke = "A stable joke row."
        output = io.StringIO()
        with redirect_stdout(output):
            ui._render_live_locked()

        rendered = output.getvalue()
        self.assertIn("Advertpreneur is acting", rendered)
        self.assertNotIn("gemini-3.7-flash", rendered)
        self.assertIn("Message Advertpreneur", rendered)
        self.assertIn(ui._joke, rendered)
        self.assertIn("STATUS BAR", rendered)

    def test_result_card_prints_persistent_advertpreneur_summary(self):
        ui = object.__new__(TerminalUI)
        ui._live_lock = __import__("threading").RLock()
        ui._live_active = False
        output = io.StringIO()
        with redirect_stdout(output):
            ui.result_card("completed", "Updated homepage", files=["index.html"], actions=2, verification="tests passed")

        rendered = output.getvalue()
        self.assertIn("Advertpreneur result", rendered)
        self.assertIn("Updated homepage", rendered)
        self.assertIn("index.html", rendered)
        self.assertIn("tests passed", rendered)


if __name__ == "__main__":
    unittest.main()
