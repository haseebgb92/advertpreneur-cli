from pathlib import Path
from advertpreneur_cli.swarm import SwarmCoordinator, SwarmRole, ParcelStatus


def test_swarm_plan_mission(tmp_path: Path) -> None:
    events = []
    coord = SwarmCoordinator(tmp_path, event_sink=lambda k, t, d: events.append((k, t, d)))
    mission = coord.plan_mission("Implement JWT Authentication")

    assert mission.goal == "Implement JWT Authentication"
    assert len(mission.parcels) == 3
    assert mission.parcels[0].role == SwarmRole.ARCHITECT
    assert mission.parcels[1].role == SwarmRole.CODER
    assert mission.parcels[2].role == SwarmRole.REVIEWER
    assert len(events) == 1
    assert "Swarm Mission Planned" in events[0][1]


def test_swarm_parcel_lifecycle(tmp_path: Path) -> None:
    coord = SwarmCoordinator(tmp_path)
    coord.plan_mission("Add Rate Limiter")

    # Step 1: Architect
    p1 = coord.next_pending_parcel()
    assert p1 is not None
    assert p1.role == SwarmRole.ARCHITECT
    coord.start_parcel(p1.id)
    assert p1.status == ParcelStatus.IN_PROGRESS
    coord.complete_parcel(p1.id, "Plan completed", approved=True)
    assert p1.status == ParcelStatus.COMPLETED

    # Step 2: Coder
    p2 = coord.next_pending_parcel()
    assert p2 is not None
    assert p2.role == SwarmRole.CODER
    coord.start_parcel(p2.id)
    coord.complete_parcel(p2.id, "Code implemented", approved=True)

    # Step 3: Reviewer
    p3 = coord.next_pending_parcel()
    assert p3 is not None
    assert p3.role == SwarmRole.REVIEWER
    coord.start_parcel(p3.id)
    coord.complete_parcel(p3.id, "Audit clean", approved=True)

    # No more pending
    p4 = coord.next_pending_parcel()
    assert p4 is None
    assert coord.active_mission.status == "completed"
