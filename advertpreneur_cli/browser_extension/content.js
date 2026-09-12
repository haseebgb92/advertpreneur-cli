(() => {
  "use strict";

  const BRIDGE = "http://127.0.0.1:8765";
  const POLL_MS = 1200;
  const STABLE_MS = 2200;
  const PAIR_KEY = "advertpreneurPairsV1";
  const PENDING_KEY = "advertpreneurPendingV1";
  let stopped = false;
  let lastConversationId = "";
  let currentWork = null;

  function conversationId() {
    const match = location.pathname.match(/\/c\/([^/?#]+)/);
    return match ? match[1] : "";
  }

  async function storageGet(key) {
    return new Promise((resolve) => chrome.storage.local.get([key], (data) => resolve(data[key] || {})));
  }

  async function storageSet(key, value) {
    return new Promise((resolve) => chrome.storage.local.set({ [key]: value }, resolve));
  }

  async function getPair(cid) {
    const pairs = await storageGet(PAIR_KEY);
    return pairs[cid] || null;
  }

  async function getPending(cid) {
    const rows = await storageGet(PENDING_KEY);
    return rows[cid] || null;
  }

  async function setPending(cid, value) {
    const rows = await storageGet(PENDING_KEY);
    if (value) rows[cid] = value;
    else delete rows[cid];
    await storageSet(PENDING_KEY, rows);
  }

  async function api(path, options = {}) {
    const response = await new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: "ADP_BRIDGE_API", path, options }, (row) => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (!row?.ok) return reject(new Error(row?.error || "Browser Bridge request failed"));
        resolve(row.data || {});
      });
    });
    return response;
  }

  function messageNodes() {
    return Array.from(document.querySelectorAll('[data-message-author-role="user"], [data-message-author-role="assistant"]'));
  }

  function nodeText(node) {
    return node ? (node.innerText || node.textContent || "").trim() : "";
  }

  function marker(eventId) {
    return `ADP Bridge Event: ${eventId}`;
  }

  function userNodeForEvent(eventId) {
    const needle = marker(eventId);
    return messageNodes().find((node) => node.getAttribute("data-message-author-role") === "user" && nodeText(node).includes(needle)) || null;
  }

  function assistantNodeAfterEvent(eventId) {
    const nodes = messageNodes();
    const needle = marker(eventId);
    const anchor = nodes.findIndex((node) => node.getAttribute("data-message-author-role") === "user" && nodeText(node).includes(needle));
    if (anchor < 0) return null;
    for (let i = anchor + 1; i < nodes.length; i += 1) {
      const role = nodes[i].getAttribute("data-message-author-role");
      if (role === "user") return null; // another user turn means correlation is no longer safe
      if (role === "assistant") return nodes[i];
    }
    return null;
  }

  function isGenerating() {
    const candidates = Array.from(document.querySelectorAll("button"));
    return candidates.some((button) => {
      const testid = (button.getAttribute("data-testid") || "").toLowerCase();
      const aria = (button.getAttribute("aria-label") || "").toLowerCase();
      const text = (button.innerText || "").trim().toLowerCase();
      return testid.includes("stop") || aria.includes("stop generating") || text === "stop";
    });
  }

  function composer() {
    return document.querySelector("#prompt-textarea") ||
      document.querySelector('textarea[data-testid="prompt-textarea"]') ||
      document.querySelector('[contenteditable="true"][data-testid*="prompt"]') ||
      document.querySelector('div[contenteditable="true"]');
  }

  function sendButton() {
    return document.querySelector('button[data-testid="send-button"]') ||
      Array.from(document.querySelectorAll("button")).find((button) => {
        const aria = (button.getAttribute("aria-label") || "").toLowerCase();
        return aria === "send prompt" || aria === "send message" || aria.startsWith("send");
      });
  }

  function setComposerText(node, text) {
    node.focus();
    if (node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement) {
      const proto = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      if (setter) setter.call(node, text); else node.value = text;
      node.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
      node.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    const selection = window.getSelection(); const range = document.createRange();
    range.selectNodeContents(node); selection.removeAllRanges(); selection.addRange(range);
    let inserted = false;
    try { inserted = document.execCommand("insertText", false, text); } catch (_) { inserted = false; }
    if (!inserted) {
      node.textContent = text;
      node.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    }
  }

  function formatResult(payload, eventId) {
    const usage = payload.usage || {};
    const changed = Array.isArray(payload.changed_files) ? payload.changed_files : [];
    const lines = [
      "[ADVERTPRENEUR_BROWSER_BRIDGE_RESULT]",
      marker(eventId),
      `Session: ${payload.session_name || payload.session_id || "Advertpreneur CLI"}`,
      `Project: ${payload.project || ""}`,
      `Status: ${payload.status || "completed"}`,
      `Model: ${payload.provider || ""}/${payload.model || ""}`,
      "", "Task:", String(payload.task || "").trim(), "", "Completed result:", String(payload.result || "").trim()
    ];
    if (changed.length) lines.push("", "Changed files:", ...changed.map((x) => `- ${x}`));
    if (payload.checkpoint) lines.push("", `Checkpoint: ${payload.checkpoint}`);
    if (usage.requests !== undefined) {
      const extras = [];
      if (usage.reasoning_effort) extras.push(`reasoning ${usage.reasoning_effort}`);
      if (usage.tool_calls !== undefined) extras.push(`${usage.tool_calls || 0} tool call(s)`);
      if (usage.thinking_tokens) extras.push(`${usage.thinking_tokens} thinking`);
      if (usage.cache_read_tokens) extras.push(`${usage.cache_read_tokens} cached`);
      lines.push("", `CLI usage: ${usage.requests || 0} request(s) · ${usage.input_tokens || 0} in · ${usage.output_tokens || 0} out${extras.length ? " · " + extras.join(" · ") : ""} · ${usage.metered || "$0.0000"}`);
      if (usage.quota_before || usage.quota_after) lines.push(`Provider quota: ${usage.quota_before || "unavailable"} → ${usage.quota_after || "unavailable"}`);
      if (usage.quota_consumed && Object.keys(usage.quota_consumed).length) {
        lines.push(`Quota consumed by task: ${Object.entries(usage.quota_consumed).map(([k,v]) => `${k === "weekly" ? "wk" : k} ${v}%`).join(" · ")}`);
      }
    }
    lines.push("[/ADVERTPRENEUR_BROWSER_BRIDGE_RESULT]", "",
      "Browser Bridge protocol: review this completed CLI result and reply with the next concrete instruction for this same CLI session. Do not repeat the result back. If no further local work is needed, reply exactly ADP_BRIDGE_DONE.");
    return lines.join("\n");
  }

  async function waitForSendButton(timeoutMs = 6000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const button = sendButton();
      if (button && !button.disabled && button.getAttribute("aria-disabled") !== "true") return button;
      await new Promise((r) => setTimeout(r, 120));
    }
    return null;
  }

  async function confirmSubmission(pair, pending, timeoutMs = 15000) {
    const deadline = Date.now() + timeoutMs;
    while (!stopped && Date.now() < deadline) {
      if (conversationId() !== pair.conversationId) return false;
      if (userNodeForEvent(pending.eventId)) {
        await api("/v1/public/submitted", { method: "POST", body: {
          session_id: pair.sessionId, pair_token: pair.pairToken,
          conversation_id: pair.conversationId, event_id: pending.eventId
        }});
        pending.stage = "submitted"; pending.submittedAt = Date.now();
        await setPending(pair.conversationId, pending);
        return true;
      }
      await new Promise((r) => setTimeout(r, 180));
    }
    return false;
  }

  async function submitResult(pair, event) {
    // If the browser submitted this exact event before a broker POST/tab refresh,
    // recover it from the DOM instead of sending a duplicate user message.
    if (userNodeForEvent(event.event_id)) {
      const pending = { sessionId: pair.sessionId, pairToken: pair.pairToken, eventId: event.event_id, stage: "submitted", submittedAt: Date.now() };
      await setPending(pair.conversationId, pending);
      await api("/v1/public/submitted", { method: "POST", body: {
        session_id: pair.sessionId, pair_token: pair.pairToken, conversation_id: pair.conversationId, event_id: event.event_id
      }});
      return pending;
    }
    const node = composer(); if (!node) throw new Error("ChatGPT composer was not found");
    setComposerText(node, formatResult(event.payload || {}, event.event_id));
    const button = await waitForSendButton(); if (!button) throw new Error("ChatGPT send button did not become available");
    const pending = { sessionId: pair.sessionId, pairToken: pair.pairToken, eventId: event.event_id, stage: "sending", clickedAt: Date.now() };
    await setPending(pair.conversationId, pending);
    button.click();
    const committed = await confirmSubmission(pair, pending);
    if (!committed) {
      // A click is not delivery. Clear only the local sending marker so the queued
      // broker event can retry. If the DOM later contains the marker, the recovery
      // path above will recognize it and avoid duplicate submission.
      await setPending(pair.conversationId, null);
      throw new Error("ChatGPT did not confirm the bridge user turn after send click");
    }
    return pending;
  }

  async function waitForAssistantReply(pair, pending) {
    if (pending.stage === "sending") {
      const ok = await confirmSubmission(pair, pending);
      if (!ok) { await setPending(pair.conversationId, null); return; }
    }
    let stableText = ""; let stableSince = 0;
    const deadline = Date.now() + 30 * 60 * 1000;
    while (!stopped && Date.now() < deadline) {
      if (conversationId() !== pair.conversationId) return;
      const assistant = assistantNodeAfterEvent(pending.eventId);
      const text = nodeText(assistant);
      if (assistant && text) {
        if (text !== stableText) { stableText = text; stableSince = Date.now(); }
        else if (!isGenerating() && Date.now() - stableSince >= STABLE_MS) {
          await api("/v1/public/reply", { method: "POST", body: {
            session_id: pair.sessionId, pair_token: pair.pairToken, conversation_id: pair.conversationId,
            event_id: pending.eventId, text: stableText
          }});
          await setPending(pair.conversationId, null);
          return;
        }
      }
      await new Promise((r) => setTimeout(r, 450));
    }
  }

  async function bridgeLoop() {
    while (!stopped) {
      try {
        const cid = conversationId();
        if (!cid) { await new Promise((r) => setTimeout(r, POLL_MS)); continue; }
        if (cid !== lastConversationId) { lastConversationId = cid; currentWork = null; }
        const pair = await getPair(cid);
        if (!pair) { await new Promise((r) => setTimeout(r, POLL_MS)); continue; }
        pair.conversationId = cid;
        const pending = await getPending(cid);
        if (pending) {
          if (!currentWork) currentWork = waitForAssistantReply(pair, pending).finally(() => { currentWork = null; });
          await new Promise((r) => setTimeout(r, POLL_MS)); continue;
        }
        if (currentWork) { await new Promise((r) => setTimeout(r, POLL_MS)); continue; }
        const query = new URLSearchParams({ session_id: pair.sessionId, pair_token: pair.pairToken, conversation_id: cid });
        const data = await api(`/v1/public/outbound?${query.toString()}`); const event = data.event;
        if (event && event.state === "queued") {
          currentWork = (async () => {
            const row = await submitResult(pair, event); if (row) await waitForAssistantReply(pair, row);
          })().catch((error) => console.warn("Advertpreneur Bridge relay failed", error)).finally(() => { currentWork = null; });
        } else if (event && event.state === "submitted") {
          // Restore correlation after a tab/service-worker restart even if Chrome
          // storage was cleared but the exact user turn remains in the conversation.
          const row = { sessionId: pair.sessionId, pairToken: pair.pairToken, eventId: event.event_id, stage: "submitted", submittedAt: Date.now() };
          if (userNodeForEvent(event.event_id)) {
            await setPending(cid, row);
            currentWork = waitForAssistantReply(pair, row).finally(() => { currentWork = null; });
          }
        }
      } catch (_) {
        // Broker offline, extension unpaired, or ChatGPT DOM temporarily changing.
        // Broker state remains retryable and event IDs prevent duplicate execution.
      }
      await new Promise((r) => setTimeout(r, POLL_MS));
    }
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "ADP_BRIDGE_STATUS") {
      const cid = conversationId();
      getPair(cid).then((pair) => sendResponse({ conversationId: cid, paired: Boolean(pair), pair })).catch(() => sendResponse({ conversationId: cid, paired: false }));
      return true;
    }
    if (message?.type === "ADP_BRIDGE_REFRESH") {
      sendResponse({ ok: true });
      return false;
    }
    return false;
  });

  bridgeLoop();
})();
