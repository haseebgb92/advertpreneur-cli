(() => {
  "use strict";
  const BRIDGE = "http://127.0.0.1:8765";
  const PAIR_KEY = "advertpreneurPairsV1";
  const $ = (id) => document.getElementById(id);
  let currentTab = null;
  let cid = "";
  let sessions = [];
  let pairs = {};

  function conversationId(url) {
    try {
      const u = new URL(url);
      const match = u.pathname.match(/\/c\/([^/?#]+)/);
      return match ? match[1] : "";
    } catch (_) { return ""; }
  }

  function notice(text, error = false) {
    const node = $("notice");
    node.textContent = text || "";
    node.classList.toggle("hidden", !text);
    node.classList.toggle("error", error);
  }

  async function getStorage() {
    return new Promise((resolve) => chrome.storage.local.get([PAIR_KEY], (data) => resolve(data[PAIR_KEY] || {})));
  }

  async function setStorage(value) {
    return new Promise((resolve) => chrome.storage.local.set({ [PAIR_KEY]: value }, resolve));
  }

  async function api(path, options = {}) {
    return await new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: "ADP_BRIDGE_API", path, options }, (row) => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (!row?.ok) return reject(new Error(row?.error || "Browser Bridge request failed"));
        resolve(row.data || {});
      });
    });
  }

  async function contentBridgeReady() {
    if (!currentTab?.id) return false;
    return await new Promise((resolve) => {
      chrome.tabs.sendMessage(currentTab.id, { type: "ADP_BRIDGE_STATUS" }, (response) => {
        if (chrome.runtime.lastError) return resolve(false);
        resolve(Boolean(response));
      });
    });
  }

  function render() {
    const pair = cid ? pairs[cid] : null;
    $("chat").textContent = cid ? cid.slice(0, 10) + "…" : "Open a saved ChatGPT conversation";
    $("linkState").textContent = pair ? "Linked" : "Not linked";
    $("pairPanel").classList.toggle("hidden", !cid || Boolean(pair));
    $("linkedPanel").classList.toggle("hidden", !pair);
    if (pair) {
      const session = sessions.find((s) => s.session_id === pair.sessionId);
      $("linkedTitle").textContent = session?.session_name || pair.sessionName || "Advertpreneur CLI session";
      $("linkedMeta").textContent = session ? `${session.project} · ${session.model} · ${session.status}` : "Linked session is not currently running.";
    }
  }

  async function refresh() {
    notice("");
    pairs = await getStorage();
    currentTab = await new Promise((resolve) => chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => resolve(tabs[0] || null)));
    cid = currentTab && String(currentTab.url || "").startsWith("https://chatgpt.com/") ? conversationId(currentTab.url) : "";
    let brokerOk = false;
    try {
      const health = await api("/health");
      brokerOk = health.service === "advertpreneur-browser-bridge";
      $("broker").textContent = brokerOk ? "Connected" : "Unexpected service";
      const data = await api("/v1/public/sessions");
      sessions = Array.isArray(data.sessions) ? data.sessions : [];
      const browser = await api("/v1/cli/browser-status");
      const progress = browser.progress || {};
      $("controlled").textContent = browser.available ? (progress.url || "Ready") : "Not connected";
      $("operation").textContent = progress.stage || "Idle";
    } catch (error) {
      sessions = [];
      $("broker").textContent = "Offline";
      $("controlled").textContent = "Unavailable";
      $("operation").textContent = "Unavailable";
      notice("Start Advertpreneur CLI and enable Browser Bridge with /bridge on.", true);
    }
    const select = $("session");
    select.innerHTML = "";
    for (const session of sessions) {
      const option = document.createElement("option");
      option.value = session.session_id;
      option.textContent = `${session.session_name} — ${session.project}`;
      select.appendChild(option);
    }
    if (!sessions.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No bridge-enabled CLI sessions";
      select.appendChild(option);
    }
    if (!cid && currentTab?.url?.startsWith("https://chatgpt.com/")) {
      notice("This chat has no conversation ID yet. Send/save one message first, then open the extension again.");
    } else if (!currentTab?.url?.startsWith("https://chatgpt.com/")) {
      notice("Open the ChatGPT conversation you want to pair.");
    } else if (cid && brokerOk && !(await contentBridgeReady())) {
      notice("Refresh this ChatGPT tab once after installing/updating the extension, then reopen Browser Bridge.");
    }
    render();
  }

  $("pairButton").addEventListener("click", async () => {
    const sessionId = $("session").value;
    const code = $("pairCode").value.trim();
    if (!cid || !sessionId || !/^\d{6}$/.test(code)) {
      notice("Choose a running CLI session and enter its 6-digit /bridge pair code.", true);
      return;
    }
    try {
      const data = await api("/v1/public/pair", {
        method: "POST",
        body: {
          session_id: sessionId,
          pair_code: code,
          conversation_id: cid,
          conversation_url: currentTab.url,
          replace: false
        }
      });
      const session = sessions.find((s) => s.session_id === sessionId);
      pairs[cid] = {
        sessionId,
        sessionName: session?.session_name || "Advertpreneur CLI",
        pairToken: data.pair_token,
        conversationUrl: currentTab.url
      };
      await setStorage(pairs);
      $("pairCode").value = "";
      notice("Linked. Completed CLI results will be sent to this chat; its next response returns to the same CLI session.");
      try { chrome.tabs.sendMessage(currentTab.id, { type: "ADP_BRIDGE_REFRESH" }); } catch (_) {}
      await refresh();
    } catch (error) {
      notice(String(error.message || error), true);
    }
  });

  $("unlinkButton").addEventListener("click", async () => {
    const pair = pairs[cid];
    if (!pair) return;
    try {
      await api("/v1/public/unpair", {
        method: "POST",
        body: {
          session_id: pair.sessionId,
          pair_token: pair.pairToken,
          conversation_id: cid
        }
      });
    } catch (_) {
      // Remove local pairing even if the CLI/broker is currently offline.
    }
    delete pairs[cid];
    await setStorage(pairs);
    await refresh();
  });

  refresh().catch((error) => notice(String(error.message || error), true));
})();
