"""The browser: a single self-contained page served at /, fed exclusively by
the public JSON API. The DIP browser's successor — a live namespace tree and
an instrument-style stream pane (values, quality, rate, per-field sparklines,
copy-paste subscribers) — deep-linkable per publication."""

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>hypernova registry</title>
<style>
  :root {
    --bg:#0e1218; --panel:#151b24; --panel2:#1b2430; --line:#242e3c;
    --ink:#e7ecf3; --ink-soft:#8b98ab; --ink-faint:#5b6675;
    --accent:#e3a13c; --accent-dim:#8a6320;
    --good:#48c98a; --warn:#e0a13a; --bad:#e26a4f; --sig:#5f9fe0;
    --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  }
  :root[data-theme="light"] {
    --bg:#f5f7fa; --panel:#ffffff; --panel2:#eef2f7; --line:#dde3ec;
    --ink:#1b2430; --ink-soft:#5a6675; --ink-faint:#95a1b2;
    --accent:#b6771a; --accent-dim:#e5c78a;
    --good:#1f9d63; --warn:#b6771a; --bad:#c0472c; --sig:#2f6fb8;
  }
  @media (prefers-color-scheme: light) {
    :root:not([data-theme="dark"]) {
      --bg:#f5f7fa; --panel:#ffffff; --panel2:#eef2f7; --line:#dde3ec;
      --ink:#1b2430; --ink-soft:#5a6675; --ink-faint:#95a1b2;
      --accent:#b6771a; --accent-dim:#e5c78a;
      --good:#1f9d63; --warn:#b6771a; --bad:#c0472c; --sig:#2f6fb8;
    }
  }
  * { box-sizing:border-box; }
  html,body { height:100%; }
  body {
    margin:0; background:var(--bg); color:var(--ink);
    font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  .mono { font-family:var(--mono); font-variant-numeric:tabular-nums; }

  header {
    height:52px; display:flex; align-items:center; gap:16px; padding:0 18px;
    border-bottom:1px solid var(--line); background:var(--panel);
  }
  .brand { font-weight:800; letter-spacing:-.01em; font-size:16px; }
  .brand b { color:var(--accent); font-weight:800; }
  .brand span { color:var(--ink-faint); font-weight:500; margin-left:8px; font-size:12px; }
  .conn { display:flex; align-items:center; gap:7px; font-size:12.5px; color:var(--ink-soft); }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--good); }
  .dot.down { background:var(--bad); }
  .dot.pulse { animation:pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
  header .spacer { flex:1; }
  .search {
    background:var(--panel2); border:1px solid var(--line); border-radius:8px;
    padding:7px 11px; color:var(--ink); font:inherit; width:240px;
  }
  .search:focus { outline:none; border-color:var(--accent); }
  .iconbtn {
    background:var(--panel2); border:1px solid var(--line); border-radius:8px;
    width:34px; height:34px; color:var(--ink-soft); cursor:pointer; font-size:15px;
  }
  .iconbtn:hover { border-color:var(--accent); color:var(--ink); }

  .shell { display:grid; grid-template-columns:320px 1fr; height:calc(100vh - 52px); }
  aside {
    border-right:1px solid var(--line); background:var(--panel);
    overflow-y:auto; padding:10px 8px;
  }
  .treehead {
    font-size:11px; letter-spacing:.12em; text-transform:uppercase;
    color:var(--ink-faint); padding:6px 10px 10px; font-weight:700;
  }
  .node { user-select:none; }
  .row {
    display:flex; align-items:center; gap:7px; padding:5px 8px; border-radius:7px;
    cursor:pointer; font-size:13.5px;
  }
  .row:hover { background:var(--panel2); }
  .row.sel { background:color-mix(in srgb, var(--accent) 16%, transparent); }
  .caret { width:12px; color:var(--ink-faint); font-size:10px; transition:transform .12s; flex:none; }
  .caret.open { transform:rotate(90deg); }
  .caret.leaf { visibility:hidden; }
  .seg { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .leaf-label { font-family:var(--mono); }
  .count { font-size:11px; color:var(--ink-faint); }
  .sdot { width:7px; height:7px; border-radius:50%; flex:none; }
  .sdot.live { background:var(--good); }
  .sdot.stale { background:var(--bad); }
  .sdot.none { background:var(--ink-faint); }
  .children { margin-left:14px; border-left:1px solid var(--line); padding-left:2px; }

  main { overflow-y:auto; padding:24px 28px; }
  .empty {
    height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center;
    color:var(--ink-faint); gap:10px; text-align:center;
  }
  .empty svg { opacity:.5; }
  .empty code { background:var(--panel2); padding:2px 7px; border-radius:5px; color:var(--ink-soft); }

  .detail h1 { font-family:var(--mono); font-size:22px; font-weight:700; margin:0 0 6px; letter-spacing:-.01em; }
  .badges { display:flex; gap:7px; flex-wrap:wrap; margin-bottom:4px; }
  .badge { font-size:11.5px; font-weight:700; padding:2px 10px; border-radius:99px; border:1px solid; }
  .badge.live { color:var(--good); border-color:var(--good); }
  .badge.stale { color:var(--bad); border-color:var(--bad); }
  .badge.signed { color:var(--sig); border-color:var(--sig); }
  .badge.lease { color:var(--ink-soft); border-color:var(--line); }
  .badge.fx { color:var(--accent); border-color:var(--accent); }
  .coords { color:var(--ink-soft); font-size:13px; margin:8px 0 20px; font-family:var(--mono); }
  .coords .desc { color:var(--ink-faint); }

  /* FX provenance: this stream was wired by an `fx link` */
  .fxbar { display:flex; flex-wrap:wrap; align-items:center; gap:6px 12px; margin:0 0 20px;
           padding:11px 14px; border-radius:10px;
           background:color-mix(in srgb, var(--accent) 8%, var(--panel));
           border:1px solid color-mix(in srgb, var(--accent) 32%, var(--line)); }
  .fxbar .k { font-size:10.5px; font-weight:800; letter-spacing:.09em; text-transform:uppercase; color:var(--accent); }
  .fxbar .conn { font-family:var(--mono); font-weight:700; color:var(--ink); }
  .fxbar .flow { font-family:var(--mono); font-size:12.5px; color:var(--ink-soft); }
  .fxbar .flow .arrow { color:var(--accent); font-weight:700; padding:0 3px; }
  .fxbar .flow .srv { color:var(--ink-faint); }
  .fxtag { font-family:var(--mono); font-size:9px; font-weight:800; letter-spacing:.04em; color:var(--accent);
           border:1px solid var(--accent); border-radius:4px; padding:0 3px; line-height:1.6; flex:none; }

  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:12px; margin-bottom:22px; }
  .stat { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
  .stat .k { font-size:11px; letter-spacing:.09em; text-transform:uppercase; color:var(--ink-faint); font-weight:700; }
  .stat .v { font-family:var(--mono); font-size:22px; font-variant-numeric:tabular-nums; margin-top:3px; }
  .stat .v small { font-size:12px; color:var(--ink-soft); }

  .panel { background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
  .panel h2 { font-size:12px; letter-spacing:.09em; text-transform:uppercase; color:var(--ink-faint);
              font-weight:700; margin:0; padding:14px 16px 10px; }
  table { width:100%; border-collapse:collapse; }
  th { text-align:left; font-size:11px; letter-spacing:.06em; text-transform:uppercase;
       color:var(--ink-faint); font-weight:700; padding:6px 16px; border-bottom:1px solid var(--line); }
  td { padding:11px 16px; border-bottom:1px solid var(--line); vertical-align:middle; font-family:var(--mono);
       font-variant-numeric:tabular-nums; font-size:13px; }
  tr:last-child td { border-bottom:none; }
  td.fname { color:var(--ink); }
  td.ftype { color:var(--ink-faint); font-size:12px; }
  td.fval { color:var(--accent); font-weight:600; max-width:280px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .qual { display:inline-flex; align-items:center; gap:6px; }
  .qdot { width:8px; height:8px; border-radius:50%; }
  .qdot.good { background:var(--good); }
  .qdot.bad { background:var(--bad); }
  .qlabel.good { color:var(--good); } .qlabel.bad { color:var(--bad); }
  canvas.spark { display:block; }
  td.ftime { color:var(--ink-soft); font-size:12px; }

  .actions { display:flex; gap:10px; margin-top:18px; flex-wrap:wrap; }
  .btn { font:inherit; font-size:13px; padding:8px 14px; border-radius:8px; cursor:pointer;
         border:1px solid var(--line); background:var(--panel); color:var(--ink); display:inline-flex; gap:8px; align-items:center; }
  .btn:hover { border-color:var(--accent); }
  .btn svg { opacity:.7; }
  .note { color:var(--ink-faint); font-size:12.5px; margin-top:16px; }
</style>
</head>
<body>
<header>
  <div class="brand">hyper<b>nova</b><span>registry</span></div>
  <div class="conn"><span class="dot pulse" id="conndot"></span><span id="conntext">connecting…</span></div>
  <div class="spacer"></div>
  <input class="search" id="search" type="search" placeholder="Filter namespace…" aria-label="Filter namespace">
  <button class="iconbtn" id="theme" title="Toggle theme" aria-label="Toggle theme">◐</button>
</header>
<div class="shell">
  <aside>
    <div class="treehead">namespace</div>
    <div id="tree"></div>
  </aside>
  <main id="main"></main>
</div>
<script>
"use strict";
const el = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

let publications = [];
let selected = decodeURIComponent(location.hash.slice(1)) || null;
let detail = null;
const expanded = new Set();          // group paths the user opened
if (selected) {                      // reveal the deep-linked stream in the tree
  const parts = selected.split("/");
  for (let i = 1; i < parts.length; i++) expanded.add(parts.slice(0, i).join("/"));
}
let history = {};                    // field name -> [numeric values] for the selected stream

// theme
el("theme").onclick = () => {
  const cur = document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("hn-theme", next);
  render();
};
const savedTheme = localStorage.getItem("hn-theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;

el("search").oninput = () => renderTree();

async function tick() {
  try {
    const [pubs, health] = await Promise.all([
      fetch("/api/publications").then(r => r.json()),
      fetch("/api/health").then(r => r.json()),
    ]);
    publications = pubs;
    el("conndot").className = "dot";
    el("conntext").textContent = `${health.publications} publication${health.publications===1?"":"s"} · v${health.version}`;
    if (selected) {
      const d = await fetch("/api/publications/" + selected).then(r => r.ok ? r.json() : null);
      if (d) { detail = d; pushHistory(d); } else { detail = null; }
    }
    render();
  } catch (e) {
    el("conndot").className = "dot down";
    el("conntext").textContent = "registry unreachable";
  }
}

function pushHistory(d) {
  for (const v of (d.values || [])) {
    const n = typeof v.value === "number" ? v.value
            : Array.isArray(v.value) && typeof v.value[0] === "number" ? v.value[0] : null;
    if (n === null) continue;
    (history[v.name] = history[v.name] || []).push(n);
    if (history[v.name].length > 80) history[v.name].shift();
  }
}

function render() { renderTree(); renderDetail(); }

// ---- namespace tree ----
function buildTree(pubs) {
  const root = {};
  for (const p of pubs) {
    const parts = p.name.split("/");
    let node = root;
    parts.forEach((seg, i) => {
      node.children = node.children || {};
      node.children[seg] = node.children[seg] || {seg, path: parts.slice(0, i+1).join("/")};
      if (i === parts.length - 1) node.children[seg].pub = p;
      node = node.children[seg];
    });
  }
  return root;
}
function rollup(node) {
  if (node.pub) return {count:1, anyStale: node.pub.live.stale, seen: node.pub.live.lastSeen!=null};
  let count=0, anyStale=false, seen=false;
  for (const k in (node.children||{})) { const r = rollup(node.children[k]); count+=r.count; anyStale = anyStale||r.anyStale; seen=seen||r.seen; }
  return {count, anyStale, seen};
}
function renderTree() {
  const filter = el("search").value.trim().toLowerCase();
  const shown = filter ? publications.filter(p => p.name.toLowerCase().includes(filter)) : publications;
  if (!publications.length) {
    el("tree").innerHTML = '<div style="padding:16px 10px;color:var(--ink-faint);font-size:13px">No publications yet.<br>Register one with <span class="mono">hypernova register</span>.</div>';
    return;
  }
  const tree = buildTree(shown);
  const frag = document.createElement("div");
  walk(tree, frag, !!filter);
  el("tree").innerHTML = "";
  el("tree").appendChild(frag);
}
function walk(node, container, forceOpen) {
  const kids = node.children || {};
  Object.keys(kids).sort().forEach(seg => {
    const child = kids[seg];
    const wrap = document.createElement("div"); wrap.className = "node";
    const row = document.createElement("div"); row.className = "row";
    const isLeaf = !!child.pub;
    const open = forceOpen || expanded.has(child.path);
    const roll = rollup(child);
    const state = !roll.seen ? "none" : roll.anyStale ? "stale" : "live";

    if (isLeaf) {
      const lv = child.pub.live;
      row.classList.toggle("sel", child.path === selected);
      row.innerHTML = `<span class="caret leaf">▶</span>
        <span class="sdot ${lv.stale ? "stale" : (lv.lastSeen!=null?"live":"none")}"></span>
        <span class="seg leaf-label">${esc(seg)}</span>
        ${child.pub.fx ? '<span class="fxtag" title="wired by an fx link">FX</span>' : ""}
        <span class="count">${lv.rateHz ? lv.rateHz+" Hz" : ""}</span>`;
      row.onclick = () => {
        selected = (selected===child.path ? null : child.path);
        history = {}; detail = null;
        location.hash = selected ? encodeURIComponent(selected) : "";
        tick();
      };
    } else {
      row.innerHTML = `<span class="caret ${open?"open":""}">▶</span>
        <span class="sdot ${state}"></span>
        <span class="seg">${esc(seg)}</span>
        <span class="count">${roll.count}</span>`;
      row.onclick = () => { if (expanded.has(child.path)) expanded.delete(child.path); else expanded.add(child.path); renderTree(); };
    }
    wrap.appendChild(row);
    if (!isLeaf && open) {
      const c = document.createElement("div"); c.className = "children";
      walk(child, c, forceOpen);
      wrap.appendChild(c);
    }
    container.appendChild(wrap);
  });
}

// ---- stream detail ----
function isMulticast(address) {                     // opc.udp://239.x.x.x:port
  const m = /:\/\/(\d{1,3})\./.exec(address || "");
  return !!m && +m[1] >= 224 && +m[1] <= 239;
}
function fmt(v) {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return "[" + v.slice(0,8).map(fmt).join(", ") + (v.length>8?", …":"") + "]";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(8)));
  return String(v);
}
function renderDetail() {
  const main = el("main");
  if (!selected || !detail) {
    main.innerHTML = `<div class="empty">
      <svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4">
        <circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/>
        <path d="M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" opacity=".5"/>
      </svg>
      <div>Select a publication to watch it live.</div>
      <div style="font-size:12.5px">Its values, quality and rate stream here.</div>
    </div>`;
    return;
  }
  const p = detail, live = p.live;
  const stateBadge = live.stale
    ? `<span class="badge stale">stale${live.ageSeconds!=null?" · "+Math.round(live.ageSeconds)+"s":""}</span>`
    : `<span class="badge live">live</span>`;
  const signed = live.signed ? `<span class="badge signed" title="frames carry a signature; the registry holds no key so it is shown unverified">signed</span>` : "";
  const lease = (p.leaseExpired && live.stale) ? `<span class="badge lease">lease expired</span>` : "";
  const fxBadge = p.fx ? `<span class="badge fx" title="this stream was created by an FX link">FX link</span>` : "";
  const fxBar = p.fx ? `<div class="fxbar">
      <span class="k">FX</span><span class="conn">${esc(p.fx.connection)}</span>
      <span class="flow"><span class="srv">${esc(p.fx.publisher.server)}</span> ${esc(p.fx.publisher.entity)}/${esc(p.fx.publisher.dataset)}<span class="arrow">→</span><span class="srv">${esc(p.fx.subscriber.server)}</span> ${esc(p.fx.subscriber.entity)}/${esc(p.fx.subscriber.dataset)}</span>
    </div>` : "";
  const fxNote = (p.fx && live.stale && !isMulticast(p.address))
    ? `<div class="note">This FX link publishes point-to-point (unicast) straight to the subscriber, so the registry can't join it to show values here — the link is real and running; the servers hold the live state. Check it with <span class="mono">hypernova fx status</span>.</div>`
    : "";

  const rows = (p.values||[]).map(v => {
    const good = v.good;
    const hist = history[v.name] || [];
    const spark = (hist.length>1 && typeof v.value==="number") ? `<canvas class="spark" data-field="${esc(v.name)}" width="130" height="26"></canvas>` : "";
    return `<tr>
      <td class="fname">${esc(v.name)}</td>
      <td class="ftype">${esc(v.type)}</td>
      <td class="fval" title="${esc(fmt(v.value))}">${esc(fmt(v.value))}</td>
      <td>${spark}</td>
      <td><span class="qual"><span class="qdot ${good?"good":"bad"}"></span><span class="qlabel ${good?"good":"bad"}">${good?"good":esc(v.status)}</span></span></td>
      <td class="ftime">${v.sourceTime ? esc(v.sourceTime.replace("T"," ").replace(/\+.*/,"").slice(0,23)) : "—"}</td>
    </tr>`;
  }).join("");

  main.innerHTML = `<div class="detail">
    <h1>${esc(p.name)}</h1>
    <div class="badges">${stateBadge}${fxBadge}${signed}${lease}</div>
    <div class="coords">publisher ${p.publisherId} (${esc(p.publisherIdType)}) · writer group ${p.writerGroupId} · dataset writer ${p.dataSetWriterId} · <span class="mono">${esc(p.address)}</span>${p.description?` · <span class="desc">${esc(p.description)}</span>`:""}</div>
    ${fxBar}
    <div class="stats">
      <div class="stat"><div class="k">rate</div><div class="v">${live.rateHz||0}<small> Hz</small></div></div>
      <div class="stat"><div class="k">messages</div><div class="v">${(live.messages||0).toLocaleString()}</div></div>
      <div class="stat"><div class="k">lost</div><div class="v">${live.lost}</div></div>
      <div class="stat"><div class="k">last seen</div><div class="v">${live.ageSeconds!=null?live.ageSeconds.toFixed(1):"—"}<small>${live.ageSeconds!=null?"s ago":""}</small></div></div>
    </div>
    <div class="panel">
      <h2>fields</h2>
      <table><thead><tr><th>field</th><th>type</th><th>value</th><th></th><th>quality</th><th>source time</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="6" style="color:var(--ink-faint)">no values seen yet on '+esc(p.address)+'</td></tr>'}</tbody></table>
    </div>
    <div class="actions">
      <button class="btn" data-copy="${encodeURIComponent(snippetPy(p))}" data-label="Copy Python subscriber">${copyIcon()} Copy Python subscriber</button>
      <button class="btn" data-copy="${encodeURIComponent(snippetXml(p))}" data-label="Copy supernova DataSetReader">${copyIcon()} Copy supernova DataSetReader</button>
    </div>
    ${live.signed ? '<div class="note">This stream is signed. The registry shows values unverified (it holds no keys); a subscriber with the key verifies them.</div>' : ''}
    ${fxNote}
  </div>`;

  document.querySelectorAll("canvas.spark").forEach(c => drawSpark(c, history[c.dataset.field] || []));
  document.querySelectorAll("button[data-copy]").forEach(b => b.onclick = () => {
    navigator.clipboard.writeText(decodeURIComponent(b.dataset.copy));
    const label = b.dataset.label; b.innerHTML = copyIcon() + " copied ✓";
    setTimeout(() => b.innerHTML = copyIcon() + " " + label, 1200);
  });
}
function copyIcon() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>'; }
function snippetPy(p) {
  return `from hypernova import Subscriber\n\nwith Subscriber("${p.name}") as sub:\n    for update in sub.updates():\n        print(update.values)`;
}
function snippetXml(p) {
  const f = p.fields.map(x => `    <Field target="INSTANCE.${x.name}"/>`).join("\n");
  const t = p.publisherIdType==="UINT16"?"UInt16":p.publisherIdType;
  return `<DataSetReader publisherId="${p.publisherId}" publisherIdType="${t}"\n               writerGroupId="${p.writerGroupId}" dataSetWriterId="${p.dataSetWriterId}">\n${f}\n</DataSetReader>`;
}
function drawSpark(canvas, values) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height, pad = 2;
  ctx.clearRect(0,0,w,h);
  if (values.length < 2) return;
  const min = Math.min(...values), max = Math.max(...values), span = (max-min)||1;
  const x = i => pad + i*(w-2*pad)/(values.length-1);
  const y = v => h-pad - (v-min)/span*(h-2*pad);
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  ctx.beginPath(); ctx.moveTo(x(0), h);
  values.forEach((v,i) => ctx.lineTo(x(i), y(v))); ctx.lineTo(x(values.length-1), h); ctx.closePath();
  ctx.fillStyle = accent + "22"; ctx.fill();
  ctx.beginPath(); values.forEach((v,i) => i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v)));
  ctx.strokeStyle = accent; ctx.lineWidth = 1.4; ctx.stroke();
  ctx.beginPath(); ctx.arc(x(values.length-1), y(values[values.length-1]), 2, 0, 7); ctx.fillStyle = accent; ctx.fill();
}

tick();
setInterval(tick, 1000);
</script>
</body>
</html>
"""
