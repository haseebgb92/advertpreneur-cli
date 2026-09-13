from pathlib import Path

from advertpreneur_cli.browser_control import BrowserController
from advertpreneur_cli.action_gateway import ExternalActionGateway
from advertpreneur_cli.tools import ToolRegistry

try:
    from advertpreneur_cli.research_workflow import ResearchRun
except ImportError:
    ResearchRun = None


def test_research_run_creates_a_resumable_keyword_ledger(tmp_path: Path):
    assert ResearchRun is not None, "ResearchRun should provide the local research ledger"
    run = ResearchRun.create(tmp_path, "Baby products", ["baby sleep sack", "baby swaddle"])

    assert run.next_keyword() == "baby sleep sack"
    assert run.next_keyword() == "baby swaddle"
    assert (tmp_path / ".advertpreneur" / "research" / "baby-products" / "keywords.csv").is_file()


def test_research_run_records_and_renames_a_completed_download(tmp_path: Path):
    run = ResearchRun.create(tmp_path, "Baby products", ["baby sleep sack"])
    assert run.next_keyword() == "baby sleep sack"
    source = tmp_path / "downloads" / "export.csv"
    source.parent.mkdir()
    source.write_text("report", encoding="utf-8")

    target = run.complete_download("baby sleep sack", source, "https://www.amazon.com/s?k=baby+sleep+sack")

    assert target.name == "baby-sleep-sack.csv"
    assert target.read_text(encoding="utf-8") == "report"
    assert not source.exists()
    assert "completed" in run.ledger_path.read_text(encoding="utf-8")


def test_research_run_pauses_an_active_keyword_without_losing_later_work(tmp_path: Path):
    run = ResearchRun.create(tmp_path, "Baby products", ["baby sleep sack", "baby swaddle"])
    assert run.next_keyword() == "baby sleep sack"

    run.pause("baby sleep sack", "verification page detected", "https://www.amazon.com/errors/validateCaptcha")

    assert run.status()["paused"] == ["baby sleep sack"]
    assert run.next_keyword() == "baby swaddle"


def test_research_run_can_resume_a_paused_keyword(tmp_path: Path):
    run = ResearchRun.create(tmp_path, "Baby products", ["baby sleep sack"])
    assert run.next_keyword() == "baby sleep sack"
    run.pause("baby sleep sack", "sign-in required")

    run.resume("baby sleep sack")

    assert run.next_keyword() == "baby sleep sack"


def test_research_run_keeps_only_successful_observed_selectors_for_reuse(tmp_path: Path):
    run = ResearchRun.create(tmp_path, "Baby products", ["baby sleep sack"])

    run.remember_selectors(search="#search", submit="#submit", export="#export")

    assert run.selectors() == {"search": "#search", "submit": "#submit", "export": "#export"}


def test_browser_controller_exposes_a_download_marker_and_waiter(tmp_path: Path, monkeypatch):
    controller = BrowserController(tmp_path)
    calls = []

    def fake_call(action, **kwargs):
        calls.append((action, kwargs))
        return {"provider": "existing-edge/extension", "download_id": 42, "filename": "C:/Downloads/report.csv", "state": "complete"}

    monkeypatch.setattr(controller, "_extension_call", fake_call)
    monkeypatch.setattr(controller, "_extension_available", lambda **_kwargs: True)

    assert controller.mark_download() == 42
    assert controller.wait_for_download(42, timeout_seconds=12) == Path("C:/Downloads/report.csv")
    assert calls[0][0] == "download_mark"
    assert calls[1] == ("download_wait", {"timeout": 24.0, "marker": 42, "timeout_ms": 12000})


