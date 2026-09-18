#!/usr/bin/env python3
"""Patch pinned upstream Codex so the Unchained binary owns separate user state and branding."""

from __future__ import annotations

import sys
from pathlib import Path


def replace_exact(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text and old not in text:
        return
    if old not in text:
        raise SystemExit(f"{label}: expected source text not found in {path}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_0003.py <codex-upstream-root>")

    root = Path(sys.argv[1]).resolve()
    home = root / "codex-rs" / "utils" / "home-dir" / "src" / "lib.rs"
    cli = root / "codex-rs" / "cli" / "src" / "main.rs"

    if not home.is_file() or not cli.is_file():
        raise SystemExit("expected pinned Codex source tree was not found")

    replace_exact(
        home,
        "/// `~/.codex`.",
        "/// `~/.codex-unchained` for the Codex Unchained distribution.",
        "home-doc",
    )
    replace_exact(
        home,
        '            p.push(".codex");',
        '            p.push(".codex-unchained");',
        "home-default",
    )
    replace_exact(
        home,
        '        expected.push(".codex");',
        '        expected.push(".codex-unchained");',
        "home-test",
    )

    replace_exact(
        cli,
        "/// Codex CLI",
        "/// Codex Unchained CLI",
        "cli-title",
    )
    replace_exact(
        cli,
        '    // `codex-x86_64-unknown-linux-musl`, but the help output should always use\n'
        '    // the generic `codex` command name that users run.\n'
        '    bin_name = "codex",\n'
        '    override_usage = "codex [OPTIONS] [PROMPT]\\n       codex [OPTIONS] <COMMAND> [ARGS]"',
        '    // This distribution intentionally uses a separate command identity from upstream Codex.\n'
        '    bin_name = "codex-unchained",\n'
        '    override_usage = "codex-unchained [OPTIONS] [PROMPT]\\n       codex-unchained [OPTIONS] <COMMAND> [ARGS]"',
        "cli-brand",
    )


if __name__ == "__main__":
    main()
