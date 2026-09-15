from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


LEDGER_FIELDS = [
    "keyword", "state", "started_at", "completed_at", "report_path",
    "original_report_name", "browser_url", "detail",
]
TERMINAL_STATES = {"completed", "no_data", "download_missing", "verification_required", "site_changed"}
XRAY_SELECTOR_KEYS = ("open", "rows", "load_more", "refresh", "export", "csv")


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return cleaned[:72] or "research-run"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ResearchRun:
    root: Path
    name: str
    run_dir: Path
    ledger_path: Path

    @classmethod
    def create(cls, root: Path, name: str, keywords: Iterable[str]) -> "ResearchRun":
        root = Path(root).resolve()
        run_dir = root / ".advertpreneur" / "research" / _slug(name)
        ledger_path = run_dir / "keywords.csv"
        run = cls(root=root, name=str(name).strip() or "Research run", run_dir=run_dir, ledger_path=ledger_path)
        run_dir.mkdir(parents=True, exist_ok=True)
        if not ledger_path.exists():
            rows = []
            seen = set()
            for item in keywords:
                keyword = " ".join(str(item).split())
                normalized = keyword.casefold()
                if keyword and normalized not in seen:
                    seen.add(normalized)
                    rows.append(run._row(keyword, "pending"))
            run._write(rows)
        return run

    @classmethod
    def open(cls, root: Path, name: str) -> "ResearchRun":
        root = Path(root).resolve()
        run_dir = root / ".advertpreneur" / "research" / _slug(name)
        ledger_path = run_dir / "keywords.csv"
        if not ledger_path.is_file():
            raise FileNotFoundError(f"Research run not found: {name}")
        return cls(root=root, name=str(name).strip() or "Research run", run_dir=run_dir, ledger_path=ledger_path)

    def _row(self, keyword: str, state: str, **extra: str) -> dict[str, str]:
        row = {field: "" for field in LEDGER_FIELDS}
        row.update(keyword=keyword, state=state)
        row.update({key: str(value) for key, value in extra.items() if key in row})
        return row

    def _read(self) -> list[dict[str, str]]:
        with self.ledger_path.open("r", encoding="utf-8", newline="") as handle:
            return [{field: str(row.get(field) or "") for field in LEDGER_FIELDS} for row in csv.DictReader(handle)]

    def _write(self, rows: list[dict[str, str]]) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    @property
    def selectors_path(self) -> Path:
        return self.run_dir / "observed-selectors.json"

    def selectors(self) -> dict[str, str]:
        try:
            data = json.loads(self.selectors_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {key: str(data.get(key) or "") for key in ("search", "submit", "export") if str(data.get(key) or "")}

    def xray_selectors(self) -> dict[str, str]:
        try:
            data = json.loads(self.selectors_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        xray = data.get("xray") if isinstance(data, dict) else {}
        return {key: str(xray.get(key) or "") for key in XRAY_SELECTOR_KEYS if isinstance(xray, dict) and str(xray.get(key) or "")}

    def remember_selectors(self, *, search: str = "", submit: str = "", export: str = "") -> None:
        selectors = self.selectors()
        for key, value in {"search": search, "submit": submit, "export": export}.items():
            clean = str(value or "").strip()
            if clean:
                selectors[key] = clean
        self.selectors_path.write_text(json.dumps(selectors, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def remember_xray_selectors(self, **values: str) -> None:
        try:
            data = json.loads(self.selectors_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        xray = data.setdefault("xray", {})
        for key in XRAY_SELECTOR_KEYS:
            value = str(values.get(key) or "").strip()
            if value:
                xray[key] = value
        self.selectors_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def next_keyword(self) -> str | None:
        rows = self._read()
        for row in rows:
            if row["state"] == "pending":
                row.update(state="active", started_at=_now(), detail="")
                self._write(rows)
                return row["keyword"]
        return None

    def complete_download(self, keyword: str, source: Path, browser_url: str = "") -> Path:
        source = Path(source).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Downloaded report not found: {source}")
        rows = self._read()
        row = next((item for item in rows if item["keyword"] == keyword and item["state"] == "active"), None)
        if row is None:
            raise ValueError(f"Keyword is not active in this research run: {keyword}")
        reports = self.run_dir / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        suffix = source.suffix.lower() or ".csv"
        base = _slug(keyword)
        target = reports / f"{base}{suffix}"
        number = 2
        while target.exists():
            target = reports / f"{base}-{number}{suffix}"
            number += 1
        shutil.move(str(source), str(target))
        row.update(
            state="completed", completed_at=_now(), report_path=str(target.relative_to(self.root)),
            original_report_name=source.name, browser_url=str(browser_url), detail="download renamed locally",
        )
        self._write(rows)
        return target

    def pause(self, keyword: str, detail: str, browser_url: str = "") -> None:
        rows = self._read()
        row = next((item for item in rows if item["keyword"] == keyword and item["state"] == "active"), None)
        if row is None:
            raise ValueError(f"Keyword is not active in this research run: {keyword}")
        row.update(state="paused", browser_url=str(browser_url), detail=" ".join(str(detail).split()))
        self._write(rows)

    def record_outcome(self, keyword: str, state: str, detail: str, browser_url: str = "") -> None:
        if state not in TERMINAL_STATES - {"completed"}:
            raise ValueError(f"Unsupported research outcome: {state}")
        rows = self._read()
        row = next((item for item in rows if item["keyword"] == keyword and item["state"] == "active"), None)
        if row is None:
            raise ValueError(f"Keyword is not active in this research run: {keyword}")
        row.update(state=state, completed_at=_now(), browser_url=str(browser_url), detail=" ".join(str(detail).split()))
        self._write(rows)

    def resume(self, keyword: str) -> None:
        rows = self._read()
        row = next((item for item in rows if item["keyword"] == keyword and item["state"] == "paused"), None)
        if row is None:
            raise ValueError(f"Keyword is not paused in this research run: {keyword}")
        row.update(state="pending", started_at="", completed_at="", detail="resumed")
        self._write(rows)

    def status(self) -> dict[str, list[str] | str]:
        rows = self._read()
        return {
            "name": self.name,
            "pending": [row["keyword"] for row in rows if row["state"] == "pending"],
            "active": [row["keyword"] for row in rows if row["state"] == "active"],
            "paused": [row["keyword"] for row in rows if row["state"] == "paused"],
            "completed": [row["keyword"] for row in rows if row["state"] == "completed"],
            "no_data": [row["keyword"] for row in rows if row["state"] == "no_data"],
            "download_missing": [row["keyword"] for row in rows if row["state"] == "download_missing"],
            "verification_required": [row["keyword"] for row in rows if row["state"] == "verification_required"],
            "site_changed": [row["keyword"] for row in rows if row["state"] == "site_changed"],
        }
