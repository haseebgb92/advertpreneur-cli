from __future__ import annotations

import json
import zipfile
from pathlib import Path

from advertpreneur_cli.diff_intelligence import DiffIntelligence
from advertpreneur_cli.health import HealthMonitor
from advertpreneur_cli.notifications import TaskNotifier
from advertpreneur_cli.packaging import ProjectPackager
from advertpreneur_cli.project_contract import ProjectContract
from advertpreneur_cli.project_index import ProjectIndex
from advertpreneur_cli.task_planner import LocalTaskPlanner
from advertpreneur_cli.verification import ProportionalVerifier
from advertpreneur_cli.tui import COMMANDS


def _wp_theme(root: Path) -> ProjectContract:
    (root / "style.css").write_text("/*\nTheme Name: Demo Theme\nVersion: 1.2.3\nText Domain: demo-theme\n*/\n", encoding="utf-8")
    (root / "functions.php").write_text("<?php\nadd_action('wp_enqueue_scripts', function () {});\n", encoding="utf-8")
    (root / "template-parts").mkdir()
    return ProjectContract(root, ["wordpress"])


def test_beacon_is_removed_from_runtime_and_package_source():
    import advertpreneur_cli.notifications as notifications
    asset = Path(notifications.__file__).resolve().parent / "assets" / "beacon.ps1"
    assert not asset.exists()
    notifier = TaskNotifier(Path.cwd(), lambda: Path.cwd())
    assert not hasattr(notifier, "ensure_beacon")
    assert not hasattr(notifier, "sessions_dir")


def test_project_contract_understands_wordpress_theme(tmp_path: Path):
    contract = _wp_theme(tmp_path)
    d = contract.data
    assert d.kind == "wordpress-theme"
    assert d.package_name == "demo-theme.zip"
    assert "style.css" in d.important_files
    assert "functions.php" in d.important_files
    assert ".advertpreneur/" in d.protected_paths
    assert any("php -l" in x for x in d.validation_commands)


def test_project_contract_override_is_persistent(tmp_path: Path):
    contract = _wp_theme(tmp_path)
    contract.override_path.write_text(json.dumps({"important_files": ["style.css", "custom.php"], "package_name": "custom.zip"}), encoding="utf-8")
    contract.data = contract.refresh()
    assert contract.data.package_name == "custom.zip"
    assert contract.data.important_files == ["style.css", "custom.php"]


def test_task_planner_keeps_simple_task_lean(tmp_path: Path):
    contract = _wp_theme(tmp_path)
    idx = ProjectIndex(tmp_path); idx.build()
    plan = LocalTaskPlanner(contract, idx).plan("change the button color in style.css")
    assert plan.complexity == "low"
    assert not plan.needs_mcp
    assert not plan.needs_web
    assert not plan.needs_plugins
    assert "style.css" in plan.likely_files
    assert plan.framework_chars <= 700


def test_task_planner_escalates_release_work(tmp_path: Path):
    contract = _wp_theme(tmp_path)
    idx = ProjectIndex(tmp_path); idx.build()
    plan = LocalTaskPlanner(contract, idx).plan("build, verify and package the WordPress theme for release")
    assert plan.wants_package
    assert plan.wants_full_validation
    assert plan.task_class == "release/package"


def test_wordpress_package_has_slug_folder_and_excludes_runtime_state(tmp_path: Path):
    contract = _wp_theme(tmp_path)
    (tmp_path / ".env").write_text("SECRET=yes", encoding="utf-8")
    (tmp_path / "node_modules").mkdir(); (tmp_path / "node_modules" / "x.js").write_text("x", encoding="utf-8")
    packager = ProjectPackager(tmp_path, contract)
    out = tmp_path.parent / "out.zip"
    result = packager.create(out)
    assert not result.warnings
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "demo-theme/style.css" in names
    assert "demo-theme/functions.php" in names
    assert not any(".env" in n for n in names)
    assert not any("node_modules" in n for n in names)
    assert not any(".advertpreneur" in n for n in names)


def test_package_secret_guard_survives_override(tmp_path: Path):
    contract = _wp_theme(tmp_path)
    contract.data.package_excludes = []
    (tmp_path / "credentials.json").write_text('{"token":"secret"}', encoding="utf-8")
    out = tmp_path.parent / "out.zip"
    result = ProjectPackager(tmp_path, contract).create(out)
    assert any("credentials.json" in w for w in result.warnings)
    with zipfile.ZipFile(out) as zf:
        assert not any(n.endswith("credentials.json") for n in zf.namelist())


