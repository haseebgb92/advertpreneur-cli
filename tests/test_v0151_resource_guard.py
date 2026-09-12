from __future__ import annotations

import json
import os
from pathlib import Path

from advertpreneur_cli.resource_guard import ResourceGuard, SystemResources
from advertpreneur_cli.tools import ToolRegistry


def _guard(tmp_path: Path) -> ResourceGuard:
    return ResourceGuard(tmp_path / "app", tmp_path / "project", "session-a")


def test_pressure_bands_are_deterministic(tmp_path: Path):
    g = _guard(tmp_path)
    assert g._pressure(50) == "green"
    assert g._pressure(35) == "green"
    assert g._pressure(34.9) == "yellow"
    assert g._pressure(20) == "yellow"
    assert g._pressure(19.9) == "orange"
    assert g._pressure(12) == "orange"
    assert g._pressure(11.9) == "red"
    g.close()


def test_red_pressure_blocks_new_heavy_but_not_provider_work(tmp_path: Path, monkeypatch):
    g = _guard(tmp_path)
    monkeypatch.setattr(g, "system_snapshot", lambda cpu=False: SystemResources(16000, 1200, 7.5, 20, "red"))
    assert not g.preflight("build", cpu=True).allowed
    assert g.preflight("provider").allowed
    g.close()


def test_orange_pressure_defers_second_adp_heavy_job(tmp_path: Path, monkeypatch):
    g = _guard(tmp_path)
    other = g.instances_dir / "99999.json"
    other.write_text(json.dumps({"pid": 99999, "status": "working", "heavy": True, "own_ram_mb": 30, "child_ram_mb": 300}), encoding="utf-8")
    monkeypatch.setattr(g, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(g, "system_snapshot", lambda cpu=False: SystemResources(16000, 2400, 15, 50, "orange"))
    d = g.preflight("build", cpu=True)
    assert not d.allowed
    assert d.active_heavy_elsewhere == 1
    g.close()


def test_green_pressure_allows_parallel_heavy_job(tmp_path: Path, monkeypatch):
    g = _guard(tmp_path)
    other = g.instances_dir / "99999.json"
    other.write_text(json.dumps({"pid": 99999, "status": "working", "heavy": True}), encoding="utf-8")
    monkeypatch.setattr(g, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(g, "system_snapshot", lambda cpu=False: SystemResources(16000, 8000, 50, 30, "green"))
    assert g.preflight("build", cpu=True).allowed
    g.close()


def test_dead_adp_instance_records_are_pruned(tmp_path: Path, monkeypatch):
    g = _guard(tmp_path)
    stale = g.instances_dir / "12345.json"
    stale.write_text(json.dumps({"pid": 12345, "status": "working", "heavy": True}), encoding="utf-8")
    original = g._pid_alive
    monkeypatch.setattr(g, "_pid_alive", lambda pid: pid == os.getpid())
    rows = g.records()
    assert all(int(x["pid"]) != 12345 for x in rows)
    assert not stale.exists()
    monkeypatch.setattr(g, "_pid_alive", original)
    g.close()


def test_update_preserves_last_observed_ram_during_heavy_transition(tmp_path: Path):
    g = _guard(tmp_path)
    g.update(status="idle", own_ram_mb=25, child_ram_mb=150)
    g.update(status="working", operation="build", heavy=True)
    row = json.loads(g.path.read_text(encoding="utf-8"))
    assert row["own_ram_mb"] == 25
    assert row["child_ram_mb"] == 150
    g.close()


def test_tool_registry_marks_builds_as_heavy_without_marking_simple_commands(tmp_path: Path):
    assert ToolRegistry._resource_heavy_command("pnpm run build")
    assert ToolRegistry._resource_heavy_command(".\\gradlew test")
    assert ToolRegistry._resource_heavy_command("python -m pytest -q")
    assert not ToolRegistry._resource_heavy_command("git status --short")
    assert not ToolRegistry._resource_heavy_command("php -l functions.php")


def test_resource_guard_has_no_background_thread_or_start_method(tmp_path: Path):
    g = _guard(tmp_path)
    assert not hasattr(g, "start")
    assert not hasattr(g, "thread")
    g.close()
