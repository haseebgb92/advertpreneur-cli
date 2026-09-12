from __future__ import annotations

from dataclasses import asdict, dataclass


class PlaybookError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlaybookStep:
    action: str
    route: str = ""
    selector: str = ""
    verify: str = ""
    note: str = ""


@dataclass(frozen=True)
class PanelPlaybook:
    adapter: str
    name: str
    steps: tuple[PlaybookStep, ...]

    def export(self) -> dict:
        return {"adapter": self.adapter, "name": self.name, "steps": [asdict(step) for step in self.steps]}


class PanelPlaybookRegistry:
    """Prebuilt semantic routines. Callers must observe each step before advancing."""

    def __init__(self) -> None:
        file_steps = (
            PlaybookStep("navigate", "file_manager", verify="file manager visible", note="Open the selected hosting panel's file manager."),
            PlaybookStep("upload", selector='input[type="file"]', verify="selected filename visible", note="Use a staged archive only."),
            PlaybookStep("extract", selector='[data-action*="extract"], button[title*="Extract"], button:has-text("Extract")', verify="archive contents visible", note="Require explicit overwrite approval if files already exist."),
            PlaybookStep("verify", verify="target file tree or success notice visible", note="Do not claim deployment until the expected files are listed."),
        )
        self._rows: dict[tuple[str, str], PanelPlaybook] = {}
        for adapter in ("hostinger", "cpanel", "plesk"):
            self._rows[(adapter, "deploy_archive")] = PanelPlaybook(adapter, "deploy_archive", file_steps)
        self._rows[("wordpress", "upload_plugin")] = PanelPlaybook("wordpress", "upload_plugin", (
            PlaybookStep("navigate", "plugins", verify="Plugins page visible"),
            PlaybookStep("upload", selector='input[type="file"]', verify="selected filename visible", note="Open Add New / Upload Plugin before this step."),
            PlaybookStep("activate", selector='a.activate, a:has-text("Activate Plugin")', verify="Plugin activated notice or active row visible"),
            PlaybookStep("verify", verify="plugin appears active in plugin list"),
        ))
        self._rows[("wordpress", "upload_theme")] = PanelPlaybook("wordpress", "upload_theme", (
            PlaybookStep("navigate", "themes", verify="Themes page visible"),
            PlaybookStep("upload", selector='input[type="file"]', verify="selected filename visible"),
            PlaybookStep("activate", selector='a.activate, a:has-text("Activate")', verify="Theme active notice or active card visible"),
            PlaybookStep("verify", verify="theme appears active"),
        ))
        self._rows[("wordpress", "publish_post")] = PanelPlaybook("wordpress", "publish_post", (
            PlaybookStep("navigate", "posts", verify="Posts page visible"),
            PlaybookStep("edit", selector='a.page-title-action, a:has-text("Add New")', verify="editor visible"),
            PlaybookStep("publish", selector='button.editor-post-publish-button, input#publish', verify="published success notice or public permalink visible"),
            PlaybookStep("verify", verify="post listing shows published status"),
        ))

    def get(self, adapter: str, name: str) -> PanelPlaybook:
        row = self._rows.get((str(adapter).lower(), str(name).lower()))
        if not row: raise PlaybookError(f"No verified playbook for {adapter}: {name}")
        return row

    def names(self, adapter: str) -> list[str]:
        return sorted(name for (key, name) in self._rows if key == str(adapter).lower())
