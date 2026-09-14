from pathlib import Path
from advertpreneur_cli.tools import ToolRegistry
from advertpreneur_cli.operation_router import LocalOperationRouter


def test_tools_allows_desktop_path(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path)
    desktop_file = Path.home() / "Desktop" / "test_sample.html"
    resolved = registry._path(str(desktop_file))
    assert resolved == desktop_file.resolve()


def test_operation_router_allows_desktop_path(tmp_path: Path) -> None:
    router = LocalOperationRouter(tmp_path)
    desktop_file = Path.home() / "Desktop" / "test_sample.html"
    resolved = router.assert_contained(str(desktop_file))
    assert resolved == desktop_file.resolve()
