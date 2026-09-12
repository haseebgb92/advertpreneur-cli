from __future__ import annotations

import json
from pathlib import Path

import pytest

from advertpreneur_cli.site_adapters import SiteAdapterRegistry, SiteProfile, SiteAdapterError


@pytest.mark.parametrize(("url", "expected"), [
    ("https://example.test/wp-admin/plugins.php", "wordpress"),
    ("https://hpanel.hostinger.com/websites", "hostinger"),
    ("https://server.test:2083/cpsess123/frontend", "cpanel"),
    ("https://server.test:8443/smb/webspace", "plesk"),
])
def test_registry_detects_supported_surfaces(url: str, expected: str):
    assert SiteAdapterRegistry().detect(url).key == expected


def test_adapter_routes_and_profile_are_safe(tmp_path: Path):
    registry = SiteAdapterRegistry()
    adapter = registry.get("wordpress")
    assert adapter.route("plugins", "https://example.test/wp-admin/") == "https://example.test/wp-admin/plugins.php"
    with pytest.raises(SiteAdapterError):
        adapter.route("file_manager", "https://example.test/wp-admin/")
    profile = SiteProfile(adapter="hostinger", base_url="https://hpanel.hostinger.com")
    path = tmp_path / "site-profile.json"
    profile.save(path)
    assert SiteProfile.load(path) == profile
    assert "password" not in json.loads(path.read_text()).keys()
