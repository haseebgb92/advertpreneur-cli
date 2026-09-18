
"use strict";

const BROKER = "http://127.0.0.1:8765";
const IDENTITY_KEY = "adpUnchainedIdentity";
const BOUND_TAB_KEY = "adpUnchainedBoundTab";
const ALARM = "adp-unchained-heartbeat";
let running = false;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function storageGet(key, fallback = null) {
  return new Promise((resolve) => {
    chrome.storage.local.get([key], (row) => resolve(row[key] ?? fallback));
  });
}

function storageSet(key, value) {
  return new Promise((resolve) => chrome.storage.local.set({ [key]: value }, resolve));
}

async function identity() {
  let value = await storageGet(IDENTITY_KEY);
  if (!value?.providerId || !value?.sessionKey) {
    const bytes = new Uint8Array(24);
    crypto.getRandomValues(bytes);
    value = {
      providerId: crypto.randomUUID(),
      sessionKey: Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")
    };
    await storageSet(IDENTITY_KEY, value);
  }
  return value;
}

async function broker(path, options = {}) {
  const id = await identity();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs || 20000);
  try {
    const response = await fetch(BROKER + path, {
      method: options.method || "GET",
      headers: {
        "Content-Type": "application/json",
        "X-ADP-Provider": id.providerId,
        "X-ADP-Session": id.sessionKey
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
      cache: "no-store",
      signal: controller.signal
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Broker HTTP ${response.status}`);
    return payload;
  } finally {
    clearTimeout(timer);
  }
}

async function register() {
  const id = await identity();
  const manifest = chrome.runtime.getManifest();
  return broker("/v2/browser/register", {
    method: "POST",
    timeoutMs: 3000,
    body: {
      protocol_version: 2,
      provider_id: id.providerId,
      session_key: id.sessionKey,
      label: "Existing Chrome/Edge profile",
      extension_version: manifest.version,
      capabilities: [
        "semantic_snapshot",
        "navigate",
        "click",
        "fill",
        "scroll",
        "screenshot",
        "download_wait",
        "teach_events"
      ]
    }
  });
}

async function currentTab() {
  const rows = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return rows[0] || null;
}

async function resolveTab(explicitId = null) {
  if (explicitId) {
    try { return await chrome.tabs.get(Number(explicitId)); } catch (_) {}
  }

  const bound = await storageGet(BOUND_TAB_KEY);
  if (bound?.tabId) {
    try { return await chrome.tabs.get(Number(bound.tabId)); } catch (_) {}
  }

  const tab = await currentTab();
  if (!tab?.id) throw new Error("No browser tab is available");
  return tab;
}

async function bindActive() {
  const tab = await currentTab();
  if (!tab?.id) throw new Error("No active browser tab is available");
  await storageSet(BOUND_TAB_KEY, { tabId: tab.id, windowId: tab.windowId });
  return { tab_id: tab.id, url: tab.url || "", title: tab.title || "" };
}

function messageTab(tabId, action) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, { type: "ADP_UNCHAINED_ACTION", action }, (response) => {
      const error = chrome.runtime.lastError;
      if (error) reject(new Error(error.message));
      else if (response?.ok === false) reject(new Error(response.error || "Page action failed"));
      else resolve(response || {});
    });
  });
}

async function waitForDownload(afterId, timeoutMs) {
  const deadline = Date.now() + Math.max(1000, Math.min(120000, Number(timeoutMs || 45000)));
  while (Date.now() < deadline) {
    const rows = await chrome.downloads.search({ orderBy: ["-id"], limit: 30 });
    const match = rows.find(
      (row) => Number(row.id || 0) > Number(afterId || 0) && row.state === "complete"
    );
    if (match) {
      return {
        id: match.id,
        filename: match.filename,
        mime: match.mime || "",
        url: match.url || ""
      };
    }
    await sleep(350);
  }
  throw new Error("Timed out waiting for a completed download");
}

async function execute(command) {
  const action = command?.action || {};
  if (action.action === "bind_active") return bindActive();

  const tab = await resolveTab(command?.tab_id);
  if (!tab?.id) throw new Error("Browser tab is unavailable");

  if (action.action === "navigate") {
    const updated = await chrome.tabs.update(tab.id, { url: String(action.url || "") });
    return { tab_id: updated.id, url: updated.url || "" };
  }

  if (action.action === "screenshot") {
    const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
    return { tab_id: tab.id, data_url: dataUrl };
  }

  if (action.action === "wait_for_download") {
    return waitForDownload(action.after_id, action.timeout_ms);
  }

  return messageTab(tab.id, action);
}

async function report(command, result, error = null) {
  await broker("/v2/browser/result", {
    method: "POST",
    body: {
      command_id: String(command?.command_id || ""),
      ok: !error,
      result: error ? {} : (result || {}),
      error: error ? String(error.message || error) : null
    }
  });
}

async function loop() {
  if (running) return;
  running = true;
  try {
    while (true) {
      try {
        await register();
        const next = await broker("/v2/browser/next", { timeoutMs: 30000 });
        if (!next?.command?.command_id) {
          await sleep(300);
          continue;
        }
        try {
          await report(next.command, await execute(next.command));
        } catch (error) {
          await report(next.command, null, error);
        }
      } catch (_) {
        await sleep(1500);
      }
    }
  } finally {
    running = false;
  }
}

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type !== "ADP_UNCHAINED_LEARN_EVENT") return;
  broker("/v2/browser/learn", {
    method: "POST",
    timeoutMs: 3000,
    body: {
      tab_id: sender?.tab?.id || null,
      url: sender?.tab?.url || "",
      title: sender?.tab?.title || "",
      event: message.event || {}
    }
  }).catch(() => {});
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) loop();
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(ALARM, { delayInMinutes: 0.02, periodInMinutes: 0.5 });
  loop();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(ALARM, { delayInMinutes: 0.02, periodInMinutes: 0.5 });
  loop();
});

chrome.alarms.create(ALARM, { delayInMinutes: 0.02, periodInMinutes: 0.5 });
loop();
