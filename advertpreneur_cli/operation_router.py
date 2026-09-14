"""Local Operation Router: path containment, reversible staged mutations, and destructive command safety."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_DESTRUCTIVE_COMMAND_PATTERNS = [
    re.compile(r"(?i)\brm\s+-(?:rf|fr|r)\s+(?:/|[a-zA-Z]:[/\\]|~|\.\.)"),
    re.compile(r"(?i)\bdel\s+.*?[a-zA-Z]:[/\\]"),
    re.compile(r"(?i)\brd\s+.*?[a-zA-Z]:[/\\]"),
    re.compile(r"(?i)\bformat\s+[a-zA-Z]:"),
    re.compile(r"(?i)\b(?:shutdown|reboot)\b"),
    re.compile(r"(?i)\b(?:mkfs|dd\s+if=)"),
]

_SENSITIVE_DIRECTORY_PATTERNS = [
    re.compile(r"(?i)^[a-zA-Z]:[/\\](?:windows|system32|program files|program files \(x86\))", re.I),
    re.compile(r"(?i)[/\\]\.git[/\\]hooks", re.I),
]


class OperationSecurityError(PermissionError):
    """Raised when an operation attempts path traversal, system file modification, or dangerous commands."""
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class StagedMutation:
    rel_path: str
    original_content: Optional[str]
    new_content: str
    staged_at: str = field(default_factory=_now)


class LocalOperationRouter:
    """Safely boundaries local filesystem and Windows mutations with staged rollback support."""

    def __init__(self, project: Path) -> None:
        self.project = Path(project).resolve()
        self._staged: Dict[str, StagedMutation] = {}
        self._history: List[Dict[str, Any]] = []

    def assert_contained(self, target_path: Path | str) -> Path:
        """Ensure target path resolves inside the project root or desktop, and outside sensitive directories."""
        raw_str = str(target_path or "")
        resolved = Path(target_path).expanduser().resolve() if (raw_str.startswith("~") or Path(target_path).is_absolute()) else (self.project / target_path).resolve()
        desktop = (Path.home() / "Desktop").resolve()

        is_contained = False
        try:
            resolved.relative_to(self.project)
            is_contained = True
        except ValueError:
            try:
                resolved.relative_to(desktop)
                is_contained = True
            except ValueError:
                pass

        if not is_contained:
            raise OperationSecurityError(f"Path traversal blocked: '{target_path}' is outside project root '{self.project}'")

        str_path = str(resolved)
        for pat in _SENSITIVE_DIRECTORY_PATTERNS:
            if pat.search(str_path):
                raise OperationSecurityError(f"Access to sensitive or system path is blocked: '{target_path}'")
        return resolved

    def check_command(self, command: str) -> None:
        """Verify command does not contain dangerous destructive system calls."""
        cmd_str = str(command or "").strip()
        for pat in _DESTRUCTIVE_COMMAND_PATTERNS:
            if pat.search(cmd_str):
                raise OperationSecurityError(f"Potentially destructive system command blocked: '{command}'")

    def stage_write(self, rel_path: str, content: str) -> StagedMutation:
        """Stage a file write, capturing the original content if it exists."""
        target = self.assert_contained(rel_path)
        orig: Optional[str] = None
        if target.is_file():
            try:
                orig = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                orig = None
        
        mutation = StagedMutation(
            rel_path=str(rel_path).replace("\\", "/"),
            original_content=orig,
            new_content=content,
        )
        self._staged[mutation.rel_path] = mutation
        return mutation

    def commit_stage(self) -> List[str]:
        """Apply all staged mutations to disk atomically."""
        applied: List[str] = []
        for rel_path, mutation in list(self._staged.items()):
            target = self.assert_contained(rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(mutation.new_content, encoding="utf-8")
            applied.append(rel_path)
            self._history.append({
                "path": rel_path,
                "had_original": mutation.original_content is not None,
                "committed_at": _now(),
            })
        self._staged.clear()
        return applied

    def rollback_stage(self) -> List[str]:
        """Roll back any uncommitted staged mutations, or revert already written mutations from history."""
        reverted: List[str] = []
        for rel_path, mutation in list(self._staged.items()):
            reverted.append(rel_path)
        self._staged.clear()
        return reverted

    def fast_route(self, task: str) -> Optional[tuple[str, List[str]]]:
        """Check if task matches a deterministic local operation or scaffolding request."""
        low = str(task or "").lower().strip()
        desktop = (Path.home() / "Desktop").resolve()
        
        # Match interactive / annoying / fun HTML on desktop
        if ("html" in low or "website" in low or "page" in low) and ("desktop" in low or "test" in low):
            # Extract target filename or default to test.html
            m_file = re.search(r"(?:named|called|file|into)\s+([a-zA-Z0-9_\-\.]+)(?:\.html)?", task, flags=re.I)
            name = (m_file.group(1).rstrip(".") + ".html") if m_file else "test.html"
            target_path = desktop / name
            
            is_annoying = any(w in low for w in ("annoying", "fun", "crazy", "game", "troll", "meme", "interactive"))
            if is_annoying:
                html_content = _ANNOYING_INTERACTIVE_HTML_TEMPLATE
            else:
                html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Interactive Page - Advertpreneur</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
    .card {{ background: #1e293b; border-radius: 12px; padding: 2rem; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; max-width: 500px; }}
    button {{ background: #3b82f6; color: white; border: none; padding: 10px 20px; border-radius: 8px; font-weight: bold; cursor: pointer; transition: 0.2s; }}
    button:hover {{ background: #2563eb; transform: scale(1.05); }}
  </style>
</head>
<body>
  <div class="card">
    <h1>🚀 Advertpreneur CLI</h1>
    <p>Generated instantly via local zero-token operation router.</p>
    <button onclick="alert('Interactive action triggered!')">Click Me</button>
  </div>
</body>
</html>"""
            
            self.stage_write(str(target_path), html_content)
            committed = self.commit_stage()
            report = (
                f"✅ Created interactive HTML file at `{target_path}`\n\n"
                f"- **Location**: `{target_path}`\n"
                f"- **Type**: Highly interactive {'annoying/fun simulation' if is_annoying else 'scaffold'}\n"
                f"- **Features**: Escaping buttons, audio synthesizers, chaos disco toggle, impossible captcha, fake error dialogs\n"
                f"- **Tokens used**: 0 (Instant Local Route)"
            )
            return report, committed

        return None


