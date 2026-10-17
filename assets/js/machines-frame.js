(() => {
  "use strict";
  const frame = document.getElementById("machines-relay-frame");
  const view = document.getElementById("machines-relay-view");
  const standard = document.getElementById("machines-standard");
  const help = document.getElementById("machines-connection-help");
  if (!frame || !view || !standard) return;
  const origin = "https://43.155.209.84";
  let selected = false;
  // The page transport can work even when a mobile browser blocks cross-site fetch.
  // Keep the existing fast JSON routes; the first fresh result chooses one view.
  document.addEventListener("machines:status", event => {
    if (selected || !event.detail?.fresh || event.detail.fromCache) return;
    selected = true; view.remove(); help.hidden = true;
  });
  window.addEventListener("message", event => {
    if (event.origin !== origin || event.source !== frame.contentWindow
        || event.data?.type !== "machines-view-v1") return;
    const height = event.data.height;
    if (Number.isFinite(height) && height >= 500 && height <= 30000) frame.style.height = `${Math.ceil(height)}px`;
    if (selected && !window.MACHINES_PAUSED) return;
    if (event.data.ready !== true) return;
    selected = true; window.MACHINES_PAUSED = true;
    standard.hidden = true; view.dataset.pending = "false"; help.hidden = true;
  });
  frame.addEventListener("load", () => {
    if (!selected) frame.contentWindow?.postMessage({type: "machines-parent-v1"}, origin);
  });
  view.hidden = false;
  frame.src = `${origin}/machines/?embed=1`;
  setTimeout(() => { if (!selected) help.hidden = false; }, 10000);
})();
