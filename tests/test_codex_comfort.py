import tempfile
from pathlib import Path

from prompt_toolkit.document import Document

from advertpreneur_cli.sessions import SessionStore
from advertpreneur_cli.tui import COMMANDS, ComposerCompleter, WorkspaceIndex


def test_codex_comfort_commands_present():
    values = {x.value for x in COMMANDS}
    for cmd in {"/personality", "/goal", "/raw", "/mention", "/archive", "/unarchive", "/delete", "/config", "/debug-config", "/keymap", "/title", "/index", "/map", "/undo", "/checkpoints", "/agents", "/settings"}:
        assert cmd in values


def test_at_file_and_folder_completion():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text("print('ok')", encoding="utf-8")
        idx = WorkspaceIndex(lambda: root)
        c = ComposerCompleter(idx)
        values = [x.text for x in c.get_completions(Document("review @app"), None)]
        assert "@src/app.py" in values
        folders = [x.text for x in c.get_completions(Document("look @src"), None)]
        assert "@src/" in folders


def test_archive_and_restore_session():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = SessionStore(root / "sessions")
        rec = store.create(root, "cloud", "gpt-oss:20b")
        store.archive(rec.id, True)
        assert store.list() == []
        archived = store.list(include_archived=True)
        assert archived and archived[0].archived is True
        store.archive(rec.id, False)
        restored = store.load(rec.id)
        assert restored and restored.archived is False