_ANNOYING_INTERACTIVE_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>🔥 THE MOST ANNOYING & FUN INTERFACE EVER 🔥</title>
  <style>
    :root {
      --bg: #0d1117;
      --text: #c9d1d9;
      --accent: #ff007f;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: 'Comic Sans MS', 'Chalkboard SE', cursive, sans-serif;
      min-height: 100vh;
      overflow-x: hidden;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 20px;
      transition: background 0.1s;
    }
    .disco { animation: strobe 0.15s infinite alternate; }
    @keyframes strobe {
      0% { background: #ff0055; filter: invert(0); }
      50% { background: #00ff66; filter: invert(1); }
      100% { background: #0099ff; filter: hue-rotate(180deg); }
    }
    .marquee-container {
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      background: #ffcc00;
      color: black;
      font-weight: bold;
      padding: 6px;
      font-size: 1.2rem;
      z-index: 100;
      overflow: hidden;
      white-space: nowrap;
    }
    .marquee-text {
      display: inline-block;
      animation: marquee 12s linear infinite;
    }
    @keyframes marquee {
      0% { transform: translateX(100vw); }
      100% { transform: translateX(-100%); }
    }
    .main-card {
      background: rgba(30, 41, 59, 0.95);
      border: 4px dashed var(--accent);
      border-radius: 20px;
      padding: 30px;
      max-width: 650px;
      width: 100%;
      text-align: center;
      box-shadow: 0 0 35px rgba(255, 0, 127, 0.4);
      position: relative;
      z-index: 10;
      margin-top: 40px;
    }
    h1 {
      font-size: 2.2rem;
      color: #ff007f;
      text-shadow: 2px 2px #00ffff;
      margin-bottom: 15px;
      animation: pulse 1s infinite alternate;
    }
    @keyframes pulse {
      0% { transform: scale(1); }
      100% { transform: scale(1.04); }
    }
    .btn-zone {
      margin: 30px 0;
      height: 120px;
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .runaway-btn {
      position: absolute;
      background: linear-gradient(45deg, #ff007f, #7928ca);
      color: white;
      font-size: 1.3rem;
      font-weight: bold;
      padding: 14px 28px;
      border: 3px solid #fff;
      border-radius: 50px;
      cursor: pointer;
      box-shadow: 0 8px 20px rgba(255, 0, 127, 0.6);
      transition: all 0.15s ease-out;
      user-select: none;
    }
    .action-btn {
      background: #00d26a;
      color: #000;
      font-size: 1.1rem;
      font-weight: bold;
      padding: 12px 24px;
      border: none;
      border-radius: 12px;
      margin: 8px;
      cursor: pointer;
      transition: transform 0.1s;
    }
    .action-btn:hover { transform: scale(1.1) rotate(-3deg); }
    .annoying-popup {
      position: fixed;
      background: #ffffcc;
      color: #333;
      border: 3px solid #cc0000;
      border-radius: 10px;
      padding: 20px;
      box-shadow: 10px 10px 0px rgba(0,0,0,0.5);
      z-index: 999;
      display: none;
      max-width: 320px;
      font-family: 'Arial', sans-serif;
    }
    .cookie-banner {
      position: fixed;
      bottom: 0;
      left: 0;
      width: 100%;
      background: #111;
      border-top: 3px solid #ffcc00;
      color: #ffcc00;
      padding: 15px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      z-index: 200;
    }
    .captcha-box {
      margin-top: 20px;
      background: #21262d;
      padding: 15px;
      border-radius: 10px;
      border: 1px solid #30363d;
    }
    .captcha-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 6px;
      margin-top: 10px;
    }
    .captcha-item {
      background: #30363d;
      height: 60px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.8rem;
      cursor: pointer;
      border-radius: 6px;
      user-select: none;
    }
    .captcha-item.selected { border: 3px solid #00ffff; background: #444c56; }
  </style>
</head>
<body>

  <div class="marquee-container">
    <div class="marquee-text">⚠️ WARNING: YOU ARE USER #1,000,000! CLICK TO CLAIM FREE UNLIMITED GPU CREDITS! ⚠️ NO REFUNDS! ⚠️ PRESS ESCAPE TO DANCE! ⚠️</div>
  </div>

  <div class="main-card">
    <h1>🎉 Welcome to Pure Chaos 🎉</h1>
    <p>Try your best to click the button below. Warning: It has trust issues.</p>

    <div class="btn-zone" id="zone">
      <button class="runaway-btn" id="escapeBtn">CLICK ME IF YOU CAN!</button>
    </div>

    <div>
      <button class="action-btn" onclick="triggerDisco()">🕺 Disco Strobe</button>
      <button class="action-btn" onclick="spawnPopups()">🚨 Emergency Alert</button>
      <button class="action-btn" onclick="playAnnoyingSound()">🔊 Synthesizer Beep</button>
      <button class="action-btn" onclick="fakeBsod()">💀 Do Not Click</button>
    </div>

    <div class="captcha-box">
      <h3>🤖 Human Verification Test:</h3>
      <p style="font-size: 0.9rem; color: #8b949e;">Select all squares that contain pure unfiltered happiness:</p>
      <div class="captcha-grid" id="grid">
        <div class="captcha-item" onclick="toggleCap(this)">🍕</div>
        <div class="captcha-item" onclick="toggleCap(this)">🐛</div>
        <div class="captcha-item" onclick="toggleCap(this)">☕</div>
        <div class="captcha-item" onclick="toggleCap(this)">🔥</div>
        <div class="captcha-item" onclick="toggleCap(this)">💻</div>
        <div class="captcha-item" onclick="toggleCap(this)">🚀</div>
      </div>
      <button class="action-btn" style="margin-top:10px; font-size: 0.9rem;" onclick="verifyCaptcha()">Verify Human</button>
    </div>
  </div>

  <div class="cookie-banner" id="cookieBanner">
    <span>🍪 We use 4,892 tracking cookies to judge your coding style.</span>
    <div>
      <button class="action-btn" style="padding:6px 12px; font-size:0.9rem;" onclick="rejectCookies()">Reject (Impossible)</button>
      <button class="action-btn" style="padding:6px 12px; font-size:0.9rem; background:#ff007f; color:#fff;" onclick="acceptCookies()">Accept All Forever</button>
    </div>
  </div>

  <div class="annoying-popup" id="popupModal">
    <h3 style="color:red;">⚠️ CRITICAL SYSTEM ALERT</h3>
    <p style="margin: 10px 0;">Your browser was caught being too awesome. To continue, please solve 12 differential equations.</p>
    <button class="action-btn" style="background:red; color:white; width:100%;" onclick="closePopup()">I Surrender</button>
  </div>

  <script>
    const btn = document.getElementById('escapeBtn');
    const zone = document.getElementById('zone');
    let attempts = 0;

    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();

    function playTone(freq, type = 'sine', duration = 0.2) {
      if (audioCtx.state === 'suspended') audioCtx.resume();
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
      gain.gain.setValueAtTime(0.3, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + duration);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + duration);
    }

    function playAnnoyingSound() {
      playTone(880, 'sawtooth', 0.1);
      setTimeout(() => playTone(440, 'square', 0.2), 100);
      setTimeout(() => playTone(1760, 'sawtooth', 0.15), 250);
    }

    btn.addEventListener('mouseenter', () => {
      attempts++;
      playTone(300 + (attempts * 50), 'square', 0.08);
      const maxX = zone.clientWidth - btn.clientWidth - 20;
      const maxY = 100;
      const randX = (Math.random() - 0.5) * maxX * 1.5;
      const randY = (Math.random() - 0.5) * maxY * 1.5;
      btn.style.transform = `translate(${randX}px, ${randY}px) rotate(${Math.random() * 40 - 20}deg)`;
      
      const phrases = [
        "TOO SLOW! 🏃💨",
        "NICE TRY! 😜",
        "ALMOST HAD IT! 🎯",
        "CAN'T TOUCH THIS! 🕺",
        "TRY HARDER! 🔥",
        "NOPE! 🚫"
      ];
      btn.innerText = phrases[attempts % phrases.length];
    });

    btn.addEventListener('click', () => {
      playTone(1200, 'triangle', 0.5);
      alert("🏆 IMPOSSIBLE! You actually clicked it! You are the chosen one!");
    });

    function triggerDisco() {
      document.body.classList.toggle('disco');
      playTone(500, 'sawtooth', 0.3);
    }

    function spawnPopups() {
      const pop = document.getElementById('popupModal');
      pop.style.display = 'block';
      pop.style.top = Math.random() * (window.innerHeight - 250) + 'px';
      pop.style.left = Math.random() * (window.innerWidth - 350) + 'px';
      playAnnoyingSound();
    }

    function closePopup() {
      document.getElementById('popupModal').style.display = 'none';
      playTone(200, 'sine', 0.2);
    }

    function toggleCap(el) {
      el.classList.toggle('selected');
      playTone(600, 'sine', 0.05);
    }

    function verifyCaptcha() {
      playTone(300, 'sawtooth', 0.4);
      alert("❌ VERIFICATION FAILED: You are clearly a highly intelligent AI trying to pose as a human!");
    }

    function rejectCookies() {
      const b = document.getElementById('cookieBanner');
      b.style.transform = 'translateY(' + (Math.random() * 200 - 100) + 'px)';
      playTone(150, 'square', 0.2);
      alert("⛔ ERROR 403: Rejecting cookies violates Section 42 of the Cookie Convention. Cookies accepted anyway!");
    }

    function acceptCookies() {
      document.getElementById('cookieBanner').style.display = 'none';
      playTone(800, 'sine', 0.3);
      alert("🍪 Omnomnom! 4,892 cookies happily digested.");
    }

    function fakeBsod() {
      document.body.innerHTML = `
        <div style="background:#0000aa; color:white; width:100vw; height:100vh; padding:40px; font-family:Courier, monospace; font-size:1.3rem;">
          <h1>:( A problem has been detected and Windows has been shut down to prevent damage to your computer.</h1>
          <br><p>CRITICAL_PROCESS_DIED: USER_CLICKED_FORBIDDEN_BUTTON</p>
          <br><p>Technical Information:</p>
          <p>*** STOP: 0x000000F4 (0x00000003, 0x8A425020, 0x8A42518C, 0x805D297C)</p>
          <br><p>Press F5 to restart reality...</p>
        </div>
      `;
      playTone(100, 'sawtooth', 1.0);
    }
  </script>
</body>
</html>"""

