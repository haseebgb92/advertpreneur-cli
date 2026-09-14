from pathlib import Path
from advertpreneur_cli.dashboard_server import DashboardServer, DashboardState
import urllib.request
import json
import time


def test_dashboard_state(tmp_path: Path) -> None:
    state = DashboardState(tmp_path)
    state.add_event("task", "Build Login Page", "Generating login form")
    state.add_event("checkpoint", "Snapshot taken", "2 files modified")

    summary = state.get_summary()
    assert summary["project_name"] == tmp_path.name
    assert len(summary["events"]) == 2
    assert summary["events"][0]["title"] == "Snapshot taken"


def test_dashboard_server_endpoints(tmp_path: Path) -> None:
    server = DashboardServer(tmp_path, port=4180)
    url = server.start(open_browser=False)
    try:
        # Check GET /
        with urllib.request.urlopen(f"{url}/") as response:
            assert response.status == 200
            html = response.read().decode("utf-8")
            assert "ADP OS Live Sidecar Dashboard" in html

        # Check GET /api/status
        with urllib.request.urlopen(f"{url}/api/status") as response:
            assert response.status == 200
            data = json.loads(response.read().decode("utf-8"))
            assert "project_name" in data
            assert "events" in data

        # Check GET /api/checkpoints
        with urllib.request.urlopen(f"{url}/api/checkpoints") as response:
            assert response.status == 200
            data = json.loads(response.read().decode("utf-8"))
            assert isinstance(data, list)
    finally:
        server.stop()
