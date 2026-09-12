# Advertpreneur Browser Bridge 0.2

This unpacked Chrome/Edge extension has two local-only jobs:

1. pair a ChatGPT conversation with one Advertpreneur CLI session for completed-result relay;
2. act as Advertpreneur's preferred browser provider inside the user's existing Edge/Chrome profile.

Browser-provider commands are received from the localhost broker (`127.0.0.1:8765`) and run in one ADP-owned tab. Supported basics: navigate, inspect DOM/computed styles, reverse-engineer layout, viewport screenshot, click, fill, status and close.

The browser extension does not call Ollama and does not store Codex/AGY credentials.

After an Advertpreneur upgrade that changes this extension, open `edge://extensions` (or `chrome://extensions`) and click **Reload** once on Advertpreneur Browser Bridge.
