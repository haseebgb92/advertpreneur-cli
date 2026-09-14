from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen


class MissionClient:
    def __init__(self, project: Path, port: int | None = None) -> None:
        descriptor = Path(project).resolve() / ".advertpreneur" / "mission-daemon.json"
        data = json.loads(descriptor.read_text(encoding="utf-8"))
        self.port = int(port if port is not None else data["port"])
        self.token = str(data["token"])

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(f"http://127.0.0.1:{self.port}{path}", data=payload, method=method, headers={"X-ADP-Mission-Token": self.token, "Content-Type": "application/json"})
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def create_mission(self, request: str, steps: list[dict]) -> dict:
        return self._request("POST", "/missions", {"request": request, "steps": steps})

    def get_mission(self, mission_id: str) -> dict:
        return self._request("GET", f"/missions/{mission_id}")

