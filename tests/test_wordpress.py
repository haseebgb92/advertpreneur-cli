from __future__ import annotations

from advertpreneur_cli.wordpress import DeleteTarget, DeletionProposal, wordpress_login_state


def test_wordpress_login_detection_is_url_and_form_aware():
    assert wordpress_login_state("https://site.test/wp-login.php", {"password": True}) == "login_needed"
    assert wordpress_login_state("https://site.test/wp-admin/", {"dashboard": True}) == "authenticated"
    assert wordpress_login_state("https://site.test/wp-admin/plugins.php", {}) == "unknown"


def test_delete_proposal_requires_exact_approval():
    proposal = DeletionProposal.create([DeleteTarget("akismet", "plugin", "inactive")])
    assert proposal.approve("wrong") is False
    assert proposal.approve(proposal.token) is True
    assert proposal.approved is True


def test_delete_proposal_describes_every_target():
    proposal = DeletionProposal.create([
        DeleteTarget("Current Theme", "theme", "ACTIVE — removal may break the site"),
        DeleteTarget("Old Plugin", "plugin", "inactive"),
    ])
    text = proposal.describe()
    assert "Current Theme" in text
    assert "ACTIVE" in text
    assert proposal.token in text
