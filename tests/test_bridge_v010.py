from __future__ import annotations

import json
import threading
from pathlib import Path

from advertpreneur_cli.bridge import BridgeClient
from advertpreneur_cli.bridge_server import create_server
from advertpreneur_cli.notifications import TaskNotifier
from advertpreneur_cli.sessions import SessionRecord


def test_browser_bridge_round_trip(tmp_path: Path):
    server = create_server(0, tmp_path / "pairs.json")
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = BridgeClient(tmp_path / "app", port=port, auto_start=False)
        cli_token = "cli-secret"
        pair_token = "pair-secret"
        row = client.register("s1", "Discount App", str(tmp_path), "gemma4:31b", cli_token, pair_token)
        assert row["paired"] is False
        code = row["pair_code"]
        paired = server.state.pair({
            "session_id": "s1",
            "pair_code": code,
            "conversation_id": "chat-1",
            "conversation_url": "https://chatgpt.com/c/chat-1",
        })
        assert paired["pair_token"] == pair_token

        dispatch = client.publish("s1", cli_token, {"status": "completed", "result": "PASS"})
        assert dispatch.paired is True
        event = server.state.next_outbound("s1", pair_token, "chat-1")
        assert event and event["payload"]["result"] == "PASS"
        server.state.delivered({
            "session_id": "s1", "pair_token": pair_token, "conversation_id": "chat-1", "event_id": dispatch.event_id,
        })
        server.state.reply({
            "session_id": "s1", "pair_token": pair_token, "conversation_id": "chat-1", "event_id": dispatch.event_id,
            "text": "Run the regression tests next.",
        })
        reply = client.poll_reply("s1", cli_token, dispatch.event_id)
        assert reply is not None
        assert reply.text == "Run the regression tests next."
    finally:
        server.shutdown()
        server.server_close()


def test_bridge_session_serialization_keeps_pairing_settings():
    rec = SessionRecord(
        id="x", name="X", project="P", provider="cloud", model="m",
        bridge_enabled=True, bridge_autopilot=False, bridge_pair_token="secret",
    )
    restored = SessionRecord.from_dict(rec.to_dict())
    assert restored.bridge_enabled is True
    assert restored.bridge_autopilot is False
    assert restored.bridge_pair_token == "secret"


def test_bridge_extension_manifest_is_packaged(tmp_path: Path):
    client = BridgeClient(tmp_path)
    path = client.extension_path()
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert manifest["name"] == "Advertpreneur Browser Bridge"
    assert (path / "content.js").exists()
    assert (path / "background.js").exists()
    assert (path / "popup.html").exists()


def test_sound_uses_real_in_memory_wav():
    data = TaskNotifier._tone_wav("done")
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"
    assert len(data) > 1000


def test_bridge_reply_is_exactly_once_and_acknowledged(tmp_path: Path):
    server = create_server(0, tmp_path / "pairs.json")
    state = server.state
    try:
        reg = state.register({"session_id":"s1","session_name":"S1","project":"P","model":"m","cli_token":"cli","pair_token":"pair"})
        state.pair({"session_id":"s1","pair_code":reg["pair_code"],"conversation_id":"chat-1","conversation_url":"https://chatgpt.com/c/chat-1"})
        out = state.publish({"session_id":"s1","cli_token":"cli","payload":{"result":"PASS"}})
        eid = out["event_id"]
        state.submitted({"session_id":"s1","pair_token":"pair","conversation_id":"chat-1","event_id":eid})
        assert state.next_outbound("s1","pair","chat-1")["state"] == "submitted"
        packet={"session_id":"s1","pair_token":"pair","conversation_id":"chat-1","event_id":eid,"text":"next"}
        state.reply(packet); state.reply(packet)
        assert len(state.sessions["s1"].inbound) == 1
        reply = state.pop_reply("s1","cli",eid)
        assert reply and reply["text"] == "next"
        assert state.pop_reply("s1","cli",eid) is None
        event = next(x for x in state.sessions["s1"].outbound if x["event_id"] == eid)
        assert event["state"] == "acknowledged"
    finally:
        server.server_close()


def test_bridge_extension_uses_event_anchored_correlation():
    root = Path(__file__).resolve().parents[1]
    js = (root / "browser-extension" / "content.js").read_text(encoding="utf-8")
    assert "ADP Bridge Event:" in js
    assert "assistantNodeAfterEvent" in js
    assert "/v1/public/submitted" in js
    assert "assistantFingerprint" not in js
    assert 'event.state === "submitted"' in js


def test_bridge_status_unregistered_session_does_not_404(tmp_path: Path):
    server = create_server(0, tmp_path / "pairs.json")
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = BridgeClient(tmp_path / "app", port=port, auto_start=False)
        assert client.health() is True
        st = client.status("unregistered-session", "random-token")
        assert st["session_id"] == "unregistered-session"
        assert st["paired"] is False
        code = client.rotate_code("unregistered-session", "random-token")
        assert len(code) == 6
    finally:
        server.shutdown()
        server.server_close()


def test_browser_status_includes_live_progress(tmp_path: Path):
    server = create_server(0, tmp_path / "pairs.json")
    try:
        state = server.state
        registered = state.browser_register({"provider_id": "browser-1", "token": "secret"})
        assert registered["provider_id"] == "browser-1"
        state.browser_progress({"provider_id": "browser-1", "token": "secret", "stage": "login_needed", "detail": "Sign in in browser"})
        status = state.browser_status()
        assert status["progress"]["stage"] == "login_needed"
        assert status["progress"]["detail"] == "Sign in in browser"
    finally:
        server.server_close()


def test_extension_contains_upload_and_wordpress_state_actions():
    root = Path(__file__).resolve().parents[1]
    js = (root / "browser-extension" / "background.js").read_text(encoding="utf-8")
    assert 'action === "upload"' in js
    assert 'action === "wordpress_state"' in js





