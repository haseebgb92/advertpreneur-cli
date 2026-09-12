from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List


CONTRACT_VERSION = 1


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _package_scripts(root: Path) -> dict[str, str]:
    pkg = _read_json(root / "package.json")
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    return {str(k): str(v) for k, v in scripts.items()}


def _wp_header(path: Path) -> dict[str, str]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:12000]
    except OSError:
        return {}
    out: dict[str, str] = {}
    for key in ("Theme Name", "Plugin Name", "Version", "Text Domain"):
        m = re.search(rf"(?im)^\s*(?:/\*+\s*)?{re.escape(key)}\s*:\s*(.+?)\s*$", head)
        if m:
            out[key.lower().replace(" ", "_")] = m.group(1).strip().strip("*/ ")
    return out


@dataclass
class ProjectContractData:
    version: int
    kind: str
    name: str
    frameworks: List[str]
    important_files: List[str]
    protected_paths: List[str]
    generated_paths: List[str]
    build_commands: List[str]
    validation_commands: List[str]
    package_kind: str
    package_name: str
    package_excludes: List[str]
    source: str = "inferred"


class ProjectContract:
    """Deterministic project rules: important files, protected paths, validation and packaging.

    The inferred contract is local, compact, and costs zero model tokens. Users can override
    any inferred field with `.advertpreneur/project-contract.override.json` without teaching
    every provider the same project structure again.
    """

    def __init__(self, root: Path, frameworks: List[str] | None = None) -> None:
        self.root = root.resolve()
        self.store = self.root / ".advertpreneur"
        self.path = self.store / "project-contract.json"
        self.override_path = self.store / "project-contract.override.json"
        self.frameworks = list(frameworks or [])
        self.data = self.refresh()

    def _detect(self) -> ProjectContractData:
        root = self.root
        frameworks = list(dict.fromkeys(self.frameworks))
        scripts = _package_scripts(root)
        important: List[str] = []
        protected = [".git/", ".advertpreneur/", ".env", ".env.*", "node_modules/", "vendor/"]
        generated: List[str] = []
        build: List[str] = []
        validation: List[str] = []
        package_kind = "archive"
        package_name = root.name + ".zip"
        excludes = [
            ".git/", ".advertpreneur/", "node_modules/", ".venv/", "venv/", "__pycache__/",
            ".pytest_cache/", "coverage/", ".idea/", ".vscode/", ".github/", "tests/", "test/",
            "screenshots/", "benchmark_project/", "sample_project/", ".DS_Store", "*.log", "*.zip",
            "*.bak", "*.tmp", ".distignore", ".env", ".env.*", ".npmrc", ".pypirc", ".netrc",
            "credentials*.json", "secrets*.json", "service-account*.json", "*.pem", "*.key", "*.p12", "*.pfx",
        ]
        kind = "generic"
        name = root.name

        style_header = _wp_header(root / "style.css")
        plugin_headers: list[tuple[Path, dict[str, str]]] = []
        for p in list(root.glob("*.php"))[:40]:
            h = _wp_header(p)
            if h.get("plugin_name"):
                plugin_headers.append((p, h))

        if style_header.get("theme_name"):
            kind = "wordpress-theme"
            package_kind = "wordpress-theme"
            name = style_header.get("theme_name") or name
            slug = (style_header.get("text_domain") or root.name).strip() or root.name
            package_name = f"{slug}.zip"
            important += ["style.css"]
            for rel in ("functions.php", "theme.json", "index.php", "front-page.php", "header.php", "footer.php"):
                if (root / rel).exists(): important.append(rel)
            for rel in ("template-parts", "templates", "patterns", "assets", "inc", "woocommerce"):
                if (root / rel).exists(): important.append(rel + "/")
            protected += ["wp-admin/", "wp-includes/"]
            validation.append("php -l {changed_php}")
        elif plugin_headers:
            kind = "wordpress-plugin"
            package_kind = "wordpress-plugin"
            main, h = plugin_headers[0]
            name = h.get("plugin_name") or name
            slug = h.get("text_domain") or root.name
            package_name = f"{slug}.zip"
            important.append(main.name)
            for rel in ("includes", "src", "assets", "templates", "languages"):
                if (root / rel).exists(): important.append(rel + "/")
            protected += ["wp-admin/", "wp-includes/"]
            validation.append("php -l {changed_php}")
        elif "android" in frameworks:
            kind = "android"
            package_kind = "android-project"
            package_name = root.name + ".zip"
            for rel in ("settings.gradle.kts", "settings.gradle", "build.gradle.kts", "build.gradle", "gradle/libs.versions.toml", "app/src/main/AndroidManifest.xml"):
                if (root / rel).exists(): important.append(rel)
            if (root / "gradlew.bat").exists():
                build.append("gradlew.bat assembleDebug")
                validation.append("gradlew.bat test")
            elif (root / "gradlew").exists():
                build.append("./gradlew assembleDebug")
                validation.append("./gradlew test")
            generated += [".gradle/", "build/", "app/build/"]
            excludes += [".gradle/", "**/build/"]
        elif "shopify" in frameworks:
            kind = "shopify-theme"
            package_kind = "shopify-theme"
            for rel in ("layout/theme.liquid", "config/settings_schema.json", "templates/", "sections/", "snippets/", "assets/"):
                if (root / rel.rstrip("/")).exists(): important.append(rel)
        elif "nextjs" in frameworks:
            kind = "nextjs"
            for rel in ("package.json", "next.config.js", "next.config.mjs", "next.config.ts", "app/", "pages/", "src/"):
                if (root / rel.rstrip("/")).exists(): important.append(rel)
            generated += [".next/", "out/"]
            excludes += [".next/"]
        elif "nestjs" in frameworks:
            kind = "nestjs"
            for rel in ("package.json", "nest-cli.json", "src/", "test/"):
                if (root / rel.rstrip("/")).exists(): important.append(rel)
            generated += ["dist/"]
        elif (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
            kind = "python"
            for rel in ("pyproject.toml", "requirements.txt", "src/", "tests/"):
                if (root / rel.rstrip("/")).exists(): important.append(rel)
            validation.append("python -m py_compile {changed_python}")
        elif "web" in frameworks or (root / "index.html").exists():
            kind = "web"
            for rel in ("index.html", "package.json", "src/", "assets/"):
                if (root / rel.rstrip("/")).exists(): important.append(rel)

        if (root / "package.json").exists():
            pm = "pnpm" if (root / "pnpm-lock.yaml").exists() else ("yarn" if (root / "yarn.lock").exists() else "npm")
            def script_cmd(name: str) -> str:
                if pm == "yarn": return f"yarn {name}"
                return f"{pm} run {name}" if name != "test" or pm != "npm" else "npm test"
            if "build" in scripts: build.append(script_cmd("build"))
            if "lint" in scripts: validation.append(script_cmd("lint"))
            if "typecheck" in scripts: validation.append(script_cmd("typecheck"))
            elif "type-check" in scripts: validation.append(script_cmd("type-check"))
            if "test" in scripts and scripts.get("test") and "no test specified" not in scripts.get("test", "").lower():
                validation.append(script_cmd("test"))

        for rel in ("package.json", "composer.json", "pyproject.toml", "README.md", ".distignore"):
            if (root / rel).exists() and rel not in important:
                important.append(rel)

        # A .distignore is an explicit packaging contract and should be respected.
        distignore = root / ".distignore"
        if distignore.exists():
            try:
                for line in distignore.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        excludes.append(line)
            except OSError:
                pass

        return ProjectContractData(
            CONTRACT_VERSION, kind, name, frameworks, list(dict.fromkeys(important)),
            list(dict.fromkeys(protected)), list(dict.fromkeys(generated)), list(dict.fromkeys(build)),
            list(dict.fromkeys(validation)), package_kind, package_name, list(dict.fromkeys(excludes)),
        )

    def refresh(self) -> ProjectContractData:
        inferred = asdict(self._detect())
        override = _read_json(self.override_path) if self.override_path.exists() else {}
        # Overrides replace whole fields deliberately; inferred values remain for omitted fields.
        for key, value in override.items():
            if key in inferred and key not in {"version", "source"}:
                inferred[key] = value
        inferred["source"] = "inferred+override" if override else "inferred"
        self.store.mkdir(parents=True, exist_ok=True)
        try:
            self.path.write_text(json.dumps(inferred, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return ProjectContractData(**inferred)

    def context(self, max_chars: int = 1700) -> str:
        d = self.data
        rows = [f"Project contract (local/inferred): {d.kind} · {d.name}"]
        if d.important_files:
            rows.append("Important paths: " + ", ".join(d.important_files[:14]))
        if d.protected_paths:
            rows.append("Protected/do not edit unless explicitly required: " + ", ".join(d.protected_paths[:10]))
        if d.generated_paths:
            rows.append("Generated outputs/avoid direct edits: " + ", ".join(d.generated_paths[:8]))
        if d.build_commands:
            rows.append("Build: " + " ; ".join(d.build_commands[:3]))
        if d.validation_commands:
            rows.append("Validation: " + " ; ".join(d.validation_commands[:4]))
        if d.package_kind != "archive":
            rows.append(f"Package: {d.package_kind} → {d.package_name}; exclude development/runtime files from the contract.")
        return "\n".join(rows)[:max_chars]

    def summary(self) -> str:
        return f"{self.data.kind} · {len(self.data.important_files)} important path(s) · package {self.data.package_name}"

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self.data)
