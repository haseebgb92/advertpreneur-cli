import tempfile
from pathlib import Path

from advertpreneur_cli.project_index import ProjectIndex


def test_project_index_finds_credentials_without_model():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        (root / "src" / "auth.py").write_text(
            "def validate_credentials(email, password):\n    return email and password\n",
            encoding="utf-8",
        )
        idx = ProjectIndex(root)
        result = idx.build()
        assert result["cloud_tokens"] == 0
        hints = idx.hints("login credentials are failing")
        assert "src/auth.py" in hints
        assert "validate_credentials" in hints
        assert idx.map_path.exists()
        assert idx.memory_path.exists()


def test_project_index_incremental_reuses_unchanged():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.py").write_text("def one():\n    return 1\n", encoding="utf-8")
        idx = ProjectIndex(root)
        first = idx.build()
        second = idx.build()
        assert first["changed"] == 1
        assert second["changed"] == 0
        assert second["unchanged"] == 1


def test_project_index_fields_confidence():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")
        (root / "test_sample.py").write_text("def test_ok(): pass\n", encoding="utf-8")
        idx = ProjectIndex(root)
        idx.build()
        f = idx.fields()
        assert "project_type" in f
        assert f["project_type"].value == "python"
        assert f["project_type"].confidence == 1.0
        assert f["project_type"].inspected is True


def test_auto_index_does_not_block_tasks_when_workspace_is_home_directory():
    from advertpreneur_cli.cli import AdvertpreneurCLI

    cli = object.__new__(AdvertpreneurCLI)
    cli.auto_index = True
    cli.project = Path.home().resolve()
    cli.project_index = type("Index", (), {"ready": False, "build": lambda _self, force=False: (_ for _ in ()).throw(AssertionError("home directory must not be auto-indexed"))})()
    messages = []
    cli.ui = type("UI", (), {"muted": lambda _self, message: messages.append(message)})()

    cli._ensure_project_index()

    assert messages == ["Local index skipped: home-directory workspace; use /project for a codebase or /index to run it manually"]


def test_home_directory_workspace_disables_automatic_checkpoints():
    from advertpreneur_cli.cli import AdvertpreneurCLI

    cli = object.__new__(AdvertpreneurCLI)
    cli.project = Path.home().resolve()

    assert cli._automatic_checkpoint_allowed() is False
