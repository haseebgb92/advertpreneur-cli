import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from advertpreneur_cli.checkpoints import CheckpointManager


def test_nongit_checkpoint_undo():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.txt").write_text("before", encoding="utf-8")
        mgr = CheckpointManager(root)
        mgr.begin("edit")
        (root / "a.txt").write_text("after", encoding="utf-8")
        (root / "new.txt").write_text("new", encoding="utf-8")
        cp = mgr.finalize()
        assert cp and set(cp.changed_files) == {"a.txt", "new.txt"}
        mgr.undo_latest()
        assert (root / "a.txt").read_text(encoding="utf-8") == "before"
        assert not (root / "new.txt").exists()


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_git_checkpoint_does_not_create_commit_and_undoes():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
        (root / "a.txt").write_text("before", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
        before_head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        mgr = CheckpointManager(root)
        mgr.begin("edit")
        (root / "a.txt").write_text("after", encoding="utf-8")
        cp = mgr.finalize()
        assert cp
        mgr.undo_latest()
        after_head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        assert after_head == before_head
        assert (root / "a.txt").read_text(encoding="utf-8") == "before"


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_active_git_checkpoint_reports_live_file_stats():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "a.txt").write_text("before\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        mgr = CheckpointManager(root)
        mgr.begin("edit")
        (root / "a.txt").write_text("after\nextra\n", encoding="utf-8")
        (root / "new.txt").write_text("new\n", encoding="utf-8")

        rows = mgr.live_changes() if hasattr(mgr, "live_changes") else []

        assert ("a.txt", 2, 1) in rows
        assert ("new.txt", 0, 0) in rows
