from __future__ import annotations

import shutil
import zipfile
from pathlib import Path


class WorkspaceError(RuntimeError):
    pass


class WordPressWorkspace:
    """A project-local staging area for browser uploads and archive preparation."""

    def __init__(self, project: Path) -> None:
        self.root = (Path(project).resolve() / ".advertpreneur" / "wp-files").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative: str | Path) -> Path:
        candidate = (self.root / Path(relative)).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspaceError("Path must stay inside .advertpreneur/wp-files")
        return candidate

    def stage(self, source: str | Path, name: str = "") -> Path:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise WorkspaceError(f"Source file does not exist: {source_path}")
        target = self.resolve(name or source_path.name)
        if target == self.root:
            raise WorkspaceError("A staged file name is required")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
        return target

    def move(self, source: str | Path, destination: str | Path) -> Path:
        from_path = self.resolve(source)
        to_path = self.resolve(destination)
        if not from_path.exists():
            raise WorkspaceError(f"Workspace item does not exist: {source}")
        to_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(from_path), str(to_path))
        return to_path

    def create_zip(self, source: str | Path, archive_name: str) -> Path:
        source_path = self.resolve(source)
        if not source_path.exists():
            raise WorkspaceError(f"Workspace item does not exist: {source}")
        archive_path = self.resolve(archive_name)
        if archive_path.suffix.lower() != ".zip":
            raise WorkspaceError("Archive name must end in .zip")
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            if source_path.is_file():
                archive.write(source_path, source_path.name)
            else:
                for item in sorted(source_path.rglob("*")):
                    if item.is_file():
                        archive.write(item, item.relative_to(source_path.parent).as_posix())
        return archive_path

    def extract(self, archive: str | Path, destination: str | Path) -> Path:
        archive_path = self.resolve(archive)
        destination_path = self.resolve(destination)
        if archive_path.suffix.lower() != ".zip" or not archive_path.is_file():
            raise WorkspaceError("A staged .zip archive is required")
        if destination_path.exists():
            raise WorkspaceError(f"Extraction destination already exists: {destination}")
        with zipfile.ZipFile(archive_path) as contents:
            for member in contents.infolist():
                member_path = (destination_path / member.filename).resolve()
                if member_path != destination_path and destination_path not in member_path.parents:
                    raise WorkspaceError("Archive contains an unsafe path")
            contents.extractall(destination_path)
        return destination_path

    def list_files(self) -> list[str]:
        return [item.relative_to(self.root).as_posix() for item in sorted(self.root.rglob("*")) if item.is_file()]
