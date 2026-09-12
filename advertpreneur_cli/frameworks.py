from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


@dataclass(frozen=True)
class FrameworkPack:
    id: str
    title: str
    triggers: tuple[str, ...]
    guidance: str


PACKS: Dict[str, FrameworkPack] = {
    "wordpress": FrameworkPack(
        "wordpress", "WordPress",
        ("wordpress", "wp-content", "wp-admin", "wp-cli", "theme", "plugin", "gutenberg", "acf"),
        """WordPress engineering rules: identify whether work belongs to theme, plugin, mu-plugin, or core before editing. Respect template hierarchy, hooks/actions/filters, enqueue APIs, capability/nonce checks, escaping/sanitization, REST/AJAX conventions, block/theme.json structure, and child-theme/plugin ownership. Never modify WordPress core or vendor code unless explicitly required. For visual work map rendered sections to template parts/blocks/ACF fields and reusable design tokens before coding. Prefer WP-CLI for deterministic inspection/validation when available.""",
    ),
    "woocommerce": FrameworkPack(
        "woocommerce", "WooCommerce",
        ("woocommerce", "wc_", "product", "checkout", "cart", "order"),
        """WooCommerce rules: prefer hooks/filters and documented extension points over copying templates. If overriding a template, verify the active WooCommerce template version and keep overrides minimal. Preserve cart/checkout/order state, nonce/capability rules, CRUD APIs, HPOS compatibility, and price/tax formatting. Distinguish frontend theme concerns from business logic that belongs in a plugin.""",
    ),
    "android": FrameworkPack(
        "android", "Android",
        ("android", "kotlin", "compose", "gradle", "activity", "viewmodel", "manifest"),
        """Android rules: trace UI -> state/ViewModel -> repository/service -> network/storage before changing behavior. Respect lifecycle, coroutine scope, Compose state ownership, manifest/permission requirements, Gradle source sets and API levels. Prefer the existing architecture and dependency injection pattern. For verification run the exact relevant Gradle task; never infer a build PASS from source inspection.""",
    ),
    "nestjs": FrameworkPack(
        "nestjs", "NestJS",
        ("nestjs", "@nestjs", "controller", "provider", "guard", "interceptor", "dto"),
        """NestJS rules: trace module -> controller -> provider/service -> persistence/integration and guards/interceptors/pipes. Preserve DI boundaries and DTO validation. Authentication/authorization claims require inspection of the actual guard/service and runtime wiring. Prefer focused module tests/build/typecheck over broad speculation.""",
    ),
    "nextjs": FrameworkPack(
        "nextjs", "Next.js",
        ("next.js", "nextjs", "app router", "pages router", "server component", "route.ts"),
        """Next.js rules: first identify App Router vs Pages Router and server/client component boundaries. Preserve data/cache/revalidation semantics, route handlers, metadata and environment separation. Keep browser-only APIs out of server components and secrets out of client bundles. Validate with the project's real lint/typecheck/build commands.""",
    ),
    "shopify": FrameworkPack(
        "shopify", "Shopify/Liquid",
        ("shopify", "liquid", "section", "schema", "theme editor"),
        """Shopify theme rules: understand layout -> template JSON -> sections -> snippets -> assets. Preserve merchant-configurable section schema and blocks. Avoid hard-coding content that belongs in settings/metafields. Keep Liquid logic simple, use native image/url/filter primitives, and map visual references into reusable section settings and CSS tokens.""",
    ),
    "web": FrameworkPack(
        "web", "Web UI",
        ("html", "css", "javascript", "website", "landing page", "frontend", "ui", "design"),
        """Web UI rules: reverse-engineer layout before styling: viewport, container widths, section bounds, spacing rhythm, typography, colors, radii, borders, imagery and responsive behavior. Reuse project tokens/components when present. For reference-site work use browser evidence/design maps rather than guessing from memory.""",
    ),
}


class FrameworkIntelligence:
    """Small local framework detector + task-aware handbook.

    Detection is deterministic and costs zero model tokens. Only compact guidance for
    relevant detected frameworks is injected into a provider call.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.detected = self._detect()

    def _package_json(self) -> dict:
        for p in (self.root / "package.json", self.root / "apps" / "api" / "package.json", self.root / "apps" / "web" / "package.json"):
            if p.exists():
                try:
                    return json.loads(p.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    pass
        return {}

    def _detect(self) -> List[str]:
        out: List[str] = []
        paths = {p.name.lower() for p in self.root.iterdir()} if self.root.exists() else set()
        pkg = json.dumps(self._package_json()).lower()

        if "wp-content" in paths or (self.root / "wp-config.php").exists() or (self.root / "style.css").exists() and "theme name:" in (self.root / "style.css").read_text(encoding="utf-8", errors="ignore").lower()[:4000]:
            out.append("wordpress")
        # Theme/plugin repos often do not contain wp-content itself.
        php_candidates = list(self.root.glob("*.php"))[:20]
        if "wordpress" not in out:
            for p in php_candidates:
                try:
                    head = p.read_text(encoding="utf-8", errors="ignore")[:5000].lower()
                except OSError:
                    continue
                if "plugin name:" in head or "add_action(" in head or "add_filter(" in head:
                    out.append("wordpress")
                    break
        if "woocommerce" in pkg or any((self.root / x).exists() for x in ("woocommerce", "templates/woocommerce")):
            out.append("woocommerce")
        if (self.root / "android").exists() or (self.root / "app" / "src" / "main" / "AndroidManifest.xml").exists() or any(self.root.glob("**/build.gradle.kts")):
            # Require a useful Android signal to avoid classifying arbitrary Gradle repos.
            if (self.root / "android").exists() or any(self.root.glob("**/AndroidManifest.xml")):
                out.append("android")
        if "@nestjs/" in pkg or "@nestjs/core" in pkg:
            out.append("nestjs")
        if '"next"' in pkg or "nextjs" in pkg:
            out.append("nextjs")
        if (self.root / "templates").exists() and (self.root / "sections").exists() and list((self.root / "sections").glob("*.liquid")):
            out.append("shopify")
        if any((self.root / x).exists() for x in ("index.html", "src", "app")):
            out.append("web")
        return list(dict.fromkeys(out))

    def context(self, task: str, max_chars: int = 1800) -> str:
        low = (task or "").lower()
        chosen: List[FrameworkPack] = []
        for ident in self.detected:
            pack = PACKS.get(ident)
            if not pack:
                continue
            # Always include a single strongly-detected framework. In mixed repos, only
            # inject packs whose terms appear in the task to avoid unnecessary context.
            if len(self.detected) == 1 or any(t in low for t in pack.triggers):
                chosen.append(pack)
        # Explicit task terms can opt in a pack even if repository detection is weak.
        for pack in PACKS.values():
            if pack in chosen:
                continue
            if any(t in low for t in pack.triggers):
                chosen.append(pack)
        if not chosen:
            return ""
        rows = ["Relevant engineering intelligence (local framework pack):"]
        for pack in chosen[:2]:
            rows.append(f"{pack.title}: {pack.guidance}")
        return "\n".join(rows)[:max_chars]

    def summary(self) -> str:
        if not self.detected:
            return "No framework pack confidently detected"
        return ", ".join(PACKS[x].title for x in self.detected if x in PACKS)
