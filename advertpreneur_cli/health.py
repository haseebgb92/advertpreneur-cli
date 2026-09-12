from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class ProcessHealth:
    pid: int
    name: str
    ram_mb: float
    peak_mb: float = 0.0
    command: str = ""


@dataclass
class HealthSnapshot:
    own: ProcessHealth
    descendants: List[ProcessHealth]
    project_state_mb: float
    app_state_mb: float

    @property
    def managed_ram_mb(self) -> float:
        return self.own.ram_mb + sum(p.ram_mb for p in self.descendants)


class HealthMonitor:
    """On-demand resource inspection. No background sampler, daemon, provider, or model call."""

    def __init__(self, app_dir: Path, project: Path) -> None:
        self.app_dir = app_dir
        self.project = project

    @staticmethod
    def _own_windows() -> ProcessHealth:
        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        counters = PMC(); counters.cb = ctypes.sizeof(PMC)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        rss = counters.WorkingSetSize / 1048576.0 if ok else 0.0
        peak = counters.PeakWorkingSetSize / 1048576.0 if ok else 0.0
        # Some Python/Windows combinations return a successful zeroed PSAPI row.
        # Fall back to Get-Process only when that happens; /health is on-demand so
        # this never leaves a resident monitor behind.
        if rss <= 0.0:
            try:
                script = f"$p=Get-Process -Id {os.getpid()} -ErrorAction Stop; @{{rss=$p.WorkingSet64;peak=$p.PeakWorkingSet64}} | ConvertTo-Json -Compress"
                row = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=4)
                data = json.loads(row.stdout or "{}") if row.returncode == 0 else {}
                rss = float(data.get("rss") or 0) / 1048576.0
                peak = float(data.get("peak") or 0) / 1048576.0
            except Exception:
                pass
        return ProcessHealth(os.getpid(), "Advertpreneur CLI", rss, peak)

    @staticmethod
    def _own_posix() -> ProcessHealth:
        rss = peak = 0.0
        try:
            import resource
            value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            peak = value / (1024.0 if sys.platform != "darwin" else 1048576.0)
        except Exception:
            pass
        try:
            status = Path("/proc/self/status").read_text(encoding="utf-8", errors="replace")
            for line in status.splitlines():
                if line.startswith("VmRSS:"):
                    rss = float(line.split()[1]) / 1024.0
                    break
        except Exception:
            rss = peak
        return ProcessHealth(os.getpid(), "Advertpreneur CLI", rss, peak)

    @staticmethod
    def _windows_descendants(root_pid: int) -> List[ProcessHealth]:
        # PowerShell is spawned only for /health and exits immediately; idle ADP stays dependency-free/lightweight.
        script = r'''$rows=Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,WorkingSetSize,CommandLine;
$root=%d; $ids=@($root); $out=@(); do { $added=$false; foreach($r in $rows){ if(($ids -contains [int]$r.ParentProcessId) -and -not ($ids -contains [int]$r.ProcessId)){ $ids += [int]$r.ProcessId; $out += $r; $added=$true } } } while($added); $out | ConvertTo-Json -Compress''' % root_pid
        try:
            p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=8)
            if p.returncode != 0 or not p.stdout.strip(): return []
            raw = json.loads(p.stdout)
            rows = raw if isinstance(raw, list) else [raw]
            out = []
            for r in rows:
                pid = int(r.get("ProcessId") or 0)
                if pid <= 0: continue
                out.append(ProcessHealth(pid, str(r.get("Name") or "process"), float(r.get("WorkingSetSize") or 0) / 1048576.0, command=str(r.get("CommandLine") or "")))
            return out
        except Exception:
            return []

    @staticmethod
    def _dir_size(path: Path, cap_files: int = 20000) -> float:
        total = count = 0
        if not path.exists(): return 0.0
        try:
            for p in path.rglob("*"):
                if count >= cap_files: break
                if p.is_file():
                    count += 1
                    try: total += p.stat().st_size
                    except OSError: pass
        except OSError:
            pass
        return total / 1048576.0

    def own_process(self) -> ProcessHealth:
        return self._own_windows() if os.name == "nt" else self._own_posix()

    def snapshot(self) -> HealthSnapshot:
        own = self.own_process()
        children = self._windows_descendants(os.getpid()) if os.name == "nt" else []
        # Do not count the short-lived PowerShell health probe if it is still visible in the snapshot.
        children = [p for p in children if "health" not in p.command.lower() and "get-ciminstance win32_process" not in p.command.lower()]
        return HealthSnapshot(own, children, self._dir_size(self.project / ".advertpreneur"), self._dir_size(self.app_dir))
