from __future__ import annotations

from advertpreneur_cli.panel_playbooks import PanelPlaybookRegistry, PlaybookError


def test_wordpress_plugin_upload_playbook_has_verified_steps():
    playbook = PanelPlaybookRegistry().get("wordpress", "upload_plugin")
    assert playbook.steps[0].route == "plugins"
    assert any(step.verify for step in playbook.steps)
    assert "input[type=" in playbook.steps[1].selector


def test_host_panels_have_file_manager_upload_extract_and_verify():
    registry = PanelPlaybookRegistry()
    for adapter in ("hostinger", "cpanel", "plesk"):
        playbook = registry.get(adapter, "deploy_archive")
        assert [step.action for step in playbook.steps] == ["navigate", "upload", "extract", "verify"]


def test_unknown_playbook_is_rejected():
    try:
        PanelPlaybookRegistry().get("wordpress", "delete_everything")
    except PlaybookError:
        return
    raise AssertionError("unknown playbook must fail closed")
