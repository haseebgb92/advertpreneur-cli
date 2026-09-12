from __future__ import annotations

import atexit
import ctypes
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class SystemResources:
    total_ram_mb: float
    available_ram_mb: float
    available_percent: float
    cpu_percent: float
    pressure: str


@dataclass
class GuardDecision:
    allowed: bool
    pressure: str
    message: str = ""
    active_heavy_elsewhere: int = 0
    available_ram_mb: float = 0.0
    available_percent: float = 0.0


class _HeavyLease:
    def __init__(self, guard: "ResourceGuard", handle: Any = None, locked: bool = False, operation: str = "") -> None:
        self.guard = guard
        self.handle = handle
        self.locked = bool(locked)
        self.operation = operation
        self.closed = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.guard._unlock_heavy(self.handle, self.locked)
        finally:
            self.guard.update(status="working" if self.guard._task_active else "idle", operation="")

    def __enter__(self) -> "_HeavyLease":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


class ResourceGuard:
    """Low-overhead, transition-driven machine pressure guard.

    There is deliberately no sampler thread, daemon, watcher, or model call. Each ADP
    process writes one tiny JSON record only at lifecycle transitions. System RAM uses
    native OS APIs. Cross-instance coordination is via per-PID files and one advisory
    heavy-work lock.
    """

    GREEN_MIN = 35.0
    YELLOW_MIN = 20.0
    ORANGE_MIN = 12.0

    def __init__(self, app_dir: Path, project: Path, session_id: str = "") -> None:
        self.app_dir = Path(app_dir)
        self.project = Path(project)
        self.session_id = str(session_id or "")
        self.root = self.app_dir / "resource-guard"
        self.instances_dir = self.root / "instances"
        self.instances_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.instances_dir / f"{os.getpid()}.json"
        self.lock_path = self.root / "heavy-work.lock"
        self.enabled = True
        self.mode = "balanced"
        self._task_active = False
        self._last_cpu_sample: tuple[float, int, int, int] | None = None
        self.update(status="idle", operation="")
        atexit.register(self.close)

    @staticmethod
    def _pressure(available_percent: float) -> str:
        if available_percent >= ResourceGuard.GREEN_MIN:
            return "green"
        if available_percent >= ResourceGuard.YELLOW_MIN:
            return "yellow"
        if available_percent >= ResourceGuard.ORANGE_MIN:
            return "orange"
        return "red"

    @staticmethod
    def _memory_windows() -> tuple[float, float]:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        row = MEMORYSTATUSEX()
        row.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(row)):
            return 0.0, 0.0
        return row.ullTotalPhys / 1048576.0, row.ullAvailPhys / 1048576.0

    @staticmethod
    def _memory_posix() -> tuple[float, float]:
        try:
            page = os.sysconf("SC_PAGE_SIZE")
            total = os.sysconf("SC_PHYS_PAGES") * page / 1048576.0
            available = os.sysconf("SC_AVPHYS_PAGES") * page / 1048576.0
            return float(total), float(available)
        except Exception:
            return 0.0, 0.0

    @staticmethod
    def _filetime_value(ft: Any) -> int:
        return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)

    def _cpu_windows(self, sample_seconds: float = 0.05) -> float:
        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

        def read() -> tuple[int, int, int] | None:
            idle = FILETIME(); kernel = FILETIME(); user = FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                return None
            return self._filetime_value(idle), self._filetime_value(kernel), self._filetime_value(user)

        first = read()
        if not first:
            return 0.0
        time.sleep(max(0.0, min(0.15, sample_seconds)))
        second = read()
        if not second:
            return 0.0
        idle_delta = max(0, second[0] - first[0])
        kernel_delta = max(0, second[1] - first[1])
        user_delta = max(0, second[2] - first[2])
        total = kernel_delta + user_delta
        if total <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total)))

    def system_snapshot(self, *, cpu: bool = False) -> SystemResources:
        total, available = self._memory_windows() if os.name == "nt" else self._memory_posix()
        pct = (available / total * 100.0) if total > 0 else 100.0
        cpu_pct = self._cpu_windows() if (cpu and os.name == "nt") else 0.0
        if cpu and os.name != "nt":
            try:
                cpu_pct = min(100.0, os.getloadavg()[0] / max(1, os.cpu_count() or 1) * 100.0)
            except Exception:
                cpu_pct = 0.0
        return SystemResources(total, available, pct, cpu_pct, self._pressure(pct))

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if pid == os.getpid():
            return True
        if os.name == "nt":
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def update(self, *, status: str, operation: str = "", provider: str = "", own_ram_mb: float = 0.0,
               child_ram_mb: float = 0.0, heavy: bool = False, details: Dict[str, Any] | None = None) -> None:
        previous: Dict[str, Any] = {}
        if (own_ram_mb <= 0.0 or child_ram_mb <= 0.0) and self.path.exists():
            try:
                previous = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                previous = {}
        if own_ram_mb <= 0.0:
            own_ram_mb = float(previous.get("own_ram_mb") or 0.0)
        if child_ram_mb <= 0.0:
            child_ram_mb = float(previous.get("child_ram_mb") or 0.0)
        row = {
            "pid": os.getpid(),
            "session_id": self.session_id,
            "project": str(self.project),
            "status": str(status or "idle"),
            "operation": str(operation or ""),
            "provider": str(provider or ""),
            "heavy": bool(heavy),
            "own_ram_mb": round(float(own_ram_mb or 0.0), 1),
            "child_ram_mb": round(float(child_ram_mb or 0.0), 1),
            "updated_at": time.time(),
        }
        if details:
            row["details"] = dict(details)
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(row, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            pass

    def records(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            files = list(self.instances_dir.glob("*.json"))
        except Exception:
            files = []
        for path in files:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                pid = int(row.get("pid") or 0)
                if not self._pid_alive(pid):
                    try: path.unlink()
                    except Exception: pass
                    continue
                if isinstance(row, dict):
                    out.append(row)
            except Exception:
                try:
                    if path.stat().st_mtime < time.time() - 86400:
                        path.unlink()
                except Exception:
                    pass
        return sorted(out, key=lambda x: int(x.get("pid") or 0))

    def estate_summary(self) -> Dict[str, Any]:
        rows = self.records()
        return {
            "instances": len(rows),
            "working": sum(1 for x in rows if str(x.get("status")) == "working"),
            "heavy": sum(1 for x in rows if bool(x.get("heavy"))),
            "ram_mb": sum(float(x.get("own_ram_mb") or 0) + float(x.get("child_ram_mb") or 0) for x in rows),
            "rows": rows,
        }

    def preflight(self, operation: str, *, provider: str = "", cpu: bool = False) -> GuardDecision:
        if not self.enabled:
            return GuardDecision(True, "off")
        snap = self.system_snapshot(cpu=cpu)
        estate = self.estate_summary()
        others_heavy = sum(1 for x in estate["rows"] if int(x.get("pid") or 0) != os.getpid() and bool(x.get("heavy")))
        op = str(operation or "task").lower()
        heavy = op in {"heavy", "build", "test", "browser", "package", "full-verify"}
        allowed = True
        reason = ""
        # Active model/provider work is never killed. We only gate *new* optional/heavy work.
        if heavy and cpu and snap.cpu_percent >= 97.0 and others_heavy:
            allowed = False
            reason = f"Resource Guard deferred {op}: CPU is {snap.cpu_percent:.0f}% and another ADP is already running heavy work."
        elif heavy and snap.pressure == "red":
            allowed = False
            reason = f"Resource Guard blocked new {op}: only {snap.available_ram_mb:.0f} MB ({snap.available_percent:.0f}%) RAM is available."
        elif heavy and snap.pressure == "orange" and others_heavy:
            allowed = False
            reason = f"Resource Guard deferred {op}: another ADP is already running heavy work and RAM is at {snap.available_percent:.0f}% available."
        elif snap.pressure in {"orange", "red"}:
            reason = f"Resource pressure {snap.pressure.upper()} · {snap.available_ram_mb:.0f} MB ({snap.available_percent:.0f}%) RAM available."
        elif snap.pressure == "yellow" and heavy and others_heavy:
            reason = f"Resource Guard caution · another ADP is doing heavy work · {snap.available_percent:.0f}% RAM available."
        return GuardDecision(allowed, snap.pressure, reason, others_heavy, snap.available_ram_mb, snap.available_percent)

    def mark_task(self, active: bool, *, provider: str = "", operation: str = "provider", own_ram_mb: float = 0.0,
                  child_ram_mb: float = 0.0) -> None:
        self._task_active = bool(active)
        self.update(status="working" if active else "idle", operation=operation if active else "", provider=provider,
                    own_ram_mb=own_ram_mb, child_ram_mb=child_ram_mb, heavy=False)

    def _try_heavy_lock(self) -> tuple[Any, bool]:
        self.root.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if handle.tell() == 0:
                    handle.write(b"0"); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle, True
        except Exception:
            try: handle.close()
            except Exception: pass
            return None, False

    @staticmethod
    def _unlock_heavy(handle: Any, locked: bool) -> None:
        if not handle:
            return
        try:
            if locked and os.name == "nt":
                import msvcrt
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            elif locked:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try: handle.close()
        except Exception: pass

    def begin_heavy(self, operation: str) -> tuple[GuardDecision, _HeavyLease | None]:
        decision = self.preflight(operation, cpu=True)
        if not decision.allowed:
            return decision, None
        handle, locked = self._try_heavy_lock()
        # In green pressure we allow parallel heavy work if another instance already owns
        # the advisory lock. Under any pressure, serialize it to prevent freezes.
        if not locked and decision.pressure != "green":
            decision.allowed = False
            decision.message = decision.message or "Resource Guard deferred heavy work because another ADP already owns the heavy-work slot."
            return decision, None
        self.update(status="working", operation=operation, heavy=True)
        return decision, _HeavyLease(self, handle, locked, operation)

    def close(self) -> None:
        try:
            if self.path.exists():
                self.path.unlink()
        except Exception:
            pass
