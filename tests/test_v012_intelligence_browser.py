from pathlib import Path

from advertpreneur_cli.frameworks import FrameworkIntelligence
from advertpreneur_cli.handbook import ExperienceHandbook
from advertpreneur_cli.tools import ToolRegistry


def test_framework_detection_wordpress_and_android(tmp_path: Path):
    (tmp_path / "style.css").write_text("/*\nTheme Name: Demo\n*/", encoding="utf-8")
    (tmp_path / "android" / "app" / "src" / "main").mkdir(parents=True)
    (tmp_path / "android" / "app" / "src" / "main" / "AndroidManifest.xml").write_text("<manifest/>", encoding="utf-8")
    fw = FrameworkIntelligence(tmp_path)
    assert "wordpress" in fw.detected
    assert "android" in fw.detected
    ctx = fw.context("fix wordpress theme header")
    assert "WordPress" in ctx
    assert "template hierarchy" in ctx


def test_handbook_only_marks_validation_success_proven(tmp_path: Path):
    app = tmp_path / "state"
    project = tmp_path / "project"
    project.mkdir()
    hb = ExperienceHandbook(app, project)
    evidence = {"commands": [{"command": "python -m pytest", "exit_code": 0, "proof": ["12 passed"]}]}
    hb.learn_task("fix login validation", "Fixed login and tests pass.", "completed", evidence, ["auth.py"])
    rows = hb.search("login validation")
    assert rows
    assert rows[0]["status"] == "PROVEN"
    assert "pytest" in rows[0]["validation"]


def test_handbook_records_failed_recipe(tmp_path: Path):
    app = tmp_path / "state"
    project = tmp_path / "project"
    project.mkdir()
    hb = ExperienceHandbook(app, project)
    evidence = {"commands": [{"command": "gradlew badTask", "exit_code": 1, "proof": ["BUILD FAILED"]}]}
    hb.learn_task("build android", "Blocked", "failed", evidence, [])
    rows = hb.search("android build")
    assert any(x["status"] == "FAILED" for x in rows)


def test_browser_and_web_schemas_are_lazy(tmp_path: Path):
    tools = ToolRegistry(tmp_path)
    basic = {s["function"]["name"] for s in tools.schemas("fix auth bug")}
    assert "browser" not in basic
    assert "web_search" not in basic

    browser = {s["function"]["name"] for s in tools.schemas("open https://example.com in browser and reverse engineer the hero")}
    assert "browser" in browser

    research = {s["function"]["name"] for s in tools.schemas("look up official docs for this dependency version")}
    assert "web_search" in research
    assert "web_fetch" in research


def test_browser_tool_dispatch_without_real_browser(tmp_path: Path):
    tools = ToolRegistry(tmp_path, approval_mode="full")
    class FakeBrowser:
        running = True
        def navigate(self, url): return "NAV " + url
        def inspect(self, selector, max_elements=60): return "INSPECT " + selector
        def screenshot(self, name="page", selector="", full_page=True): return "SHOT " + name
        def reverse_engineer(self, selector="body", name="design-map", max_elements=120): return "MAP " + selector
        def click(self, selector): return "CLICK " + selector
        def fill(self, selector, value): return "FILL " + selector
        def close(self): return "CLOSED"
    tools.browser_controller = FakeBrowser()
    assert tools.tool_browser("navigate", url="https://example.com").startswith("NAV")
    assert tools.tool_browser("reverse_engineer", selector="#hero") == "MAP #hero"
    assert tools.tool_browser("screenshot", name="hero") == "SHOT hero"


def test_handbook_does_not_call_non_validation_command_proven(tmp_path: Path):
    app = tmp_path / "state"
    project = tmp_path / "project"
    project.mkdir()
    hb = ExperienceHandbook(app, project)
    evidence = {"commands": [{"command": "git status --short", "exit_code": 0, "proof": []}]}
    hb.learn_task("inspect repository state", "Repository inspected.", "completed", evidence, ["README.md"])
    rows = hb.search("repository state")
    assert rows
    assert rows[0]["status"] == "PROJECT-SPECIFIC"


def test_browser_worker_isolates_asyncio_from_cli_thread(tmp_path: Path):
    import asyncio
    import threading
    from advertpreneur_cli.browser_control import BrowserController

    class ProbeBrowser(BrowserController):
        def _status_direct(self):
            # Simulate a browser backend that owns/runs asyncio internally.
            asyncio.run(asyncio.sleep(0))
            return threading.current_thread().name

    ctl = ProbeBrowser(tmp_path, visible=False)
    worker_name = ctl._rpc("status", timeout=5)
    assert worker_name == "AdvertpreneurBrowser"
    # The CLI/main thread must still be free to let prompt_toolkit call asyncio.run.
    assert asyncio.run(asyncio.sleep(0, result="ok")) == "ok"
    ctl.close()


def test_browser_normalizes_composer_trailing_punctuation(tmp_path: Path):
    from advertpreneur_cli.browser_control import BrowserController
    assert BrowserController._normalize_url("https://www.taiyomunch.com,") == "https://www.taiyomunch.com"
    assert BrowserController._normalize_url("https://example.com/path;") == "https://example.com/path"


def test_browser_context_hint_reuses_current_page(tmp_path: Path):
    from advertpreneur_cli.browser_control import BrowserController
    ctl = BrowserController(tmp_path, visible=False)
    ctl._set_state(running=True, provider="existing-edge/cdp", url="https://www.taiyomunch.com", title="Taiyo")
    hint = ctl.context_hint()
    assert "taiyomunch.com" in hint
    assert "existing-edge/cdp" in hint
