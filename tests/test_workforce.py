from __future__ import annotations

from advertpreneur_cli.workforce import Workforce


def test_workforce_selects_specialists_from_live_site_intent(tmp_path):
    workforce = Workforce(tmp_path)
    assert workforce.select("Upload a plugin through wp-admin").key == "wordpress_steward"
    assert workforce.select("Hostinger deployment failed with a 500").key == "incident_investigator"
    assert workforce.select("Find official resolution for cPanel error").key == "research_resolution"


def test_due_tasks_activate_but_production_mutations_require_approval(tmp_path):
    workforce = Workforce(tmp_path)
    task = workforce.enqueue("wordpress_steward", "Update production plugin", due_at=0, mutation=True, production=True)
    due = workforce.due(now=1)
    assert due[0].id == task.id
    assert due[0].state == "awaiting_approval"


def test_verified_repeated_operations_become_learned_routines(tmp_path):
    workforce = Workforce(tmp_path)
    workforce.record_outcome("wordpress_steward", "upload plugin", "plugin upload verified", verified=True)
    workforce.record_outcome("wordpress_steward", "upload plugin", "plugin upload verified", verified=True)
    learned = workforce.learned("wordpress_steward")
    assert learned[0]["confidence"] == "proven"
