from pathlib import Path
from advertpreneur_cli.checkpoints import CheckpointManager, Checkpoint


def test_checkpoint_diff_git(tmp_path: Path) -> None:
    mgr = CheckpointManager(tmp_path)
    # Test diff formatting for zip mode or git mode checkpoint
    cp = Checkpoint(
        id="cp1234567890",
        created_at="2026-09-14T12:00:00Z",
        label="Test snapshot",
        mode="zip",
        ref=str(tmp_path / "dummy.zip"),
        changed_files=["file1.py", "file2.py"],
    )
    diff_text = mgr.diff(cp)
    assert "cp123456" in diff_text
    assert "2 changed file(s)" in diff_text
