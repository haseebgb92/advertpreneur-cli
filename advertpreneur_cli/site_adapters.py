from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class SiteAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class SiteProfile:
    adapter: str
    base_url: str

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"adapter": self.adapter, "base_url": self.base_url}, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "SiteProfile | None":
        if not path.exists(): return None
        row = json.loads(path.read_text(encoding="utf-8"))
        return cls(adapter=str(row["adapter"]), base_url=str(row["base_url"]))


@dataclass(frozen=True)
class SiteAdapter:
    key: str
    label: str
    routes: dict[str, str]
    upload_selector: str = 'input[type="file"]'

    def route(self, name: str, base_url: str) -> str:
        path = self.routes.get(name)
        if path is None: raise SiteAdapterError(f"{self.label} does not support route: {name}")
        base = base_url.rstrip("/")
        if self.key == "wordpress" and base.endswith("/wp-admin"):
            base = base[: -len("/wp-admin")]
        return base + "/" + path.lstrip("/")


class SiteAdapterRegistry:
    def __init__(self) -> None:
        self.adapters = {
            "wordpress": SiteAdapter("wordpress", "WordPress wp-admin", {"dashboard":"wp-admin/", "posts":"wp-admin/edit.php", "pages":"wp-admin/edit.php?post_type=page", "plugins":"wp-admin/plugins.php", "themes":"wp-admin/themes.php", "settings":"wp-admin/options-general.php", "media":"wp-admin/upload.php"}),
            "hostinger": SiteAdapter("hostinger", "Hostinger hPanel", {"dashboard":"websites", "file_manager":"hosting/file-manager", "plugins":"hosting/file-manager", "themes":"hosting/file-manager"}),
            "cpanel": SiteAdapter("cpanel", "cPanel", {"dashboard":"", "file_manager":"frontend/jupiter/filemanager/index.html", "plugins":"frontend/jupiter/filemanager/index.html", "themes":"frontend/jupiter/filemanager/index.html"}),
            "plesk": SiteAdapter("plesk", "Plesk", {"dashboard":"", "file_manager":"smb/webspace/list", "plugins":"smb/webspace/list", "themes":"smb/webspace/list"}),
        }

    def get(self, key: str) -> SiteAdapter:
        adapter = self.adapters.get(str(key).lower())
        if not adapter: raise SiteAdapterError(f"Unknown site adapter: {key}")
        return adapter

    def detect(self, url: str) -> SiteAdapter:
        lower = str(url).lower(); host = urlparse(url).netloc.lower()
        if "wp-admin" in lower or "wp-login.php" in lower: return self.get("wordpress")
        if "hostinger" in host or "hpanel" in host: return self.get("hostinger")
        if ":2083" in host or "cpanel" in lower: return self.get("cpanel")
        if ":8443" in host or "plesk" in lower: return self.get("plesk")
        raise SiteAdapterError("No supported site adapter matches this URL")