def test_browser_research_actions_search_export_and_rename_without_per_keyword_approval(tmp_path: Path):
    source = tmp_path / "downloads" / "xray.csv"
    source.parent.mkdir()
    source.write_text("xray", encoding="utf-8")

    class Browser:
        current_url = "https://www.amazon.com/s?k=baby+sleep+sack"

        def _extension_available(self, **_kwargs): return True
        def inspect(self, *_args, **_kwargs): return '{"title":"Amazon search","url":"https://www.amazon.com/s?k=baby+sleep+sack"}'
        def fill(self, selector, value): return f"filled {selector} {value}"
        def click(self, selector): return f"clicked {selector}"
        def wait(self, milliseconds): return f"waited {milliseconds}"
        def mark_download(self): return 8
        def wait_for_download(self, marker, timeout_seconds): return source

    tools = ToolRegistry(tmp_path, approval_mode="safe")
    tools.browser_controller = Browser()

    assert "baby sleep sack" in tools.tool_browser("research_start", name="Baby Run", value="baby sleep sack")
    assert "baby sleep sack" in tools.tool_browser("research_search", name="Baby Run", selector="#twotabsearchtextbox", value="#nav-search-submit-button")
    result = tools.tool_browser("research_export", name="Baby Run", selector="#xray-export", milliseconds=12)

    assert "baby-sleep-sack.csv" in result
    assert (tmp_path / ".advertpreneur" / "research" / "baby-run" / "reports" / "baby-sleep-sack.csv").is_file()
    assert ResearchRun.open(tmp_path, "Baby Run").selectors() == {
        "search": "#twotabsearchtextbox", "submit": "#nav-search-submit-button", "export": "#xray-export"
    }


def test_browser_research_resume_action_requeues_a_paused_keyword(tmp_path: Path):
    tools = ToolRegistry(tmp_path)
    tools.browser_controller = type("Browser", (), {"_extension_available": lambda _self, **_kwargs: True})()
    tools.tool_browser("research_start", name="Baby Run", value="baby sleep sack")
    run = ResearchRun.open(tmp_path, "Baby Run")
    assert run.next_keyword() == "baby sleep sack"
    run.pause("baby sleep sack", "verification required")

    assert "resumed" in tools.tool_browser("research_resume", name="Baby Run", value="baby sleep sack")
    assert run.next_keyword() == "baby sleep sack"


def test_amazon_xray_task_exposes_browser_research_actions(tmp_path: Path):
    schemas = ToolRegistry(tmp_path).schemas("Generate Amazon keywords then export an Xray report from Helium 10")
    browser = next(item for item in schemas if item["function"]["name"] == "browser")

    assert "research_export" in browser["function"]["parameters"]["properties"]["action"]["enum"]


def test_external_provider_contract_instructs_research_checkpoint_handling():
    contract = ExternalActionGateway.contract().lower()

    assert "research_start" in contract
    assert "verification" in contract


def test_browser_research_run_processes_a_saved_selector_queue(tmp_path: Path):
    downloads = []
    for index in range(2):
        source = tmp_path / "downloads" / f"xray-{index}.csv"
        source.parent.mkdir(exist_ok=True)
        source.write_text(f"xray {index}", encoding="utf-8")
        downloads.append(source)

    class Browser:
        current_url = "https://www.amazon.com/s"

        def _extension_available(self, **_kwargs): return True
        def inspect(self, *_args, **_kwargs): return '{"title":"Amazon search","url":"https://www.amazon.com/s"}'
        def fill(self, *_args): return "filled"
        def click(self, *_args): return "clicked"
        def wait(self, *_args): return "waited"
        def mark_download(self): return 9
        def wait_for_download(self, *_args, **_kwargs): return downloads.pop(0)

    tools = ToolRegistry(tmp_path, approval_mode="safe")
    tools.browser_controller = Browser()
    tools.tool_browser("research_start", name="Baby Run", value="baby sleep sack\nbaby swaddle")
    ResearchRun.open(tmp_path, "Baby Run").remember_selectors(search="#search", submit="#submit", export="#export")

    result = tools.tool_browser("research_run", name="Baby Run", milliseconds=250)

    assert "2 completed" in result
    assert ResearchRun.open(tmp_path, "Baby Run").status()["completed"] == ["baby sleep sack", "baby swaddle"]
