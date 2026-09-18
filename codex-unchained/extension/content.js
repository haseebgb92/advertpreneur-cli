
"use strict";

(() => {
  if (window.__ADP_UNCHAINED_CONTENT__) return;
  window.__ADP_UNCHAINED_CONTENT__ = true;

  const SENSITIVE = /password|passwd|secret|token|otp|mfa|pin|cvv|card|auth|passcode/i;
  const PARAMETER = /search|query|keyword/i;

  function normalize(value, max = 180) {
    return String(value || "").replace(/\s+/g, " ").trim().slice(0, max);
  }

  function cssEscape(value) {
    try { return CSS.escape(String(value)); }
    catch (_) { return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&"); }
  }

  function selectorFor(el) {
    if (!(el instanceof Element)) return "";
    if (el.id) return "#" + cssEscape(el.id);

    const testId = el.getAttribute("data-testid");
    if (testId) return '[data-testid="' + String(testId).replace(/"/g, '\\"') + '"]';

    const aria = el.getAttribute("aria-label");
    if (aria) {
      return el.tagName.toLowerCase() + '[aria-label="' + String(aria).replace(/"/g, '\\"') + '"]';
    }

    const name = el.getAttribute("name");
    if (name) {
      return el.tagName.toLowerCase() + '[name="' + String(name).replace(/"/g, '\\"') + '"]';
    }

    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 4) {
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((x) => x.tagName === node.tagName);
        if (same.length > 1) part += ":nth-of-type(" + (same.indexOf(node) + 1) + ")";
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(" > ");
  }

  function roleFor(el) {
    if (!(el instanceof Element)) return "";
    if (el.getAttribute("role")) return normalize(el.getAttribute("role"), 40);
    if (el instanceof HTMLButtonElement) return "button";
    if (el instanceof HTMLAnchorElement) return "link";
    if (el instanceof HTMLInputElement) return "input";
    return "";
  }

  function nameFor(el) {
    if (!(el instanceof Element)) return "";
    return normalize(
      el.getAttribute("aria-label") ||
      el.getAttribute("title") ||
      el.innerText ||
      el.textContent ||
      el.getAttribute("placeholder") ||
      "",
      120
    );
  }

  function isVisible(el) {
    if (!(el instanceof Element)) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 &&
      style.visibility !== "hidden" && style.display !== "none";
  }

  function allControls() {
    return Array.from(document.querySelectorAll(
      "button,a[href],input,textarea,select,[role=button],[role=link],[data-testid]"
    )).filter(isVisible);
  }

  function semanticTarget(el) {
    return {
      role: roleFor(el) || null,
      name: nameFor(el) || null,
      test_id: el.getAttribute("data-testid") || null,
      aria_label: el.getAttribute("aria-label") || null,
      css: selectorFor(el) || null,
      near: null
    };
  }

  function semanticSnapshot() {
    const elements = [];
    for (const el of allControls()) {
      const target = semanticTarget(el);
      elements.push({
        role: target.role,
        name: target.name,
        test_id: target.test_id,
        aria_label: target.aria_label,
        css: target.css,
        disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true")
      });
      if (elements.length >= 180) break;
    }

    return {
      tab_id: -1,
      url: location.href,
      title: document.title,
      elements
    };
  }

  function resolveTarget(target = {}) {
    if (target.test_id) {
      const selector = '[data-testid="' + String(target.test_id).replace(/"/g, '\\"') + '"]';
      const node = document.querySelector(selector);
      if (node) return node;
    }

    if (target.aria_label) {
      const selector = '[aria-label="' + String(target.aria_label).replace(/"/g, '\\"') + '"]';
      const node = document.querySelector(selector);
      if (node) return node;
    }

    if (target.css) {
      try {
        const node = document.querySelector(target.css);
        if (node) return node;
      } catch (_) {}
    }

    const desiredRole = normalize(target.role, 40).toLowerCase();
    const desiredName = normalize(target.name, 120).toLowerCase();
    return allControls().find((el) => {
      const role = roleFor(el).toLowerCase();
      const name = nameFor(el).toLowerCase();
      return (!desiredRole || role === desiredRole) && (!desiredName || name === desiredName);
    }) || null;
  }

  function sensitivityHint(el) {
    if (!(el instanceof Element)) return "";
    return [
      el.getAttribute("type"),
      el.getAttribute("name"),
      el.getAttribute("id"),
      el.getAttribute("autocomplete"),
      el.getAttribute("aria-label"),
      el.getAttribute("placeholder")
    ].filter(Boolean).join(" ");
  }

  async function perform(action) {
    const kind = action?.action;

    if (kind === "inspect") return semanticSnapshot();

    if (kind === "scroll") {
      window.scrollBy({ top: Number(action.amount || 0), behavior: "instant" });
      return { ok: true, scroll_y: Math.round(window.scrollY || 0) };
    }

    const target = resolveTarget(action?.target || {});
    if (!target) throw new Error("Semantic target not found");

    if (kind === "click") {
      target.scrollIntoView({ block: "center", inline: "center" });
      target.click();
      return { ok: true, url: location.href };
    }

    if (kind === "fill") {
      if (!(target instanceof HTMLInputElement ||
            target instanceof HTMLTextAreaElement ||
            target instanceof HTMLSelectElement)) {
        throw new Error("Semantic target is not fillable");
      }

      if (SENSITIVE.test(sensitivityHint(target))) {
        throw new Error("Sensitive controls are never filled by learned replay");
      }

      target.focus();
      target.value = String(action.value ?? "");
      target.dispatchEvent(new Event("input", { bubbles: true }));
      target.dispatchEvent(new Event("change", { bubbles: true }));
      return { ok: true, url: location.href };
    }

    throw new Error("Unsupported page action: " + String(kind || ""));
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "ADP_UNCHAINED_ACTION") return;
    Promise.resolve()
      .then(() => perform(message.action || {}))
      .then((result) => sendResponse({ ok: true, ...result }))
      .catch((error) => sendResponse({ ok: false, error: String(error.message || error) }));
    return true;
  });

  function emitLearn(event) {
    try {
      chrome.runtime.sendMessage(
        { type: "ADP_UNCHAINED_LEARN_EVENT", event },
        () => void chrome.runtime.lastError
      );
    } catch (_) {}
  }

  document.addEventListener("click", (event) => {
    if (!event.isTrusted) return;

    const path = typeof event.composedPath === "function" ? event.composedPath() : [];
    const source = path.find((node) => node instanceof Element) || event.target;
    const el = source instanceof Element
      ? (source.closest("button,a[href],[role=button],[role=link],input,select,textarea") || source)
      : null;

    if (!(el instanceof Element)) return;

    emitLearn({
      action: "click",
      selector_hint: selectorFor(el),
      target: semanticTarget(el),
      evidence: {
        url: location.href,
        title: document.title,
        verified: true
      }
    });
  }, true);

  document.addEventListener("change", (event) => {
    if (!event.isTrusted) return;
    const el = event.target;

    if (!(el instanceof HTMLInputElement ||
          el instanceof HTMLTextAreaElement ||
          el instanceof HTMLSelectElement)) {
      return;
    }

    const hint = selectorFor(el) + " " + sensitivityHint(el);
    if (SENSITIVE.test(hint) || !PARAMETER.test(hint)) return;

    emitLearn({
      action: "fill",
      selector_hint: hint,
      value: "[TEACH_KEYWORD]",
      target: semanticTarget(el),
      evidence: {
        url: location.href,
        title: document.title,
        verified: true
      }
    });
  }, true);
})();
