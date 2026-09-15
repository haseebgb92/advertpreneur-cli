"use strict";

const BRIDGE = "http://127.0.0.1:8765";
const PROVIDER_KEY = "adpBrowserProvider";
const TAB_KEY = "adpBrowserControlledTab";
const TAB_SLOTS_KEY = "adpBrowserControlledTabs";
const LEARN_KEY = "adpBrowserLearnActive";
let browserLoopRunning = false;

const HEARTBEAT_ALARM = "adp-browser-provider-heartbeat";

function scheduleProviderHeartbeat() {
  try {
    chrome.alarms.create(HEARTBEAT_ALARM, { delayInMinutes: 0.05, periodInMinutes: 0.5 });
  } catch (_) {}
}

function storageGet(key, fallback = null) {
  return new Promise((resolve) => chrome.storage.local.get([key], (row) => resolve(row[key] ?? fallback)));
}
function storageSet(key, value) {
  return new Promise((resolve) => chrome.storage.local.set({ [key]: value }, resolve));
}
function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

function searchDownloads(query = {}) {
  return new Promise((resolve, reject) => chrome.downloads.search(query, (items) => {
    const error = chrome.runtime.lastError;
    if (error) reject(new Error(error.message)); else resolve(items || []);
  }));
}

async function newestDownloadId() {
  const items = await searchDownloads({ orderBy: ["-id"], limit: 1 });
  return Number(items[0]?.id || 0);
}

async function waitForCompletedDownload(marker, timeoutMs) {
  const deadline = Date.now() + Math.max(1000, Math.min(120000, Number(timeoutMs || 45000)));
  while (Date.now() < deadline) {
    const items = await searchDownloads({ orderBy: ["-id"], limit: 30 });
    const item = items.find((row) => Number(row.id || 0) > Number(marker || 0) && row.state === "complete" && row.filename);
    if (item) return item;
    await sleep(400);
  }
  throw new Error("Timed out waiting for a completed browser download");
}

