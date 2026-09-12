from __future__ import annotations

import io
import math
import os
import struct
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import Callable


class TaskNotifier:
    """Lightweight local feedback: sounds + Windows desktop balloon notifications.

    There is intentionally no always-on Beacon, heartbeat, status writer, resident UI,
    model call, or provider call in this module.
    """

    def __init__(self, app_dir: Path, project_getter: Callable[[], Path], enabled: bool = True, sounds: bool = True, **_compat) -> None:
        self.app_dir = app_dir
        self.project_getter = project_getter
        self.enabled = bool(enabled)
        self.sounds = bool(sounds)

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def configure(self, enabled: bool | None = None, sounds: bool | None = None, **_compat) -> None:
        if enabled is not None: self.enabled = bool(enabled)
        if sounds is not None: self.sounds = bool(sounds)

    def _creationflags(self) -> int:
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0

    @staticmethod
    def _tone_wav(kind: str) -> bytes:
        sequences = {
            "start": [(740.0, 0.08), (988.0, 0.10)],
            "done": [(784.0, 0.08), (1047.0, 0.12)],
            "error": [(392.0, 0.14), (294.0, 0.18)],
        }
        seq = sequences.get(kind, sequences["start"]); rate = 22050; amplitude = 9000; frames = bytearray()
        for freq, duration in seq:
            samples = max(1, int(rate * duration)); attack = max(1, int(rate * 0.006)); release = max(1, int(rate * 0.018))
            for i in range(samples):
                gain = 1.0
                if i < attack: gain = i / attack
                elif i > samples - release: gain = max(0.0, (samples - i) / release)
                frames.extend(struct.pack("<h", int(amplitude * gain * math.sin(2.0 * math.pi * freq * i / rate))))
            frames.extend(b"\x00\x00" * int(rate * 0.025))
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(bytes(frames))
        return buffer.getvalue()

    def _sound(self, kind: str) -> None:
        if not self.sounds: return
        def play() -> None:
            if sys.platform == "win32":
                try:
                    import winsound
                    winsound.PlaySound(self._tone_wav(kind), winsound.SND_MEMORY); return
                except Exception:
                    try:
                        import winsound
                        flags = {"start": getattr(winsound, "MB_ICONASTERISK", -1), "done": getattr(winsound, "MB_ICONEXCLAMATION", -1), "error": getattr(winsound, "MB_ICONHAND", -1)}
                        winsound.MessageBeep(flags.get(kind, -1)); return
                    except Exception: pass
            try: sys.stdout.write("\a"); sys.stdout.flush()
            except Exception: pass
        threading.Thread(target=play, name=f"advertpreneur-sound-{kind}", daemon=True).start()

    def _balloon(self, title: str, text: str) -> None:
        if not self.enabled or not self.supported: return
        title = title.replace("'", "''")[:80]; text = text.replace("'", "''")[:220]
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
            "$n=New-Object System.Windows.Forms.NotifyIcon; $n.Icon=[System.Drawing.SystemIcons]::Information; "
            f"$n.BalloonTipTitle='{title}'; $n.BalloonTipText='{text}'; "
            "$n.Visible=$true; $n.ShowBalloonTip(3500); Start-Sleep -Milliseconds 3800; $n.Dispose();"
        )
        try:
            subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=self._creationflags())
        except Exception: pass

    def task_started(self, model: str, summary: str = "Working") -> None:
        project = self.project_getter().name or str(self.project_getter())
        self._sound("start"); self._balloon("Advertpreneur CLI · started", f"{project} · {summary}")

    def task_finished(self, success: bool = True, detail: str = "") -> None:
        project = self.project_getter().name or str(self.project_getter())
        status = "done" if success else "needs attention"
        self._sound("done" if success else "error")
        self._balloon(f"Advertpreneur CLI · {status}", detail or project)

    def quota_warning(self, provider: str, threshold: int, remaining: float | None, quota_text: str) -> None:
        pct = "unknown" if remaining is None else f"{remaining:.0f}%"
        if threshold <= 0: title = f"{provider.upper()} quota exhausted"
        elif threshold <= 5: title = f"{provider.upper()} quota critical"
        elif threshold <= 10: title = f"{provider.upper()} quota low"
        else: title = f"{provider.upper()} quota warning"
        self._balloon(title, f"{pct} remaining · {quota_text}")

    def close(self) -> None:
        return
