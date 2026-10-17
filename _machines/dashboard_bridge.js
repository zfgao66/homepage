(() => {
  "use strict";
  let ready = false;
  let parentOrigin = null;
  const parents = ["http://gaozefeng.tech", "https://gaozefeng.tech", "http://www.gaozefeng.tech", "https://www.gaozefeng.tech", "http://127.0.0.1:8788"];
  function report() {
    if (window.parent === window || !parentOrigin) return;
    const height = Math.max(500, document.body.scrollHeight);
    window.parent.postMessage({type: "machines-view-v1", ready, height}, parentOrigin);
  }
  window.addEventListener("message", event => {
    if (event.source !== window.parent || !parents.includes(event.origin)
        || event.data?.type !== "machines-parent-v1") return;
    parentOrigin = event.origin; report();
  });
  document.addEventListener("machines:status", event => {
    ready = Boolean(event.detail?.fresh && !event.detail.fromCache);
    report();
  });
  new ResizeObserver(report).observe(document.body);
  window.addEventListener("resize", report);
})();