async function bridgeFetch(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Number(options.timeoutMs || 25000));
  try {
    const response = await fetch(BRIDGE + path, {
      method: options.method || "GET",
      headers: { "Content-Type": "application/json" },
      body: options.body ? JSON.stringify(options.body) : undefined,
      cache: "no-store",
      signal: controller.signal
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Bridge HTTP ${response.status}`);
    return data || {};
  } finally {
    clearTimeout(timer);
  }
}

function randomToken() {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function providerIdentity() {
  let row = await storageGet(PROVIDER_KEY, null);
  if (!row?.providerId || !row?.token) {
    row = { providerId: crypto.randomUUID(), token: randomToken() };
    await storageSet(PROVIDER_KEY, row);
  }
  return row;
}

async function registerBrowserProvider() {
  const id = await providerIdentity();
  const manifest = chrome.runtime.getManifest();
  const data = await bridgeFetch("/v1/browser/register", {
    method: "POST",
    body: {
      provider_id: id.providerId,
      token: id.token,
      label: "Existing Edge/Chrome profile",
      version: manifest.version,
      user_agent: navigator.userAgent
    },
    timeoutMs: 3000
  });
  return { providerId: data.provider_id || id.providerId, token: data.token || id.token };
}

async function reportProgress(stage, detail = "", tab = null) {
  try {
    const id = await providerIdentity();
    await bridgeFetch("/v1/browser/progress", { method: "POST", body: {
      provider_id: id.providerId, token: id.token, stage, detail,
      url: tab?.url || "", title: tab?.title || ""
    }, timeoutMs: 3000 });
  } catch (_) {}
}

function sendDebugger(tabId, method, params = {}) {
  return new Promise((resolve, reject) => chrome.debugger.sendCommand({tabId}, method, params, (row) => {
    const error = chrome.runtime.lastError; if (error) reject(new Error(error.message)); else resolve(row || {});
  }));
}
function attachDebugger(tabId) {
  return new Promise((resolve, reject) => chrome.debugger.attach({tabId}, "1.3", () => {
    const error = chrome.runtime.lastError; if (error) reject(new Error(error.message)); else resolve();
  }));
}
function detachDebugger(tabId) { return new Promise((resolve) => chrome.debugger.detach({tabId}, () => resolve())); }

async function wordpressState(tabId) {
  return await executeInTab(tabId, () => {
    const password = Boolean(document.querySelector('input[type="password"]'));
    const dashboard = Boolean(document.body?.classList.contains("wp-admin") || document.querySelector("#wpadminbar, #dashboard-widgets"));
    const notices = Array.from(document.querySelectorAll(".notice, .updated, .error")).map(x => String(x.innerText || x.textContent || "").replace(/\s+/g," ").trim()).filter(Boolean).slice(0,6);
    const rows = Array.from(document.querySelectorAll(".wp-list-table tbody tr")).slice(0,50).map(x => String(x.innerText || "").replace(/\s+/g," ").trim()).filter(Boolean);
    return {login_needed: location.pathname.includes("wp-login.php") || password, authenticated: dashboard && !password, url: location.href, title: document.title, notices, rows};
  });
}

function slotName(value) {
  const clean = String(value || "work").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").slice(0, 32);
  return clean || "work";
}

async function controlledTabs() {
  const slots = await storageGet(TAB_SLOTS_KEY, {});
  if (slots && typeof slots === "object") return slots;
  const legacy = await storageGet(TAB_KEY, null);
  return legacy?.tabId ? { work: legacy } : {};
}

async function saveControlledTab(slot, tab) {
  const slots = await controlledTabs();
  slots[slotName(slot)] = { tabId: tab.id, windowId: tab.windowId };
  await storageSet(TAB_SLOTS_KEY, slots);
  await storageSet(TAB_KEY, slots.work || slots[slotName(slot)] || null);
}

async function existingControlledTab(slot = "work") {
  const slots = await controlledTabs();
  const saved = slots[slotName(slot)];
  if (!saved?.tabId) return null;
  try {
    return await chrome.tabs.get(Number(saved.tabId));
  } catch (_) {
    delete slots[slotName(slot)];
    await storageSet(TAB_SLOTS_KEY, slots);
    if (slotName(slot) === "work") await storageSet(TAB_KEY, null);
    return null;
  }
}

async function ensureControlledTab(url = "about:blank", slot = "work") {
  let tab = await existingControlledTab(slot);
  if (tab) return tab;
  tab = await chrome.tabs.create({ url, active: true });
  await saveControlledTab(slot, tab);
  return tab;
}

async function captureSpawnedTab(slot, existingIds, windowId, timeoutMs = 8000) {
  const deadline = Date.now() + Math.max(1000, Math.min(15000, Number(timeoutMs || 8000)));
  while (Date.now() < deadline) {
    const tabs = await chrome.tabs.query({ windowId });
    const tab = tabs.find((row) => row.id && !existingIds.has(row.id));
    if (tab) { await saveControlledTab(slot, tab); return tab; }
    await sleep(150);
  }
  return null;
}

function waitTab(tabId, timeoutMs = 30000) {
  return new Promise((resolve) => {
    let done = false;
    const finish = async () => {
      if (done) return;
      done = true;
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(timer);
      try { resolve(await chrome.tabs.get(tabId)); } catch (_) { resolve(null); }
    };
    const listener = (id, info) => { if (id === tabId && info.status === "complete") finish(); };
    chrome.tabs.onUpdated.addListener(listener);
    const timer = setTimeout(finish, timeoutMs);
    chrome.tabs.get(tabId).then((tab) => { if (tab?.status === "complete") finish(); }).catch(() => {});
  });
}

async function activateTab(tab) {
  if (!tab) return;
  try { await chrome.tabs.update(tab.id, { active: true }); } catch (_) {}
  try { await chrome.windows.update(tab.windowId, { focused: true }); } catch (_) {}
}

async function executeInTab(tabId, func, args = []) {
  const rows = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return rows?.[0]?.result;
}

function controlBarFunction(message = "Advertpreneur is controlling this tab", state = "active") {
  const ID = "adp-browser-control-bar";
  let bar = document.getElementById(ID);
  if (!bar) {
    bar = document.createElement("div");
    bar.id = ID;
    bar.dataset.adpControlBar = "1";
    Object.assign(bar.style, {
      position: "fixed", top: "10px", left: "50%", transform: "translateX(-50%)",
      zIndex: "2147483647", pointerEvents: "none", display: "flex", alignItems: "center",
      gap: "8px", padding: "8px 13px", borderRadius: "999px",
      background: "rgba(22, 24, 29, .94)", color: "#fff", border: "1px solid rgba(255,255,255,.16)",
      boxShadow: "0 6px 24px rgba(0,0,0,.28)", font: "600 12px/1.2 system-ui,-apple-system,Segoe UI,sans-serif",
      letterSpacing: ".01em", backdropFilter: "blur(8px)"
    });
    const dot = document.createElement("span");
    dot.dataset.adpDot = "1";
    Object.assign(dot.style, { width: "8px", height: "8px", borderRadius: "50%", background: "#ff7a18", boxShadow: "0 0 0 3px rgba(255,122,24,.18)" });
    const text = document.createElement("span"); text.dataset.adpText = "1";
    bar.append(dot, text); document.documentElement.appendChild(bar);
  }
  bar.style.display = "flex";
  const text = bar.querySelector('[data-adp-text="1"]');
  if (text) text.textContent = message;
  const dot = bar.querySelector('[data-adp-dot="1"]');
  if (dot) dot.style.background = state === "working" ? "#ff7a18" : "#4ade80";
  return true;
}

async function setControlBar(tabId, message, state = "active") {
  try { await executeInTab(tabId, controlBarFunction, [message, state]); } catch (_) {}
}

function snapshotFunction(selector, maxElements) {
  const root = document.querySelector(selector || "body");
  if (!root) return { error: `Selector not found: ${selector}` };
  const cleanText = (value, cap = 360) => String(value || "").replace(/\s+/g, " ").trim().slice(0, cap);
  const cleanBg = (value) => cleanText(value, 600);
  const pseudoOf = (el, which) => {
    const cs = getComputedStyle(el, which);
    const content = String(cs.content || "");
    const bg = cleanBg(cs.backgroundImage);
    if ((!content || content === "none" || content === '""') && (!bg || bg === "none")) return null;
    return {
      pseudo: which, content: cleanText(content, 240), position: cs.position,
      width: cs.width, height: cs.height, top: cs.top, right: cs.right, bottom: cs.bottom, left: cs.left,
      backgroundColor: cs.backgroundColor, backgroundImage: bg, borderRadius: cs.borderRadius,
      transform: cleanText(cs.transform, 220), clipPath: cleanText(cs.clipPath, 300), opacity: cs.opacity,
      zIndex: cs.zIndex, filter: cleanText(cs.filter, 220)
    };
  };
  const styleOf = (el) => {
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const before = pseudoOf(el, "::before"); const after = pseudoOf(el, "::after");
    return {
      tag: el.tagName.toLowerCase(), id: el.id || "", classes: Array.from(el.classList || []).slice(0, 10),
      text: cleanText(el.innerText || el.textContent || el.getAttribute("aria-label") || el.getAttribute("alt") || ""),
      rect: { x: Math.round(rect.x), y: Math.round(rect.y), pageX: Math.round(rect.x + scrollX), pageY: Math.round(rect.y + scrollY), width: Math.round(rect.width), height: Math.round(rect.height) },
      style: {
        display: cs.display, position: cs.position, boxSizing: cs.boxSizing,
        width: cs.width, height: cs.height, maxWidth: cs.maxWidth, minHeight: cs.minHeight,
        margin: cs.margin, padding: cs.padding, gap: cs.gap,
        color: cs.color, backgroundColor: cs.backgroundColor, backgroundImage: cleanBg(cs.backgroundImage),
        backgroundSize: cs.backgroundSize, backgroundPosition: cs.backgroundPosition,
        fontFamily: cleanText(cs.fontFamily, 220), fontSize: cs.fontSize, fontWeight: cs.fontWeight,
        lineHeight: cs.lineHeight, letterSpacing: cs.letterSpacing, textAlign: cs.textAlign, textTransform: cs.textTransform,
        border: cs.border, borderRadius: cs.borderRadius, boxShadow: cleanText(cs.boxShadow, 300),
        overflow: cs.overflow, flexDirection: cs.flexDirection, alignItems: cs.alignItems,
        justifyContent: cs.justifyContent, gridTemplateColumns: cleanText(cs.gridTemplateColumns, 280),
        transform: cleanText(cs.transform, 260), opacity: cs.opacity, zIndex: cs.zIndex,
        objectFit: cs.objectFit, clipPath: cleanText(cs.clipPath, 300), filter: cleanText(cs.filter, 260)
      },
      href: el instanceof HTMLAnchorElement ? el.href : "",
      src: (el instanceof HTMLImageElement || el instanceof HTMLSourceElement) ? (el.currentSrc || el.src || el.srcset || "") : "",
      alt: el.getAttribute?.("alt") || "", before, after
    };
  };
  const candidates = [root, ...root.querySelectorAll("section,header,main,nav,article,aside,footer,div,h1,h2,h3,h4,p,a,button,img,picture,video,svg,ul,ol,li,form,input")];
  const scored = [];
  for (const el of candidates) {
    if (el?.dataset?.adpControlBar === "1" || el?.closest?.('[data-adp-control-bar="1"]')) continue;
    const rect = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    if (rect.width < 2 || rect.height < 2 || cs.display === "none" || cs.visibility === "hidden") continue;
    const tag = el.tagName.toLowerCase();
    let score = 0;
    if (["h1","h2","h3","button","a","img","picture","video","svg"].includes(tag)) score += 80;
    if (["section","header","main","nav","p"].includes(tag)) score += 45;
    if (rect.width > innerWidth * .3) score += 20;
    if (rect.height > 80) score += 15;
    if (cs.backgroundImage && cs.backgroundImage !== "none") score += 30;
    if (pseudoOf(el,"::before") || pseudoOf(el,"::after")) score += 35;
    const text = cleanText(el.innerText || el.textContent || ""); if (text) score += Math.min(20, text.length / 8);
    scored.push({ score, el });
  }
  scored.sort((a,b) => b.score - a.score);
  const visible = scored.slice(0, Math.max(1, Number(maxElements || 100))).map(x => styleOf(x.el));
  return {
    url: location.href, title: document.title,
    viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio, scrollX, scrollY },
    document: { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight },
    selector: selector || "body", root: styleOf(root), elements: visible
  };
}

async function browserCommand(command) {
  const action = String(command.action || "status"); const args = command.args || {};
  const slot = slotName(args.tab);
  if (action === "status") {
    const current = await existingControlledTab(slot);
    const slots = await controlledTabs();
    if (current?.id) await setControlBar(current.id, "Advertpreneur is controlling this tab", "active");
    return { provider: "existing-edge/extension", running: Boolean(current), tab: slot, tabs: Object.keys(slots), tab_id: current?.id || 0, url: current?.url || "", title: current?.title || "" };
  }
  if (action === "selector_state") {
    const tab = await ensureControlledTab("about:blank", slot);
    const selector = String(args.selector || "");
    if (!selector) throw new Error("selector_state requires selector");
    const result = await executeInTab(tab.id, (sel) => {
      const rows = Array.from(document.querySelectorAll(sel));
      const visible = rows.some(el => { const style = getComputedStyle(el); const rect = el.getBoundingClientRect(); return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0; });
      return {count: rows.length, visible};
    }, [selector]);
    return {provider:"existing-edge/extension", verified:true, ...result};
  }
  if (action === "learn_start") {
    const tab = await ensureControlledTab("about:blank", slot);
    await storageSet(LEARN_KEY, { active: true, name: String(args.name || "routine"), tabId: tab.id, startedAt: Date.now() });
    await setControlBar(tab.id, `Advertpreneur Learn Mode · ${String(args.name || "routine")}`, "working");
    return { provider: "existing-edge/extension", learning: true, tab_id: tab.id, url: tab.url || "", title: tab.title || "", verified: true };
  }
  if (action === "learn_stop") {
    const tab = await existingControlledTab(slot);
    await storageSet(LEARN_KEY, null);
    if (tab?.id) await setControlBar(tab.id, "Advertpreneur is controlling this tab", "active");
    return { provider: "existing-edge/extension", learning: false, tab_id: tab?.id || 0, url: tab?.url || "", title: tab?.title || "", verified: true };
  }
  let tab = await ensureControlledTab("about:blank", slot);
  if (action === "download_mark") {
    const marker = await newestDownloadId();
    return {provider:"existing-edge/extension", marker, verified:true};
  }
  if (action === "download_wait") {
    await setControlBar(tab.id, "Advertpreneur · waiting for download", "working");
    const item = await waitForCompletedDownload(args.marker, args.timeout_ms);
    tab = await chrome.tabs.get(tab.id); await setControlBar(tab.id, "Advertpreneur is controlling this tab", "active");
    await reportProgress("download_completed", item.filename || "", tab);
    return {provider:"existing-edge/extension", download_id:item.id, filename:item.filename, state:item.state, url:tab.url || "", title:tab.title || "", verified:true};
  }
  if (action === "wordpress_state") {
    tab = await chrome.tabs.get(tab.id); const state = await wordpressState(tab.id);
    await reportProgress(state.login_needed ? "login_needed" : (state.authenticated ? "authenticated" : "wordpress_unknown"), state.notices?.[0] || "", tab);
    return {provider:"existing-edge/extension", tab_id:tab.id, verified:true, ...state};
  }
  if (action === "upload") {
    const selector = String(args.selector || 'input[type="file"]'); const filePath = String(args.file_path || "");
    if (!filePath) throw new Error("upload requires file_path");
    tab = await chrome.tabs.get(tab.id); await setControlBar(tab.id, "Advertpreneur · uploading file", "working"); await reportProgress("uploading", filePath, tab);
    await attachDebugger(tab.id);
    try {
      const documentNode = await sendDebugger(tab.id, "DOM.getDocument", {depth: 1});
      const queried = await sendDebugger(tab.id, "DOM.querySelector", {nodeId: documentNode.root.nodeId, selector});
      if (!queried.nodeId) throw new Error(`File input not found: ${selector}`);
      await sendDebugger(tab.id, "DOM.setFileInputFiles", {files: [filePath], nodeId: queried.nodeId});
    } finally { await detachDebugger(tab.id); }
    const observed = await executeInTab(tab.id, (sel) => { const input=document.querySelector(sel); return {selected:input?.files?.[0]?.name || "", count:input?.files?.length || 0}; }, [selector]);
    if (!observed?.selected) throw new Error("Browser did not retain the selected upload file");
    tab = await chrome.tabs.get(tab.id); await setControlBar(tab.id, "Advertpreneur is controlling this tab", "active"); await reportProgress("upload_selected", observed.selected, tab);
    return {provider:"existing-edge/extension", tab_id:tab.id, url:tab.url || "", title:tab.title || "", verified:true, ...observed};
  }
  if (action === "navigate") {
    const url = String(args.url || ""); if (!/^https?:\/\//i.test(url)) throw new Error("navigate requires an http/https URL");
    await setControlBar(tab.id, "Advertpreneur · navigating", "working");
    tab = await chrome.tabs.update(tab.id, { url, active: true }); tab = await waitTab(tab.id, Number(args.timeout_ms || 30000)); await saveControlledTab(slot, tab);
    if (tab?.id) await setControlBar(tab.id, "Advertpreneur is controlling this tab", "active");
    await reportProgress("navigated", "Navigation verified", tab); return { provider: "existing-edge/extension", url: tab?.url || url, title: tab?.title || "", tab_id: tab?.id || 0, verified: true };
  }
  if (action === "inspect" || action === "reverse_engineer") {
    tab = await chrome.tabs.get(tab.id); await setControlBar(tab.id, `Advertpreneur · ${action === "inspect" ? "inspecting" : "reverse engineering"}`, "working");
    const result = await executeInTab(tab.id, snapshotFunction, [String(args.selector || "body"), Number(args.max_elements || (action === "reverse_engineer" ? 140 : 70))]);
    await setControlBar(tab.id, "Advertpreneur is controlling this tab", "active"); if (result?.error) throw new Error(result.error);
    return { provider: "existing-edge/extension", verified: true, ...result };
  }
  if (action === "screenshot") {
    tab = await chrome.tabs.get(tab.id); const selector = String(args.selector || "");
    if (selector) { const r = await executeInTab(tab.id, (sel) => { const el=document.querySelector(sel); if(!el)return{error:`Selector not found: ${sel}`}; el.scrollIntoView({block:"start",inline:"nearest",behavior:"instant"}); return {ok:true}; }, [selector]); if(r?.error)throw new Error(r.error); }
    await activateTab(tab); await sleep(220);
    // Never burn the ADP control capsule into evidence screenshots.
    await executeInTab(tab.id, () => { const x=document.getElementById("adp-browser-control-bar"); if(x)x.style.display="none"; return true; });
    const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "jpeg", quality: 92 });
    await setControlBar(tab.id, "Advertpreneur is controlling this tab", "active");
    return { provider: "existing-edge/extension", url: tab.url || "", title: tab.title || "", data_url: dataUrl, format: "jpeg", viewport_only: true, selector, verified: true };
  }
  if (action === "click") {
    const selector = String(args.selector || ""); const beforeUrl = tab.url || ""; const beforeIds = new Set((await chrome.tabs.query({})).map((row) => row.id)); await setControlBar(tab.id, "Advertpreneur · clicking", "working");
    const result = await executeInTab(tab.id, (sel) => {
      const el=document.querySelector(sel); if(!el)return{error:`Selector not found: ${sel}`};
      const anchor=el.closest("a") || (el.tagName === "A" ? el : null); const target=anchor?.href || "";
      const rect=el.getBoundingClientRect(); el.scrollIntoView({block:"center",inline:"nearest",behavior:"instant"}); el.click();
      return {ok:true,target,element_text:String(el.innerText||el.textContent||"").replace(/\s+/g," ").trim().slice(0,160), box:{x:Math.round(rect.x),y:Math.round(rect.y),width:Math.round(rect.width),height:Math.round(rect.height)}};
    }, [selector]);
    if(result?.error)throw new Error(result.error);
    const expectsNav=Boolean(result?.target && !String(result.target).toLowerCase().startsWith("javascript:")); const deadline=Date.now()+Number(args.verify_ms||5000); let current=await chrome.tabs.get(tab.id);
    while(expectsNav && current?.url===beforeUrl && Date.now()<deadline){await sleep(180); current=await chrome.tabs.get(tab.id);}
    await setControlBar(tab.id, "Advertpreneur is controlling this tab", "active");
    const captured = args.capture_tab ? await captureSpawnedTab(args.capture_tab, beforeIds, tab.windowId, args.capture_timeout_ms) : null;
    return {provider:"existing-edge/extension",tab:slot,captured_tab:captured ? slotName(args.capture_tab) : "",captured_tab_id:captured?.id || 0,clicked:selector,before_url:beforeUrl,url:current?.url||beforeUrl,title:current?.title||"",target:result?.target||"",element_text:result?.element_text||"",navigated:Boolean((current?.url||beforeUrl)!==beforeUrl),verified:true};
  }
  if (action === "fill") {
    const selector=String(args.selector||""); const value=String(args.value||""); await setControlBar(tab.id,"Advertpreneur · filling field","working");
    const result=await executeInTab(tab.id,(sel,val)=>{const el=document.querySelector(sel);if(!el)return{error:`Selector not found: ${sel}`};el.focus();const proto=el instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;const setter=Object.getOwnPropertyDescriptor(proto,"value")?.set;if(setter)setter.call(el,val);else el.value=val;el.dispatchEvent(new Event("input",{bubbles:true}));el.dispatchEvent(new Event("change",{bubbles:true}));return{ok:true};},[selector,value]);
    if(result?.error)throw new Error(result.error); await setControlBar(tab.id,"Advertpreneur is controlling this tab","active");
    return {provider:"existing-edge/extension",filled:selector,url:(await chrome.tabs.get(tab.id))?.url||"",verified:true};
  }
  if (action === "scroll") {
    const amount=args.amount ?? 650; await setControlBar(tab.id,"Advertpreneur · scrolling","working");
    const result=await executeInTab(tab.id,(value)=>{if(typeof value==="string" && !/^[+-]?\d+$/.test(value)){const el=document.querySelector(value);if(!el)return{error:`Selector not found: ${value}`};el.scrollIntoView({block:"center",behavior:"instant"});}else{window.scrollBy({top:Number(value)||0,behavior:"instant"});}return{scroll_y:Math.round(window.scrollY),url:location.href};},[amount]);
    if(result?.error)throw new Error(result.error); await setControlBar(tab.id,"Advertpreneur is controlling this tab","active"); return {provider:"existing-edge/extension",verified:true,...result};
  }
  if (action === "wait") {
    const ms=Math.max(0,Math.min(30000,Number(args.milliseconds||750))); await setControlBar(tab.id,`Advertpreneur · waiting ${ms}ms`,"working"); await sleep(ms); const current=await chrome.tabs.get(tab.id); await setControlBar(tab.id,"Advertpreneur is controlling this tab","active"); return {provider:"existing-edge/extension",url:current?.url||"",title:current?.title||"",verified:true,milliseconds:ms};
  }
  if (action === "close") { try{await chrome.tabs.remove(tab.id);}catch(_){} const slots=await controlledTabs(); delete slots[slot]; await storageSet(TAB_SLOTS_KEY,slots); if(slot==="work")await storageSet(TAB_KEY,null); return {provider:"existing-edge/extension",closed:true,tab:slot,verified:true}; }
  throw new Error(`Unsupported browser action: ${action}`);
}

async function browserProviderLoop() {
  if (browserLoopRunning) return;
  browserLoopRunning = true;
  while (browserLoopRunning) {
    try {
      const id = await registerBrowserProvider();
      const query = new URLSearchParams({ provider_id: id.providerId, token: id.token, wait: "20" });
      const data = await bridgeFetch(`/v1/browser/next?${query.toString()}`, { timeoutMs: 24000 });
      const command = data.command;
      if (command?.command_id) {
        let payload;
        try {
          payload = { provider_id: id.providerId, token: id.token, command_id: command.command_id, ok: true, result: await browserCommand(command) };
        } catch (error) {
          payload = { provider_id: id.providerId, token: id.token, command_id: command.command_id, ok: false, error: String(error?.message || error) };
        }
        await bridgeFetch("/v1/browser/result", { method: "POST", body: payload, timeoutMs: 5000 });
      }
    } catch (_) {
      await sleep(1800);
    }
  }
}

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status !== "complete") return;
  (async () => {
    const controlled = await existingControlledTab();
    if (!controlled?.id || Number(controlled.id) !== Number(tabId)) return;
    const learn = await storageGet(LEARN_KEY, null);
    if (learn?.active) await setControlBar(tabId, `Advertpreneur Learn Mode · ${String(learn.name || "routine")}`, "working");
    else await setControlBar(tabId, "Advertpreneur is controlling this tab", "active");
  })().catch(() => {});
});

chrome.runtime.onInstalled.addListener(() => { scheduleProviderHeartbeat(); browserProviderLoop(); });
chrome.runtime.onStartup.addListener(() => { scheduleProviderHeartbeat(); browserProviderLoop(); });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm?.name === HEARTBEAT_ALARM) browserProviderLoop();
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "ADP_BRIDGE_API") {
    const path = String(message.path || "");
    const options = message.options || {};
    bridgeFetch(path, { method: options.method || "GET", body: options.body, timeoutMs: Number(options.timeoutMs || 6000) })
      .then((data) => sendResponse({ ok: true, data }))
      .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }
  if (message?.type === "ADP_BROWSER_LEARN_EVENT") {
    (async () => {
      const active = await storageGet(LEARN_KEY, null);
      const controlled = await existingControlledTab();
      if (!active?.active || !controlled?.id || Number(_sender?.tab?.id || 0) !== Number(controlled.id)) return;
      const id = await providerIdentity();
      await bridgeFetch("/v1/browser/learn-event", {
        method: "POST",
        body: { provider_id: id.providerId, token: id.token, event: message.event || {} },
        timeoutMs: 3000
      });
    })().then(() => sendResponse({ ok: true })).catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }
  if (message?.type === "ADP_BROWSER_WAKE") {
    browserProviderLoop();
    sendResponse({ ok: true });
    return false;
  }
  return false;
});

scheduleProviderHeartbeat();
browserProviderLoop();
