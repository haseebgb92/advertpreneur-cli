from advertpreneur_cli.missions import EvidenceItem, MissionStore


def test_mission_store_creates_plan_and_tracks_step_states(tmp_path):
    store = MissionStore(tmp_path)

    mission = store.create("Update homepage", [{"title": "Inspect", "kind": "inspect"}])

    assert mission.status == "planned"
    assert mission.steps[0].state == "pending"
    store.transition_step(mission.id, mission.steps[0].id, "active")
    assert store.load(mission.id).steps[0].state == "active"


def test_mission_rejects_completion_when_required_evidence_is_missing(tmp_path):
    store = MissionStore(tmp_path)
    mission = store.create(
        "Verify site",
        [{"title": "Inspect", "kind": "browser", "evidence": ["browser_observation"]}],
    )

    assert store.verify_step(mission.id, mission.steps[0].id) is False
    assert store.complete(mission.id) is False


def test_code_step_requires_checkpoint_and_verification_evidence(tmp_path):
    store = MissionStore(tmp_path)
    mission = store.create(
        "Patch",
        [{"title": "Patch", "kind": "code", "evidence": ["diff", "verification"]}],
    )
    step = mission.steps[0]

    store.record_evidence(mission.id, step.id, EvidenceItem("diff", "checkpoint:abc"))
    assert store.verify_step(mission.id, step.id) is False
    store.record_evidence(mission.id, step.id, EvidenceItem("verification", "pytest:pass"))
    assert store.verify_step(mission.id, step.id) is True


def test_attention_pauses_only_its_mission_and_resume_targets_same_step(tmp_path):
    store = MissionStore(tmp_path)
    mission = store.create("Upload", [{"title": "Confirm upload", "kind": "browser"}])
    request = store.request_attention(
        mission.id,
        mission.steps[0].id,
        "approval",
        ["approve", "deny"],
    )

    assert store.load(mission.id).status == "waiting"
    store.resolve_attention(request.id, "approve")
    resumed = store.load(mission.id)
    assert resumed.steps[0].state == "active"
    assert resumed.status == "active"
