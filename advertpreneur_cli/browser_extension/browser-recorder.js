"use strict";
(() => {
  if (window.__ADP_BROWSER_RECORDER__) return;
  window.__ADP_BROWSER_RECORDER__ = true;
  let lastScrollY = Math.round(window.scrollY || 0);
  let userScrollUntil = 0;
  let scrollTimer = 0;

  function esc(value) {
    try { return CSS.escape(String(value)); } catch (_) { return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&"); }
  }
  function selectorFor(el) {
    if (!(el instanceof Element)) return "";
    if (el.id) return `#${esc(el.id)}`;
    const testid = el.getAttribute("data-testid"); if (testid) return `[data-testid="${String(testid).replace(/"/g,'\\"')}"]`;
    const aria = el.getAttribute("aria-label"); if (aria) return `${el.tagName.toLowerCase()}[aria-label="${String(aria).replace(/"/g,'\\"')}"]`;
    const name = el.getAttribute("name"); if (name) return `${el.tagName.toLowerCase()}[name="${String(name).replace(/"/g,'\\"')}"]`;
    const anchor = el.closest("a[href]");
    if (anchor) {
      const href = anchor.getAttribute("href") || "";
      if (href && href !== "#") return `a[href="${String(href).replace(/"/g,'\\"')}"]`;
    }
    const parts=[]; let node=el;
    while (node && node.nodeType===1 && parts.length<5) {
      let part=node.tagName.toLowerCase();
      const classes=Array.from(node.classList||[]).filter(x=>x && !/^(active|selected|hover|focus|open)$/i.test(x)).slice(0,2);
      if(classes.length) part += classes.map(x=>`.${esc(x)}`).join("");
      const parent=node.parentElement;
      if(parent){ const same=Array.from(parent.children).filter(x=>x.tagName===node.tagName); if(same.length>1) part += `:nth-of-type(${same.indexOf(node)+1})`; }
      parts.unshift(part); node=parent;
      if(node?.id){parts.unshift(`#${esc(node.id)}`);break;}
    }
    return parts.join(" > ");
  }
  function send(event) {
    try { chrome.runtime.sendMessage({ type: "ADP_BROWSER_LEARN_EVENT", event }, () => void chrome.runtime.lastError); } catch (_) {}
  }
  document.addEventListener("click", (e) => {
    if (!e.isTrusted) return;
    const el=e.target instanceof Element ? (e.target.closest("a,button,[role=button],input,select,textarea") || e.target) : null;
    const selector=selectorFor(el); if(!selector) return;
    const a=el?.closest?.("a[href]");
    send({ action:"click", args:{selector}, evidence:{before_url:location.href,target:a?.href||"",element_text:String(el?.innerText||el?.textContent||"").replace(/\s+/g," ").trim().slice(0,160),verified:true} });
  }, true);
  document.addEventListener("change", (e) => {
    if (!e.isTrusted) return;
    const el=e.target; if(!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement)) return;
    const selector=selectorFor(el); if(!selector) return;
    const sensitive = /password|passwd|secret|token|otp|pin|cvv|card|auth/i.test(`${el.type||""} ${el.name||""} ${el.id||""} ${el.autocomplete||""}`);
    send({ action:"fill", args:{selector,value:sensitive?"[NOT_STORED_SENSITIVE_VALUE]":String(el.value||"")}, evidence:{url:location.href,verified:true} });
  }, true);
  for (const type of ["wheel","touchstart"]) window.addEventListener(type, (e)=>{ if(e.isTrusted) userScrollUntil=Date.now()+1200; }, {capture:true,passive:true});
  window.addEventListener("keydown", (e)=>{ if(e.isTrusted && ["PageDown","PageUp","ArrowDown","ArrowUp","Home","End"," "].includes(e.key)) userScrollUntil=Date.now()+1200; }, true);
  window.addEventListener("scroll", () => {
    if(Date.now()>userScrollUntil) return;
    clearTimeout(scrollTimer);
    scrollTimer=setTimeout(()=>{ const y=Math.round(window.scrollY||0); const delta=y-lastScrollY; lastScrollY=y; if(Math.abs(delta)>=20) send({action:"scroll",args:{amount:delta},evidence:{url:location.href,scroll_y:y,verified:true}}); },280);
  }, {passive:true});
})();
