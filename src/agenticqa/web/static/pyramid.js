/* Closing 3D testing pyramid - a sliced triangle (front) with a right-side
   depth extrusion, tapering to a point at the apex. Rendered as an SVG string
   and injected via x-html onto a <g> inside an <svg>, so the children parse in
   the SVG namespace and actually paint (a <template x-for> inside <svg> would
   land in the HTML namespace and render invisibly). Interactivity is via event
   delegation on the group (data-layer), not per-band Alpine handlers.

   Palette follows the Cloudera deck: deep blue base rising through cyan, with
   Cloudera orange reserved for the apex (Adversarial / Safety). Live runs
   override a layer's colour with its state (running / passed / failed). */

(function () {
  const H = 58, PH = 290, DX = 30, DY = 16, CX = 322, W0 = 150;
  const BASEY = 28 + PH, VW = 520, VH = BASEY + 30;
  const hw = (lvl) => W0 * (1 - lvl / 5);            // half-width at height level 0..5

  // Cool base warming gradually to Cloudera orange at the apex - the orange
  // eases in over the top two tiers rather than appearing suddenly.
  const PALETTE = {
    unit: "#0e5289", property: "#2181a4", trajectory: "#38a597",
    llm_judge: "#dd9038", adversarial: "#f96302",
  };
  const STATE = { running: "#2563eb", passed: "#16a34a", failed: "#dc2626", error: "#dc2626", skipped: "#94a3b8" };

  function shade(hex, amt) {
    const n = parseInt(hex.slice(1), 16);
    let r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    const t = amt < 0 ? 0 : 255, p = Math.abs(amt);
    r = Math.round(r + (t - r) * p); g = Math.round(g + (t - g) * p); b = Math.round(b + (t - b) * p);
    return `rgb(${r},${g},${b})`;
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pts = (a) => a.map((p) => p.join(",")).join(" ");

  window.pyramidViewBox = `0 0 ${VW} ${VH}`;

  window.pyramid3D = function (layers, opts) {
    const o = opts || {};
    const sel = o.selectedLayer || null;
    const mode = o.mode || "run";       // "run" (tallies) | "anatomy" (descriptions)
    const desc = o.desc || {};
    const interactive = o.interactive !== false;
    const idp = o.idPrefix || mode;     // unique gradient-id namespace per instance
    const rows = [...layers].sort((a, b) => a.order - b.order);  // bottom → top

    const defs = [], faces = [], labels = [];
    faces.push(`<ellipse cx="${CX + DX / 2}" cy="${BASEY + 11}" rx="${W0 * 1.03}" ry="11" fill="rgba(15,23,42,.10)"></ellipse>`);

    rows.forEach((l, i) => {
      const yb = BASEY - i * H, yt = BASEY - (i + 1) * H;
      const wb = hw(i), wt = hw(i + 1);
      const app = l.applicable !== false;
      const stColor = STATE[l.state];
      const col = (mode === "run" && stColor) ? stColor : (PALETTE[l.layer] || "#64748b");
      const gid = `gf-${idp}-${l.layer}`;
      defs.push(`<linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${shade(col, .20)}"/><stop offset="1" stop-color="${shade(col, -.06)}"/></linearGradient>`);
      const right = shade(col, -.30);
      const frontPts = pts([[CX - wb, yb], [CX + wb, yb], [CX + wt, yt], [CX - wt, yt]]);
      const rightPts = pts([[CX + wb, yb], [CX + wt, yt], [CX + wt + DX, yt - DY], [CX + wb + DX, yb - DY]]);
      const outline  = pts([[CX - wt, yt], [CX + wt, yt], [CX + wt + DX, yt - DY], [CX + wb + DX, yb - DY], [CX + wb, yb], [CX - wb, yb]]);
      const cls = ["tier"];
      if (l.layer === sel) cls.push("sel");
      if (!app) cls.push("dim");
      if (l.state === "running") cls.push("running");
      if (!interactive) cls.push("noint");
      const a11y = interactive ? ` role="button" tabindex="0"` : "";
      faces.push(`<g class="${cls.join(" ")}" data-layer="${esc(l.layer)}"${a11y} aria-label="${esc(l.label)}">
        <polygon class="face right" points="${rightPts}" fill="${right}"></polygon>
        <polygon class="face front" points="${frontPts}" fill="url(#${gid})"></polygon>
        <polygon class="edge" points="${outline}"></polygon></g>`);

      // left-column label + leader line to the band's left edge
      const midY = (yt + yb) / 2, wmid = hw(i + 0.5), lx = CX - W0 - 30;
      let sub;
      if (mode === "run") {
        if (l.state === "passed") sub = `✓ ${l.passed}/${l.planned_count || l.passed}`;
        else if (l.state === "failed" || l.state === "error") sub = `✗ ${l.failed} · ✓ ${l.passed}`;
        else if (l.state === "running") sub = "running…";
        else if (!app) sub = "n/a";
        else if (l.planned_count) sub = `${l.planned_count} tests`;
        else sub = desc[l.layer] || "";
      } else {
        sub = desc[l.layer] || (app ? "" : "agentic only");
      }
      const ag = l.agentic_only ? ` <tspan fill="#f96302" dy="-1">✦</tspan>` : "";
      const lblCls = ["lbl"]; if (!interactive) lblCls.push("noint");
      labels.push(`<g class="${lblCls.join(" ")}" data-layer="${esc(l.layer)}"${app ? "" : ' opacity="0.5"'}>
        <line x1="${lx + 9}" y1="${midY}" x2="${CX - wmid - 3}" y2="${midY}" stroke="${col}" stroke-width="1.5" opacity="0.55"></line>
        <circle cx="${CX - wmid - 3}" cy="${midY}" r="2.6" fill="${col}"></circle>
        <text x="${lx}" y="${midY - 3}" text-anchor="end" class="llabel${l.layer === sel ? " on" : ""}">${esc(l.label)}${ag}</text>
        <text x="${lx}" y="${midY + 12}" text-anchor="end" class="ltally">${esc(sub)}</text></g>`);
    });

    return { viewBox: window.pyramidViewBox, svg: `<defs>${defs.join("")}</defs>${faces.join("")}${labels.join("")}` };
  };
})();
