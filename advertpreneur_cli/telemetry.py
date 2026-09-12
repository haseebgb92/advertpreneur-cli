from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _now() -> datetime:
    return datetime.now().astimezone()


class HarnessTelemetry:
    """Tiny local JSONL telemetry for daily/weekly engineering review.

    It never calls a model and stores no source bodies, credentials, browser page
    content, or prompts by default. Events contain operational metadata only.
    """

    def __init__(self, app_dir: Path) -> None:
        self.path = app_dir / "harness-telemetry.jsonl"
        self.report_dir = app_dir / "reports"
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def record(self, kind: str, **data: Any) -> None:
        row = {"at": _now().isoformat(timespec="seconds"), "kind": str(kind)}
        for k, v in data.items():
            if k in {"prompt", "source", "content", "credentials", "token", "secret"}:
                continue
            row[k] = v
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def _rows(self, since: datetime) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
                at = datetime.fromisoformat(str(row.get("at") or ""))
            except Exception:
                continue
            if at >= since:
                rows.append(row)
        return rows

    def summary(self, period: str = "today") -> str:
        now = _now()
        period = period.lower().strip()
        if period in {"week", "weekly", "7d"}:
            since = now - timedelta(days=7); label = "Last 7 days"
        else:
            since = now.replace(hour=0, minute=0, second=0, microsecond=0); label = "Today"
        rows = self._rows(since)
        provider_runs = [r for r in rows if r.get("kind") == "provider_run"]
        tasks = [r for r in rows if r.get("kind") == "task"]
        browser = [r for r in rows if r.get("kind") == "browser"]
        failures = [r for r in rows if not bool(r.get("ok", True))]
        # Top-level task rows are the accounting source of truth. External coding also
        # emits provider_run diagnostics, so summing both used to double-count usage.
        accounting = tasks if tasks else provider_runs
        by_provider: Dict[str, Dict[str, float]] = defaultdict(lambda: {
            "runs": 0, "in": 0, "out": 0, "cache": 0, "thinking": 0, "cost": 0.0,
            "turns": 0, "tools": 0, "seconds": 0.0, "max_ram": 0.0,
        })
        for r in accounting:
            p = str(r.get("provider") or "unknown"); row = by_provider[p]
            row["runs"] += 1; row["in"] += int(r.get("input_tokens") or 0); row["out"] += int(r.get("output_tokens") or 0)
            row["cache"] += int(r.get("cache_read_tokens") or 0); row["thinking"] += int(r.get("thinking_tokens") or 0)
            row["cost"] += float(r.get("metered_usd") or 0.0); row["turns"] += int(r.get("provider_turns") or r.get("requests") or 0)
            row["tools"] += int(r.get("tool_calls") or 0); row["seconds"] += float(r.get("duration_seconds") or 0.0)
            row["max_ram"] = max(row["max_ram"], float(r.get("adp_ram_end_mb") or 0.0))
        lines = [f"Advertpreneur harness review · {label}", "", f"Events: {len(rows)} · tasks: {len(tasks)} · external provider events: {len(provider_runs)} · browser ops: {len(browser)} · failures: {len(failures)}"]
        if by_provider:
            lines += ["", "Task/resource usage (top-level tasks only; no provider-event double counting):"]
            for p, d in sorted(by_provider.items()):
                cost = f"${d['cost']:.4f}" if d["cost"] else "provider/subscription managed"
                avg = d["seconds"] / max(1.0, d["runs"])
                cache = f" · {int(d['cache']):,} cached" if d["cache"] else ""
                ram = f" · ADP RAM up to {d['max_ram']:.1f} MB" if d["max_ram"] else ""
                lines.append(f"- {p}: {int(d['runs'])} task(s) · {int(d['turns'])} provider turn(s) · {int(d['tools'])} tools · {int(d['in']):,} in{cache} · {int(d['out']):,} out · avg {avg:.1f}s{ram} · {cost}")
        specialist = [r for r in provider_runs if str(r.get("purpose") or "") not in {"", "coding"}]
        if specialist:
            lines += ["", f"Specialist/reviewer provider runs: {len(specialist)} (reported separately from top-level task accounting)."]
        if browser:
            prov = Counter(str(r.get("browser_provider") or "unknown") for r in browser)
            lines += ["", "Browser providers: " + ", ".join(f"{k} ×{v}" for k, v in prov.most_common())]
        if failures:
            lines += ["", "Recent failures:"]
            for r in failures[-8:]: lines.append(f"- {r.get('kind')} · {r.get('detail') or r.get('status') or 'failed'}")
        lines += ["", "This report is generated locally and uses 0 model tokens."]
        return "\n".join(lines)

    def save_report(self, period: str = "today") -> Path:
        text = self.summary(period)
        now = _now()
        suffix = "week" if period.lower().strip() in {"week", "weekly", "7d"} else "day"
        path = self.report_dir / f"{now.date().isoformat()}-{suffix}.md"
        path.write_text("# " + text.replace("Advertpreneur harness review · ", "Advertpreneur harness review — ", 1), encoding="utf-8")
        return path
