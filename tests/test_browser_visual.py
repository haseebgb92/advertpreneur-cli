from pathlib import Path

from advertpreneur_cli.browser_visual import BrowserVisualMemory


def test_visual_memory_emits_each_state_once(tmp_path: Path):
    memory = BrowserVisualMemory(tmp_path)

    first = memory.observe("access", "https://members.softzilla.net/login", "Softzilla", [{"label": "Login"}])
    again = memory.observe("access", "https://members.softzilla.net/login", "Softzilla", [{"label": "Login"}])

    assert first.capture_screenshot is True
    assert again.capture_screenshot is False


def test_visual_memory_reuses_only_matching_safe_mapping(tmp_path: Path):
    memory = BrowserVisualMemory(tmp_path)
    memory.learn("access", "fingerprint", "open helium", "Helium10 Diamond Access", "button[data-tool='helium']")

    assert memory.recall("access", "fingerprint", "open helium") == "button[data-tool='helium']"
    assert memory.recall("access", "other", "open helium") == ""
