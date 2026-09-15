from __future__ import annotations

import base64
import json
import os
import queue
import re
import shutil
import threading
import time
from urllib.parse import urlsplit, urlunsplit
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from .bridge import BridgeClient, BridgeError
from .browser_learning import BrowserRoutineStore, BrowserRoutineError


class BrowserUnavailable(RuntimeError):
    pass


@dataclass
class _BrowserCall:
    action: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


class BrowserController:
    """Local browser automation with Playwright isolated from the CLI event loop.

    Playwright's synchronous API owns an asyncio loop internally. Running it on the
    same thread as prompt_toolkit can leave that loop visible to prompt_toolkit and
    cause ``asyncio.run() cannot be called from a running event loop`` after a browser
    tool call. All Playwright work therefore lives on one dedicated worker thread.

    Browser selection is local-only and costs no model tokens. In ``auto`` mode we
    first try to attach to an already-running Chromium/Edge instance exposed through
    the DevTools protocol (default http://127.0.0.1:9222). If that is unavailable we
    launch an isolated installed Edge/Chrome/Playwright Chromium instance.
    """

    def __init__(self, project: Path, visible: bool = True) -> None:
        self.project = project.resolve()
        self.visible = bool(visible)
        self.browser_dir = self.project / ".advertpreneur" / "browser"
        self.browser_dir.mkdir(parents=True, exist_ok=True)
        self.slots_path = self.browser_dir / "slots.json"
        self.routines = BrowserRoutineStore(self.browser_dir)
        self.cdp_url = str(os.environ.get("ADP_BROWSER_CDP_URL") or "http://127.0.0.1:9222").strip()
        self.bridge = BridgeClient(Path.home() / ".advertpreneur-cli")
        self._extension_last_check = 0.0
        self._extension_live = False
        self._calls: queue.Queue[_BrowserCall] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._running = False
        self._provider = "idle"
        self._current_url = ""
        self._current_title = ""
        self.last_learned_routine = ""

        # The following are touched only by the worker thread.
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._attached_cdp = False
        self._owns_context = False

    def get_slots(self) -> dict[str, dict[str, Any]]:
        if not self.slots_path.exists():
            return {}
        try:
            data = json.loads(self.slots_path.read_text(encoding="utf-8"))
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            return {}

    def record_slot(self, slot: str, url: str, title: str = "") -> None:
        slots = self.get_slots()
        slots[str(slot)] = {
            "url": str(url),
            "title": str(title),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            tmp = self.slots_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(slots, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.slots_path)
        except Exception:
            pass

    @property
    def running(self) -> bool:
        with self._state_lock:
            return bool(self._running)

    @property
    def provider(self) -> str:
        with self._state_lock:
            return str(self._provider)

    @property
    def current_url(self) -> str:
        with self._state_lock:
            return str(self._current_url)

    @property
    def current_title(self) -> str:
        with self._state_lock:
            return str(self._current_title)

    def _set_state(self, *, running: bool | None = None, provider: str | None = None, url: str | None = None, title: str | None = None) -> None:
        with self._state_lock:
            if running is not None:
                self._running = bool(running)
            if provider is not None:
                self._provider = str(provider)
            if url is not None:
                self._current_url = str(url)
            if title is not None:
                self._current_title = str(title)

    def _extension_available(self, wait_seconds: float = 0.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(wait_seconds))
        while True:
            now = time.monotonic()
            if now - self._extension_last_check > 0.8 or wait_seconds:
                self._extension_last_check = now
                try:
                    status = self.bridge.browser_status()
                    self._extension_live = bool(status.get("available"))
                except Exception:
                    self._extension_live = False
            if self._extension_live or time.monotonic() >= deadline:
                return self._extension_live
            time.sleep(0.2)

    def _extension_call(self, action: str, timeout: float = 45.0, **args: Any) -> dict[str, Any]:
        if not self._extension_available(wait_seconds=2.5):
            raise BrowserUnavailable("Advertpreneur Browser Bridge extension is not connected")
        try:
            row = self.bridge.browser_command(action, args, timeout=timeout)
        except BridgeError as exc:
            self._extension_live = False
            raise BrowserUnavailable(str(exc)) from exc
        is_running = bool(row.get("running", True)) if action == "status" else action != "close"
        self._set_state(
            running=is_running,
            provider="existing-edge/extension" if action != "close" else "idle",
            url=str(row.get("url") or ("" if action == "close" else self.current_url)),
            title=str(row.get("title") or ("" if action == "close" else self.current_title)),
        )
        return row

    @staticmethod
    def _extension_summary(row: dict[str, Any], action: str) -> str:
        if action == "status":
            progress = row.get("progress") if isinstance(row.get("progress"), dict) else {}
            state = str(progress.get("stage") or "ready")
            detail = str(progress.get("detail") or "")
            location = str(progress.get("url") or "")
            return f"Browser bridge {'connected' if row.get('available') is not False else 'offline'} · existing-edge/extension · {state} · {detail} · {location}".strip(" ·")
        if action == "navigate":
            return f"Navigated · existing Edge · {row.get('title') or ''} · {row.get('url') or ''}".strip(" ·")
        return json.dumps(row, ensure_ascii=False)

    def set_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible != self.visible and self.running:
            self.close()
        self.visible = visible

    def _ensure_worker(self) -> None:
        with self._thread_lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._worker_main, name="AdvertpreneurBrowser", daemon=True)
            self._thread.start()

    def _rpc(self, action: str, *args: Any, timeout: float = 75.0, **kwargs: Any) -> Any:
        self._ensure_worker()
        call = _BrowserCall(action=action, args=args, kwargs=kwargs)
        self._calls.put(call)
        if not call.done.wait(timeout=max(1.0, float(timeout))):
            raise BrowserUnavailable(f"Browser {action} timed out after {int(timeout)}s")
        if call.error is not None:
            raise call.error
        return call.result

    def _worker_main(self) -> None:
        while True:
            call = self._calls.get()
            try:
                if call.action == "__shutdown__":
                    call.result = self._close_direct()
                    return
                fn = getattr(self, f"_{call.action}_direct", None)
                if not fn:
                    raise BrowserUnavailable(f"Unknown browser operation: {call.action}")
                call.result = fn(*call.args, **call.kwargs)
            except BaseException as exc:  # propagate into the CLI thread as a normal tool error
                call.error = exc
            finally:
                call.done.set()

    @staticmethod
    def _normalize_url(url: str) -> str:
        text = str(url or "").strip()
        # Common composer punctuation after a URL should not become part of the host.
        while text and text[-1] in {",", ";"}:
            text = text[:-1].rstrip()
        if not re.match(r"^https?://", text, flags=re.I):
            raise ValueError("Browser navigation requires an http/https URL")
        return text

    def _ensure_direct(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise BrowserUnavailable(
                "Browser control needs the Python 'playwright' package. Re-run INSTALL.ps1 or run: python -m pip install playwright"
            ) from exc

        self._pw = sync_playwright().start()
        errors: list[str] = []

        # Prefer the user's already-running Edge/Chrome when it was started with a
        # DevTools port. This keeps the user's normal profile/extensions available.
        if self.cdp_url:
            try:
                self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url, timeout=1400)
                contexts = list(self._browser.contexts)
                if not contexts:
                    raise RuntimeError("connected browser exposed no contexts")
                self._context = contexts[0]
                # Create one ADP-owned tab inside the user's existing browser/profile;
                # do not hijack or navigate an arbitrary existing tab.
                self._page = self._context.new_page()
                self._attached_cdp = True
                self._owns_context = False
                self._set_state(running=True, provider="existing-edge/cdp", url=self._page.url, title="")
                return self._page
            except Exception as exc:
                errors.append(f"existing-browser {self.cdp_url}: {exc}")
                self._browser = self._context = self._page = None
                self._attached_cdp = False

        launch_options = {"headless": not self.visible}
        launch_provider = ""
        for channel in ("msedge", "chrome", None):
            try:
                if channel:
                    self._browser = self._pw.chromium.launch(channel=channel, **launch_options)
                    launch_provider = f"isolated-{channel}"
                else:
                    self._browser = self._pw.chromium.launch(**launch_options)
                    launch_provider = "isolated-playwright-chromium"
                break
            except Exception as exc:
                errors.append(f"{channel or 'playwright-chromium'}: {exc}")

        if self._browser is None:
            candidates = [
                shutil.which("chromium"), shutil.which("chromium-browser"),
                shutil.which("google-chrome"), shutil.which("microsoft-edge"),
            ]
            for executable in [x for x in candidates if x]:
                try:
                    self._browser = self._pw.chromium.launch(executable_path=executable, **launch_options)
                    launch_provider = f"isolated-{Path(executable).name}"
                    break
                except Exception as exc:
                    errors.append(f"{executable}: {exc}")

        if self._browser is None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None
            raise BrowserUnavailable(
                "Could not attach to an existing debug-enabled Edge or launch Edge/Chrome/Chromium. "
                "If needed run: python -m playwright install chromium\n" + "\n".join(errors[-3:])
            )

        self._context = self._browser.new_context(viewport={"width": 1440, "height": 1000})
        self._owns_context = True
        self._page = self._context.new_page()
        self._set_state(running=True, provider=launch_provider or "isolated-browser", url=self._page.url, title="")
        return self._page

    def _update_page_state(self, page) -> None:
        try:
            title = page.title()
        except Exception:
            title = ""
        self._set_state(running=True, url=str(page.url or ""), title=title)

    def _close_direct(self) -> str:
        # With CDP attachment, closing the browser/context could terminate or disturb
        # the user's actual Edge profile. Close only the ADP-owned tab and disconnect.
        try:
            if self._page:
                self._page.close()
        except Exception:
            pass
        if not self._attached_cdp:
            try:
                if self._context:
                    self._context.close()
            except Exception:
                pass
            try:
                if self._browser:
                    self._browser.close()
            except Exception:
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._page = self._context = self._browser = self._pw = None
        self._attached_cdp = False
        self._owns_context = False
        self._set_state(running=False, provider="idle", url="", title="")
        return "Browser closed."

    def close(self) -> str:
        # Prefer closing only ADP's extension-owned tab in the user's normal browser.
        if self._extension_available(wait_seconds=0.4):
            try:
                self._extension_call("close", timeout=10)
            except Exception:
                pass
        with self._thread_lock:
            thread = self._thread
        if not thread or not thread.is_alive():
            self._set_state(running=False, provider="idle", url="", title="")
            return "Browser closed."
        call = _BrowserCall(action="__shutdown__")
        self._calls.put(call)
        call.done.wait(timeout=10)
        if call.error:
            raise call.error
        thread.join(timeout=2)
        with self._thread_lock:
            if self._thread is thread:
                self._thread = None
        return str(call.result or "Browser closed.")

    def _status_direct(self) -> str:
        if self._page is None:
            return f"Browser idle · existing-browser attach target {self.cdp_url}"
        self._update_page_state(self._page)
        return f"Browser running · provider {self.provider} · {self.current_title or '(untitled)'} · {self.current_url}"

    def status(self) -> str:
        if self._extension_available(wait_seconds=0.8):
            try:
                row = self._extension_call("status", timeout=8)
                return self._extension_summary(row, "status")
            except Exception:
                pass
        if not self.running:
            return f"Browser idle · extension unavailable · CDP fallback {self.cdp_url}"
        return str(self._rpc("status", timeout=10))

    def context_hint(self) -> str:
        if not self.running and not self._extension_available(wait_seconds=0.0):
            return ""
        url = self.current_url
        if not url or url == "about:blank":
            return ""
        names = self.routines.names()[:6]
        routine_hint = f" · learned routines: {', '.join(names)}" if names else ""
        return (
            f"Local browser context (already open; use this page unless the user gives a different real URL): "
            f"{url} · provider {self.provider}{routine_hint}"
        )

    def _navigate_direct(self, url: str, wait_until: str = "domcontentloaded") -> str:
        url = self._normalize_url(url)
        page = self._ensure_direct()
        response = page.goto(url, wait_until=wait_until, timeout=45000)
        self._update_page_state(page)
        status = response.status if response else "?"
        return f"Navigated · HTTP {status} · {self.current_title} · {self.current_url} · provider {self.provider}"

    def navigate(self, url: str, wait_until: str = "domcontentloaded", tab: str = "work") -> str:
        url = self._normalize_url(url)
        if self._extension_available(wait_seconds=2.5):
            row = self._extension_call("navigate", timeout=45, url=url, wait_until=wait_until, timeout_ms=35000, tab=tab)
            self.routines.record("navigate", {"url": url, "wait_until": wait_until, "tab": tab}, row)
            self.record_slot(tab, url, str(row.get("title") or ""))
            return self._extension_summary(row, "navigate")
        if tab != "work":
            raise BrowserUnavailable("Named browser tabs require the connected Advertpreneur Browser Bridge extension")
        result = str(self._rpc("navigate", url, wait_until, timeout=60))
        self.routines.record("navigate", {"url": url, "wait_until": wait_until}, {"url": self.current_url, "verified": True})
        self.record_slot(tab, url, self.current_title)
        return result

    def wordpress_state(self) -> dict[str, Any]:
        if self._extension_available(wait_seconds=1.0):
            return self._extension_call("wordpress_state", timeout=15)
        page = self._rpc("inspect", "body", timeout=25)  # Ensure a fallback page exists.
        _ = page
        url = self.current_url
        return {"provider": self.provider, "url": url, "login_needed": "wp-login.php" in url.lower(), "authenticated": "/wp-admin" in url.lower() and "wp-login.php" not in url.lower(), "verified": True}

    def upload(self, file_path: Path, selector: str = 'input[type="file"]') -> str:
        path = Path(file_path).resolve()
        if not path.is_file():
            raise BrowserUnavailable(f"Upload file does not exist: {path}")
        if self._extension_available(wait_seconds=1.0):
            row = self._extension_call("upload", timeout=30, selector=selector, file_path=str(path))
            return f"Upload selected · {row.get('selected') or path.name} · existing-edge/extension · {row.get('url') or ''}".strip(" ·")
        return str(self._rpc("upload", str(path), selector, timeout=35))

    def _upload_direct(self, file_path: str, selector: str) -> str:
        page = self._ensure_direct()
        page.locator(selector).first.set_input_files(file_path, timeout=15000)
        self._update_page_state(page)
        return f"Upload selected · {Path(file_path).name} · provider {self.provider}"

    def _screenshot_direct(self, name: str = "page", selector: str = "", full_page: bool = True) -> str:
        page = self._ensure_direct()
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name or "page").strip("-.") or "page"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.browser_dir / f"{safe}-{stamp}.png"
        if selector:
            page.locator(selector).first.screenshot(path=str(path))
        else:
            page.screenshot(path=str(path), full_page=bool(full_page))
        self._update_page_state(page)
        rel = path.relative_to(self.project).as_posix()
        viewport = page.viewport_size or {"width": "?", "height": "?"}
        return f"Screenshot saved · {rel} · {viewport.get('width')}x{viewport.get('height')} viewport · cloud tokens 0"

    def screenshot(self, name: str = "page", selector: str = "", full_page: bool = True, tab: str = "work") -> str:
        if self._extension_available(wait_seconds=1.0):
            row = self._extension_call("screenshot", timeout=25, name=name, selector=selector, full_page=full_page, tab=tab)
            data_url = str(row.get("data_url") or "")
            if not data_url.startswith("data:image/"):
                raise BrowserUnavailable("Existing-browser extension returned no screenshot data")
            header, encoded = data_url.split(",", 1)
            ext = "jpg" if "jpeg" in header else "png"
            safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "page")).strip("-.") or "page"
            path = self.browser_dir / f"{safe}.{ext}"
            path.write_bytes(base64.b64decode(encoded))
            return f"Screenshot · {path} · existing-edge/extension · viewport capture"
        if tab != "work":
            raise BrowserUnavailable("Named browser tabs require the connected Advertpreneur Browser Bridge extension")
        return str(self._rpc("screenshot", name, selector, full_page, timeout=45))

    def _inspect_direct(self, selector: str = "body", max_elements: int = 60) -> str:
        page = self._ensure_direct()
        max_elements = max(5, min(150, int(max_elements)))
        data = page.locator(selector).first.evaluate(
            """(root, maxElements) => {
              const clean = s => (s || '').replace(/\\s+/g,' ').trim().slice(0,220);
              const candidates = [root, ...root.querySelectorAll('header,nav,main,section,article,aside,footer,h1,h2,h3,h4,p,a,button,img,input,form')];
              const out = [];
              for (const el of candidates) {
                if (out.length >= maxElements) break;
                const r = el.getBoundingClientRect();
                if (r.width < 2 || r.height < 2) continue;
                const s = getComputedStyle(el);
                out.push({
                  tag: el.tagName.toLowerCase(), id: el.id || '',
                  cls: (typeof el.className === 'string' ? el.className : '').split(/\\s+/).slice(0,4).join('.'),
                  text: clean(el.innerText || el.getAttribute('alt') || el.getAttribute('aria-label')),
                  x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
                  display: s.display, position: s.position, font: `${s.fontWeight} ${s.fontSize}/${s.lineHeight} ${s.fontFamily}`.slice(0,140),
                  color: s.color, bg: s.backgroundColor, radius: s.borderRadius,
                  href: el.href || '', src: el.currentSrc || el.src || ''
                });
              }
              return out;
            }""",
            max_elements,
        )
        self._update_page_state(page)
        lines = [f"Page · {self.current_title} · {self.current_url} · provider {self.provider}"]
        for e in data:
            ident = ("#" + e["id"]) if e.get("id") else (("." + e["cls"]) if e.get("cls") else "")
            lines.append(
                f"{e['tag']}{ident} [{e['x']},{e['y']} {e['w']}x{e['h']}] font={e['font']} color={e['color']} bg={e['bg']} radius={e['radius']} text={e['text']!r}"
            )
        return "\n".join(lines)

    def inspect(self, selector: str = "body", max_elements: int = 60, tab: str = "work") -> str:
        if self._extension_available(wait_seconds=0.8):
            row = self._extension_call("inspect", timeout=25, selector=selector, max_elements=max_elements, tab=tab)
            return json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        if tab != "work":
            raise BrowserUnavailable("Named browser tabs require the connected Advertpreneur Browser Bridge extension")
        return str(self._rpc("inspect", selector, max_elements, timeout=35))

    def selector_state(self, selector: str, tab: str = "work") -> dict[str, Any]:
        """Return observed count and visibility for a selector in a named extension tab."""
        if not selector:
            raise BrowserUnavailable("Selector state requires an observed selector")
        if not self._extension_available(wait_seconds=0.8):
            raise BrowserUnavailable("Named selector state requires the connected Advertpreneur Browser Bridge extension")
        row = self._extension_call("selector_state", timeout=12, selector=selector, tab=tab)
        return {"count": int(row.get("count") or 0), "visible": bool(row.get("visible"))}

    def visible_controls(self, tab: str = "work") -> dict[str, Any]:
        if not self._extension_available(wait_seconds=0.8):
            raise BrowserUnavailable("Named visible controls require the connected Advertpreneur Browser Bridge extension")
        row = self._extension_call("visible_controls", timeout=12, tab=tab)
        controls = row.get("controls") if isinstance(row.get("controls"), list) else []
        return {"url": str(row.get("url") or ""), "title": str(row.get("title") or ""), "controls": [item for item in controls if isinstance(item, dict)][:24]}

    def _click_direct(self, selector: str) -> str:
        page = self._ensure_direct()
        page.locator(selector).first.click(timeout=15000)
        page.wait_for_timeout(250)
        self._update_page_state(page)
        return f"Clicked {selector!r} · {self.current_url}"

    def click(self, selector: str, tab: str = "work", capture_tab: str = "") -> str:
        if self._extension_available(wait_seconds=0.5):
            row = self._extension_call("click", timeout=25, selector=selector, verify_ms=8000, tab=tab, capture_tab=capture_tab)
            self.routines.record("click", {"selector": selector, "tab": tab}, row)
            outcome = "navigation verified" if row.get("navigated") else "click verified"
            return (
                f"Clicked {selector!r} · {outcome} · existing-edge/extension · "
                f"{row.get('url') or self.current_url}"
            )
        if tab != "work":
            raise BrowserUnavailable("Named browser tabs require the connected Advertpreneur Browser Bridge extension")
        before = self.current_url
        result = str(self._rpc("click", selector, timeout=25))
        self.routines.record("click", {"selector": selector}, {"before_url": before, "url": self.current_url, "verified": True, "navigated": before != self.current_url})
        return result

    def _fill_direct(self, selector: str, value: str) -> str:
        page = self._ensure_direct()
        page.locator(selector).first.fill(str(value), timeout=15000)
        self._update_page_state(page)
        return f"Filled {selector!r} ({len(str(value))} chars)."

    def fill(self, selector: str, value: str, tab: str = "work") -> str:
        if self._extension_available(wait_seconds=0.5):
            row = self._extension_call("fill", timeout=20, selector=selector, value=str(value), tab=tab)
            self.routines.record("fill", {"selector": selector, "value": str(value), "tab": tab}, row)
            return f"Filled {selector!r} ({len(str(value))} chars) · existing-edge/extension"
        if tab != "work":
            raise BrowserUnavailable("Named browser tabs require the connected Advertpreneur Browser Bridge extension")
        result = str(self._rpc("fill", selector, value, timeout=25))
        self.routines.record("fill", {"selector": selector, "value": str(value)}, {"verified": True, "url": self.current_url})
        return result

    def _scroll_direct(self, amount: int | str = 650) -> str:
        page = self._ensure_direct()
        if isinstance(amount, str) and not str(amount).lstrip("-+").isdigit():
            page.locator(str(amount)).first.scroll_into_view_if_needed(timeout=15000)
        else:
            page.evaluate("dy => window.scrollBy({top: dy, behavior: 'instant'})", int(amount))
        page.wait_for_timeout(120)
        self._update_page_state(page)
        y = int(page.evaluate("window.scrollY"))
        return f"Scrolled · y={y} · {self.current_url}"

    def scroll(self, amount: int | str = 650, tab: str = "work") -> str:
        if self._extension_available(wait_seconds=0.5):
            row = self._extension_call("scroll", timeout=20, amount=amount, tab=tab)
            self.routines.record("scroll", {"amount": amount, "tab": tab}, row)
            return f"Scrolled · y={row.get('scroll_y', '?')} · existing-edge/extension · {row.get('url') or self.current_url}"
        if tab != "work":
            raise BrowserUnavailable("Named browser tabs require the connected Advertpreneur Browser Bridge extension")
        result = str(self._rpc("scroll", amount, timeout=25))
        self.routines.record("scroll", {"amount": amount}, {"verified": True, "url": self.current_url})
        return result

    def _wait_direct(self, milliseconds: int = 750) -> str:
        page = self._ensure_direct()
        ms = max(0, min(30000, int(milliseconds)))
        page.wait_for_timeout(ms)
        self._update_page_state(page)
        return f"Waited {ms}ms · {self.current_url}"

    def wait(self, milliseconds: int = 750, tab: str = "work") -> str:
        ms = max(0, min(30000, int(milliseconds)))
        if self._extension_available(wait_seconds=0.5):
            row = self._extension_call("wait", timeout=max(20, ms / 1000 + 5), milliseconds=ms, tab=tab)
            self.routines.record("wait", {"milliseconds": ms, "tab": tab}, row)
            return f"Waited {ms}ms · existing-edge/extension · {row.get('url') or self.current_url}"
        if tab != "work":
            raise BrowserUnavailable("Named browser tabs require the connected Advertpreneur Browser Bridge extension")
        result = str(self._rpc("wait", ms, timeout=max(25, ms / 1000 + 5)))
        self.routines.record("wait", {"milliseconds": ms}, {"verified": True, "url": self.current_url})
        return result

    def mark_download(self, tab: str = "work") -> int:
        """Capture the newest download id before a visible export click.

        Download observation is only available through the connected Browser Bridge;
        an isolated fallback browser must not be used for authenticated research.
        """
        if not self._extension_available(wait_seconds=0.8):
            raise BrowserUnavailable("Research downloads require the connected Advertpreneur Browser Bridge extension")
        row = self._extension_call("download_mark", timeout=12, tab=tab)
        return int(row.get("marker") or row.get("download_id") or 0)

    def wait_for_download(self, marker: int, timeout_seconds: int = 45, tab: str = "work") -> Path:
        if not self._extension_available(wait_seconds=0.8):
            raise BrowserUnavailable("Research downloads require the connected Advertpreneur Browser Bridge extension")
        timeout_ms = max(1_000, min(120_000, int(timeout_seconds) * 1000))
        row = self._extension_call("download_wait", timeout=(timeout_ms / 1000) + 12, marker=int(marker), timeout_ms=timeout_ms, tab=tab)
        filename = str(row.get("filename") or "")
        if not filename:
            raise BrowserUnavailable("Browser Bridge completed a download without a local filename")
        return Path(filename)

    def learn_start(self, name: str) -> str:
        tabs = self.get_slots()
        actual = self.routines.start(name, protected_tabs=tabs)
        # Clear any stale human events and arm the existing-browser recorder.
        try:
            self.bridge.browser_learn_events()
        except Exception:
            pass
        if self._extension_available(wait_seconds=0.8):
            try:
                row = self._extension_call("learn_start", timeout=10, name=actual, tabs=list(tabs))
            except Exception:
                pass
        return f"Browser Learn Mode ON · recording {actual!r} · AI/direct commands and human actions in the controlled tab are captured locally"

    def learn_stop(self) -> str:
        if self._extension_available(wait_seconds=0.5):
            try:
                self._extension_call("learn_stop", timeout=10)
            except Exception:
                pass
        # Human actions are buffered by the broker so they survive service-worker sleep/navigation.
        try:
            for event in self.bridge.browser_learn_events():
                action = str(event.get("action") or "").lower()
                args = event.get("args") if isinstance(event.get("args"), dict) else {}
                evidence = event.get("evidence") if isinstance(event.get("evidence"), dict) else {}
                tab = str(args.get("tab") or "").strip()
                if tab:
                    self.routines.protect_tab(tab, str(evidence.get("url") or ""), str(evidence.get("title") or ""))
                    self.record_slot(tab, str(evidence.get("url") or ""), str(evidence.get("title") or ""))
                self.routines.record(action, args, evidence)
        except Exception:
            pass
        row = self.routines.stop()
        self.last_learned_routine = str(row.get("name") or "")
        return f"Browser routine saved · {row.get('name')} · {len(row.get('steps') or [])} step(s) · replay uses 0 model tokens"

    def learn_cancel(self) -> str:
        name = self.routines.learning_name
        self.routines.cancel()
        return f"Browser Learn Mode canceled{(' · ' + name) if name else ''}"

    def routine_names(self) -> list[str]:
        return self.routines.names()

    @staticmethod
    def _routine_url(value: object) -> str:
        parsed = urlsplit(str(value or ""))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    def _verify_protected_tabs(self, protected_tabs: dict[str, Any]) -> None:
        current = self.get_slots()
        for slot, expected in protected_tabs.items():
            expected_url = self._routine_url(expected.get("url") if isinstance(expected, dict) else "")
            actual_url = self._routine_url((current.get(slot) or {}).get("url"))
            if not expected_url or actual_url != expected_url:
                raise BrowserRoutineError(f"protected tab mismatch: {slot}; re-teach this changed step")

    def run_routine(self, name: str, repeat: int = 1, keyword: str = "") -> str:
        row = self.routines.get(name)
        self._verify_protected_tabs(row.get("protected_tabs") if isinstance(row.get("protected_tabs"), dict) else {})
        count = max(1, min(100, int(repeat)))
        steps = list(row.get("steps") or [])
        if not steps:
            raise BrowserRoutineError(f"Browser routine {name!r} has no recorded steps")
        was_learning = self.routines.learning
        learned_name = self.routines.learning_name
        learned_steps = list(getattr(self.routines, "_learning_steps", []))
        self.routines.cancel()
        executed = 0
        try:
            for cycle in range(count):
                for step in steps:
                    action = str(step.get("action") or "")
                    args = dict(step.get("args") or {})
                    tab = str(args.get("tab") or "work")
                    if action == "navigate":
                        self.navigate(str(args.get("url") or ""), str(args.get("wait_until") or "domcontentloaded"), tab=tab)
                    elif action == "click":
                        result = self.click(str(args.get("selector") or ""), tab=tab)
                        expected = str((step.get("evidence") or {}).get("url") or "")
                        if expected and (step.get("evidence") or {}).get("navigated") and self.current_url != expected:
                            raise BrowserRoutineError(f"Routine click did not reach expected URL: {expected}; current: {self.current_url}")
                    elif action == "fill":
                        value = str(args.get("value") or "")
                        if value == "[TEACH_KEYWORD]":
                            if not keyword.strip():
                                raise BrowserRoutineError("Routine needs a keyword for its taught search step")
                            value = keyword
                        elif value == "[NOT_STORED_SENSITIVE_VALUE]":
                            raise BrowserRoutineError("Routine contains a sensitive fill value that was intentionally not stored")
                        self.fill(str(args.get("selector") or ""), value, tab=tab)
                    elif action == "scroll":
                        self.scroll(args.get("amount", 650), tab=tab)
                    elif action == "wait":
                        self.wait(int(args.get("milliseconds", 750)), tab=tab)
                    else:
                        raise BrowserRoutineError(f"Unsupported routine action: {action}")
                    executed += 1
        finally:
            if was_learning and learned_name:
                self.routines._learning_name = learned_name
                self.routines._learning_steps = learned_steps
        return f"Browser routine complete · {row.get('name')} · {count} run(s) · {executed} deterministic step(s) · model tokens 0"

    def _reverse_engineer_direct(self, selector: str = "body", name: str = "design-map", max_elements: int = 120) -> str:
        page = self._ensure_direct()
        max_elements = max(20, min(220, int(max_elements)))
        data: Dict[str, Any] = page.locator(selector).first.evaluate(
            """(root, maxElements) => {
              const clean = s => (s || '').replace(/\\s+/g,' ').trim().slice(0,260);
              const pick = s => ({
                display:s.display, position:s.position, width:s.width, height:s.height,
                margin:s.margin, padding:s.padding, gap:s.gap, gridTemplateColumns:s.gridTemplateColumns,
                justifyContent:s.justifyContent, alignItems:s.alignItems,
                fontFamily:s.fontFamily, fontSize:s.fontSize, fontWeight:s.fontWeight, lineHeight:s.lineHeight,
                letterSpacing:s.letterSpacing, color:s.color, backgroundColor:s.backgroundColor,
                backgroundImage:s.backgroundImage, border:s.border, borderRadius:s.borderRadius,
                boxShadow:s.boxShadow, textAlign:s.textAlign
              });
              const all = [root, ...root.querySelectorAll('*')];
              const elements=[];
              for (const el of all) {
                if(elements.length>=maxElements) break;
                const r=el.getBoundingClientRect();
                if(r.width<20 || r.height<10) continue;
                const tag=el.tagName.toLowerCase();
                const semantic = ['header','nav','main','section','article','aside','footer','h1','h2','h3','h4','p','a','button','img','form'].includes(tag);
                const large = r.width > innerWidth*0.35 && r.height > 60;
                if(!semantic && !large) continue;
                const s=getComputedStyle(el);
                elements.push({
                  tag, id:el.id||'', classes:(typeof el.className==='string'?el.className:'').split(/\\s+/).filter(Boolean).slice(0,6),
                  text:clean(el.innerText || el.getAttribute('alt') || el.getAttribute('aria-label')),
                  box:{x:Math.round(r.x+scrollX), y:Math.round(r.y+scrollY), width:Math.round(r.width), height:Math.round(r.height)},
                  style:pick(s), href:el.href||'', src:el.currentSrc||el.src||''
                });
              }
              const rootStyle=getComputedStyle(root);
              return {
                url:location.href, title:document.title,
                viewport:{width:innerWidth,height:innerHeight,devicePixelRatio:devicePixelRatio},
                document:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight},
                body:{background:getComputedStyle(document.body).backgroundColor,color:getComputedStyle(document.body).color,fontFamily:getComputedStyle(document.body).fontFamily},
                target:{selector: root===document.body?'body':'custom', box:(()=>{const r=root.getBoundingClientRect();return {x:Math.round(r.x+scrollX),y:Math.round(r.y+scrollY),width:Math.round(r.width),height:Math.round(r.height)}})(), style:pick(rootStyle)},
                elements
              };
            }""",
            max_elements,
        )
        self._update_page_state(page)
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name or "design-map").strip("-.") or "design-map"
        map_path = self.browser_dir / f"{safe}.json"
        shot_path = self.browser_dir / f"{safe}.png"
        page.locator(selector).first.screenshot(path=str(shot_path)) if selector and selector != "body" else page.screenshot(path=str(shot_path), full_page=True)
        data["screenshot"] = shot_path.relative_to(self.project).as_posix()
        data["captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        data["browser_provider"] = self.provider
        map_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        major = sorted(data.get("elements") or [], key=lambda e: e.get("box", {}).get("width", 0) * e.get("box", {}).get("height", 0), reverse=True)[:18]
        lines = [
            f"Design map saved · {map_path.relative_to(self.project).as_posix()}",
            f"Screenshot saved · {shot_path.relative_to(self.project).as_posix()}",
            f"Reference · {data.get('title')} · {data.get('url')}",
            f"Provider · {self.provider}",
            f"Viewport {data.get('viewport')} · document {data.get('document')} · cloud tokens 0",
            "Major measured elements:",
        ]
        for e in major:
            b=e.get('box') or {}; s=e.get('style') or {}
            label=(e.get('text') or '')[:100]
            lines.append(f"- {e.get('tag')} {b.get('width')}x{b.get('height')} @ {b.get('x')},{b.get('y')} · font {s.get('fontSize')}/{s.get('lineHeight')} {s.get('fontWeight')} · bg {s.get('backgroundColor')} · radius {s.get('borderRadius')} · {label!r}")
        return "\n".join(lines)

    def reverse_engineer(self, selector: str = "body", name: str = "design-map", max_elements: int = 120) -> str:
        if self._extension_available(wait_seconds=0.8):
            row = self._extension_call("reverse_engineer", timeout=30, selector=selector, max_elements=max_elements)
            safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "design-map")).strip("-.") or "design-map"
            map_path = self.browser_dir / f"{safe}.json"
            shot_path = self.browser_dir / f"{safe}.jpg"
            data = dict(row)
            data["captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            data["browser_provider"] = "existing-edge/extension"
            # Capture a visual reference using the same normal-browser tab/profile.
            try:
                shot = self._extension_call("screenshot", timeout=25, name=safe, selector=selector)
                data_url = str(shot.get("data_url") or "")
                if data_url.startswith("data:image/"):
                    _, encoded = data_url.split(",", 1)
                    shot_path.write_bytes(base64.b64decode(encoded))
                    data["screenshot"] = shot_path.relative_to(self.project).as_posix()
            except Exception:
                pass
            map_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            elements = list(data.get("elements") or [])[:28]
            lines = [
                f"Design map saved · {map_path.relative_to(self.project).as_posix()}",
                f"Reference · {data.get('title') or self.current_title} · {data.get('url') or self.current_url}",
                "Provider · existing-edge/extension",
                f"Measured elements · {len(data.get('elements') or [])} · cloud tokens 0",
                "FIDELITY RULE: use the exact reference text/assets/geometry/styles below; do not substitute generic marketing copy, stock art, fonts, decoration, or guessed shapes when evidence is available.",
            ]
            if data.get("screenshot"):
                lines.insert(1, f"Screenshot saved · {data['screenshot']}")
            for e in elements:
                r=e.get("rect") or e.get("box") or {}; st=e.get("style") or {}
                text=(e.get('text') or '')[:180]
                extra=[]
                if e.get('src'): extra.append(f"src={str(e.get('src'))[:240]}")
                if e.get('href'): extra.append(f"href={str(e.get('href'))[:180]}")
                if st.get('backgroundImage') and st.get('backgroundImage') != 'none': extra.append(f"bgImage={str(st.get('backgroundImage'))[:240]}")
                for pseudo_name in ('before','after'):
                    pseudo=e.get(pseudo_name) or {}
                    if pseudo:
                        extra.append(f"::{pseudo_name}={json.dumps(pseudo, ensure_ascii=False, separators=(',', ':'))[:420]}")
                lines.append(
                    f"- {e.get('tag')} {r.get('width') or r.get('w')}x{r.get('height') or r.get('h')} @ {r.get('x')},{r.get('y')} "
                    f"· font={st.get('fontFamily','')} {st.get('fontSize','')}/{st.get('lineHeight','')} w{st.get('fontWeight','')} "
                    f"· color={st.get('color','')} bg={st.get('backgroundColor','')} pad={st.get('padding','')} radius={st.get('borderRadius','')} "
                    f"· transform={st.get('transform','')} clip={st.get('clipPath','')} · text={text!r}"
                    + ((" · " + " · ".join(extra)) if extra else "")
                )
            return "\n".join(lines)
        return str(self._rpc("reverse_engineer", selector, name, max_elements, timeout=60))
