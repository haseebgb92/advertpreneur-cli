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
