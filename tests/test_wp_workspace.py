from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from advertpreneur_cli.wp_workspace import WordPressWorkspace, WorkspaceError


def test_stage_zip_extract_and_list_remain_inside_workspace(tmp_path: Path):
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("theme/style.css", "/* Theme Name: Test */")

    workspace = WordPressWorkspace(tmp_path / "project")
    staged = workspace.stage(source)

    assert staged.is_file()
    assert staged.parent == workspace.root
    extracted = workspace.extract(staged, "unpacked")
    assert (extracted / "theme" / "style.css").is_file()
    assert workspace.list_files() == ["source.zip", "unpacked/theme/style.css"]


def test_workspace_rejects_escaping_paths(tmp_path: Path):
    workspace = WordPressWorkspace(tmp_path / "project")
    with pytest.raises(WorkspaceError, match="inside"):
        workspace.resolve("../outside.zip")


def test_workspace_rejects_zip_slip_members(tmp_path: Path):
    source = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("../outside.txt", "no")
    workspace = WordPressWorkspace(tmp_path / "project")
    staged = workspace.stage(source)
    with pytest.raises(WorkspaceError, match="unsafe"):
        workspace.extract(staged, "unpacked")