def test_python_safe_verification_does_not_create_pycache(tmp_path: Path):
    contract = ProjectContract(tmp_path, [])
    p = tmp_path / "demo.py"; p.write_text("x = 1\n", encoding="utf-8")
    results = ProportionalVerifier(tmp_path, contract).run(["demo.py"])
    assert results and all(r.ok for r in results)
    assert not (tmp_path / "__pycache__").exists()


def test_diff_intelligence_flags_sensitive_paths(tmp_path: Path):
    rows = DiffIntelligence(tmp_path).analyze(["functions.php", "assets/style.css"])
    assert rows[0].risk == "high"
    assert rows[1].risk == "low"


def test_health_monitor_is_on_demand_and_reports_current_process(tmp_path: Path):
    h = HealthMonitor(tmp_path / "app", tmp_path)
    own = h.own_process()
    assert own.pid > 0
    assert own.ram_mb >= 0
    assert not hasattr(h, "start")


def test_maturity_commands_are_in_palette():
    values = {x.value for x in COMMANDS}
    assert {"/health", "/contract", "/taskplan", "/verify", "/package"} <= values


def test_project_contract_uses_pnpm_when_lockfile_exists(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts":{"build":"vite build","lint":"eslint .","test":"vitest"}}), encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    contract = ProjectContract(tmp_path, ["web"])
    assert "pnpm run build" in contract.data.build_commands
    assert "pnpm run lint" in contract.data.validation_commands
    assert "pnpm run test" in contract.data.validation_commands


def test_default_package_contract_excludes_dev_folders(tmp_path: Path):
    contract = _wp_theme(tmp_path)
    excludes = set(contract.data.package_excludes)
    assert {"tests/", ".github/", "screenshots/", "node_modules/", ".advertpreneur/"} <= excludes


def test_telemetry_does_not_double_count_provider_run_and_task(tmp_path: Path):
    from advertpreneur_cli.telemetry import HarnessTelemetry
    tel = HarnessTelemetry(tmp_path)
    tel.record("provider_run", provider="agy", purpose="coding", input_tokens=100, output_tokens=10, ok=True)
    tel.record("task", provider="agy", input_tokens=100, output_tokens=10, provider_turns=1, tool_calls=2, duration_seconds=3.0, adp_ram_end_mb=25.0, ok=True)
    text = tel.summary("today")
    assert "100 in" in text
    assert "200 in" not in text
    assert "2 tools" in text
    assert "ADP RAM up to 25.0 MB" in text


def test_wordpress_plugin_word_does_not_load_provider_plugins(tmp_path: Path):
    (tmp_path / "demo.php").write_text("<?php\n/* Plugin Name: Demo Plugin\nText Domain: demo-plugin\n*/\n", encoding="utf-8")
    contract = ProjectContract(tmp_path, ["wordpress"])
    idx = ProjectIndex(tmp_path); idx.build()
    plan = LocalTaskPlanner(contract, idx).plan("fix the WordPress plugin activation notice in demo.php")
    assert contract.data.kind == "wordpress-plugin"
    assert not plan.needs_plugins


def test_generated_paths_are_exposed_in_contract_context(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts":{"build":"next build"}}), encoding="utf-8")
    contract = ProjectContract(tmp_path, ["nextjs"])
    assert ".next/" in contract.data.generated_paths
    assert "Generated outputs/avoid direct edits" in contract.context()


def test_package_secret_guard_excludes_common_token_files(tmp_path: Path):
    contract = _wp_theme(tmp_path)
    (tmp_path / ".npmrc").write_text("//registry.npmjs.org/:_authToken=secret\n", encoding="utf-8")
    (tmp_path / "service-account-prod.json").write_text('{"private_key":"secret"}', encoding="utf-8")
    out = tmp_path.parent / "out-secrets.zip"
    result = ProjectPackager(tmp_path, contract).create(out)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert not any(n.endswith("/.npmrc") or n == ".npmrc" for n in names)
    assert not any("service-account-prod.json" in n for n in names)
