from __future__ import annotations

from advertpreneur_cli.operations import OperationLedger, ProjectIdentity


def test_identity_classifies_local_staging_and_production():
    assert ProjectIdentity.from_url("http://localhost:3000").environment == "local"
    assert ProjectIdentity.from_url("https://staging.example.test").environment == "staging"
    assert ProjectIdentity.from_url("https://example.com").environment == "production"


def test_production_mutation_requires_plan_approval_and_checkpoint(tmp_path):
    ledger = OperationLedger(tmp_path)
    plan = ledger.plan("upload_plugin", ProjectIdentity.from_url("https://example.com"), ["plugin.zip"])
    assert plan.requires_approval is True
    assert ledger.approve(plan.id, plan.token) is True
    ledger.evidence(plan.id, "upload", "Plugin selected", url="https://example.com/wp-admin/plugin-install.php")
    ledger.checkpoint(plan.id, "uploaded")
    assert ledger.resume().stage == "uploaded"
