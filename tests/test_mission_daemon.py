from advertpreneur_cli.mission_daemon import MissionDaemon
from advertpreneur_cli.mission_client import MissionClient
from advertpreneur_cli.missions import MissionStore
import json


def test_daemon_starts_once_and_client_recovers_saved_mission(tmp_path):
    daemon = MissionDaemon(tmp_path, port=0)
    assert daemon.start() is True
    try:
        client = MissionClient(tmp_path, daemon.port)
        mission = client.create_mission("Inspect repo", [{"title": "Inspect", "kind": "inspect"}])

        assert client.get_mission(mission["id"])["request"] == "Inspect repo"
        second = MissionDaemon(tmp_path, port=daemon.port)
        assert second.start() is False
    finally:
        daemon.stop()


def test_daemon_replaces_a_stale_descriptor(tmp_path):
    descriptor = tmp_path / ".advertpreneur" / "mission-daemon.json"
    descriptor.parent.mkdir()
    descriptor.write_text(json.dumps({"pid": 2147483647, "port": 1, "token": "stale"}), encoding="utf-8")

    daemon = MissionDaemon(tmp_path, port=0)
    assert daemon.start() is True
    daemon.stop()


def test_daemon_recovery_marks_an_active_mission_interrupted_and_requeues_its_step(tmp_path):
    store = MissionStore(tmp_path)
    mission = store.create("Build release", [{"title": "Build", "kind": "code"}])
    store.transition_step(mission.id, mission.steps[0].id, "active")

    recovered = store.recover_interrupted()
    saved = store.load(mission.id)

    assert recovered == [mission.id]
    assert saved.status == "interrupted"
    assert saved.steps[0].state == "pending"
