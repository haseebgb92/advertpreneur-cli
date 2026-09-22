#!/usr/bin/env python3
"""Add native /adp routing-mode commands to the pinned Codex TUI."""

from __future__ import annotations

import pathlib
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one baseline match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_0004.py /path/to/codex")

    root = pathlib.Path(sys.argv[1]).resolve()
    slash_command = root / "codex-rs/tui/src/slash_command.rs"
    slash_dispatch = root / "codex-rs/tui/src/chatwidget/slash_dispatch.rs"
    settings = root / "codex-rs/tui/src/chatwidget/settings.rs"

    command_text = slash_command.read_text(encoding="utf-8")
    command_text = replace_once(
        command_text,
        "    Model,\n    Ide,",
        "    Model,\n    Adp,\n    Ide,",
        "slash enum",
    )
    command_text = replace_once(
        command_text,
        '            SlashCommand::Model => "choose what model and reasoning effort to use",\n',
        '            SlashCommand::Model => "choose what model and reasoning effort to use",\n'
        '            SlashCommand::Adp => "choose ADP routing: auto, economy, balanced, or max",\n',
        "slash description",
    )
    command_text = replace_once(
        command_text,
        "            SlashCommand::Review\n",
        "            SlashCommand::Adp\n                | SlashCommand::Review\n",
        "slash inline args",
    )
    command_text = replace_once(
        command_text,
        "            | SlashCommand::Review\n            | SlashCommand::Plan",
        "            | SlashCommand::Review\n            | SlashCommand::Adp\n            | SlashCommand::Plan",
        "slash active-task policy",
    )
    command_text = replace_once(
        command_text,
        "    fn auto_review_command_is_approve() {",
        """    fn adp_supports_inline_routing_modes() {
        assert_eq!(SlashCommand::Adp.command(), "adp");
        assert!(SlashCommand::Adp.supports_inline_args());
        assert!(!SlashCommand::Adp.available_during_task());
    }

    #[test]
    fn auto_review_command_is_approve() {""",
        "slash test",
    )
    slash_command.write_text(command_text, encoding="utf-8")

    settings_text = settings.read_text(encoding="utf-8")
    settings_text = replace_once(
        settings_text,
        "    fn submit_collaboration_mode_settings_update(&self) {",
        "    pub(crate) fn submit_collaboration_mode_settings_update(&self) {",
        "settings submit visibility",
    )
    settings.write_text(settings_text, encoding="utf-8")

    dispatch_text = slash_dispatch.read_text(encoding="utf-8")
    dispatch_text = replace_once(
        dispatch_text,
        """            SlashCommand::Model => {
                self.open_model_popup();
                self.defer_input_until_settings_applied();
            }
            SlashCommand::Plan => {""",
        """            SlashCommand::Model => {
                self.open_model_popup();
                self.defer_input_until_settings_applied();
            }
            SlashCommand::Adp => {
                let mode = self
                    .current_model()
                    .strip_prefix("adp/")
                    .unwrap_or("manual");
                self.add_info_message(
                    format!("ADP routing mode: {mode}"),
                    Some("Usage: /adp auto|economy|balanced|max".to_string()),
                );
            }
            SlashCommand::Plan => {""",
        "bare adp dispatch",
    )
    dispatch_text = replace_once(
        dispatch_text,
        """        match cmd {
            SlashCommand::Export if trimmed.is_empty() => self.show_transcript_export_popup(),""",
        """        match cmd {
            SlashCommand::Adp => {
                let mode = trimmed.to_ascii_lowercase();
                let target = match mode.as_str() {
                    "auto" => "adp/auto",
                    "economy" => "adp/economy",
                    "balanced" => "adp/balanced",
                    "max" => "adp/max",
                    _ => {
                        self.add_error_message(
                            "Usage: /adp auto|economy|balanced|max".to_string(),
                        );
                        return;
                    }
                };
                self.set_model(target);
                self.submit_collaboration_mode_settings_update();
                self.add_info_message(
                    format!("ADP routing mode changed to {mode}."),
                    Some("Use /model to pin an exact provider model instead.".to_string()),
                );
            }
            SlashCommand::Export if trimmed.is_empty() => self.show_transcript_export_popup(),""",
        "adp args dispatch",
    )
    dispatch_text = replace_once(
        dispatch_text,
        "            | SlashCommand::Model\n            | SlashCommand::Plan",
        "            | SlashCommand::Model\n            | SlashCommand::Adp\n            | SlashCommand::Plan",
        "queued adp policy",
    )
    slash_dispatch.write_text(dispatch_text, encoding="utf-8")


if __name__ == "__main__":
    main()
