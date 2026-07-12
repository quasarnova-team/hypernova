"""The browser: a single self-contained page served at /, fed exclusively by
the public JSON API. DIP Browser's job — see what exists, watch live values —
with staleness, rates and copy-paste subscriber snippets."""

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>hypernova registry</title>
<style>
  :root {
    --bg:#f7f8fa; --surface:#ffffff; --ink:#1c2331; --ink-soft:#566178;
    --line:#dde2ea; --accent:#b97a1e; --wire:#3a66b5; --good:#2e7d55; --bad:#b3452e;
    --code:#eef1f6;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#10141d; --surface:#171d29; --ink:#e8ebf1; --ink-soft:#9aa4b5;
      --line:#2a3242; --accent:#e2a23b; --wire:#7fa3e8; --good:#5cc492; --bad:#e08a74;
      --code:#1d2433;
    }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { display:flex; align-items:baseline; gap:14px; padding:18px 24px;
           border-bottom:1px solid var(--line); }
  header h1 { font-size:18px; font-weight:800; margin:0; letter-spacing:-.01em; }
  header h1 span { color:var(--accent); }
  header .stats { color:var(--ink-soft); font-size:13px; }
  main { max-width: 1080px; margin: 0 auto; padding: 20px 24px 60px; }
  .searchbar { margin: 4px 0 16px; }
  .searchbar input { width:100%; max-width:420px; padding:8px 12px; border-radius:8px;
    border:1px solid var(--line); background:var(--surface); color:var(--ink); font:inherit; }
  table { border-collapse:collapse; width:100%; font-variant-numeric: tabular-nums; }
  th { text-align:left; font-size:11px; letter-spacing:.1em; text-transform:uppercase;
       color:var(--ink-soft); padding:8px 12px 8px 0; border-bottom:2px solid var(--line); }
  td { padding:8px 12px 8px 0; border-bottom:1px solid var(--line); vertical-align:top; }
  tr.pub { cursor:pointer; }
  tr.pub:hover td { background:color-mix(in srgb, var(--accent) 6%, transparent); }
  .name { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size:13.5px; }
  .badge { display:inline-block; padding:1px 9px; border-radius:99px; font-size:11.5px;
           font-weight:700; }
  .badge.live { color:var(--good); border:1px solid var(--good); }
  .badge.stale { color:var(--bad); border:1px solid var(--bad); }
  .badge.lease { color:var(--ink-soft); border:1px solid var(--line); margin-left:6px; }
  .badge.signed { color:var(--wire); border:1px solid var(--wire); margin-left:6px; }
  .detail { background:var(--surface); border:1px solid var(--line); border-radius:10px;
            padding:16px 20px; margin:10px 0 18px; }
  .detail h2 { margin:0 0 4px; font-size:16px; font-weight:800; }
  .detail .coords { color:var(--ink-soft); font-size:13px; margin-bottom:10px;
                    font-family: ui-monospace, Menlo, monospace; }
  .values td { font-family: ui-monospace, Menlo, monospace; font-size:13px; }
  .quality-good { color:var(--good); font-weight:700; }
  .quality-bad { color:var(--bad); font-weight:700; }
  pre { background:var(--code); border:1px solid var(--line); border-radius:8px;
        padding:10px 12px; overflow-x:auto; font-size:12.5px; }
  .copyrow { display:flex; gap:10px; align-items:center; margin-top:12px; flex-wrap:wrap; }
  button { font:inherit; font-size:13px; padding:5px 12px; border-radius:7px; cursor:pointer;
           border:1px solid var(--line); background:var(--surface); color:var(--ink); }
  button:hover { border-color: var(--accent); }
  .empty { color:var(--ink-soft); padding:40px 0; text-align:center; }
  .empty code { background:var(--code); padding:2px 6px; border-radius:5px; }
  .error { color:var(--bad); }
</style>
</head>
<body>
<header>
  <h1>hyper<span>nova</span> registry</h1>
  <div class="stats" id="stats">loading…</div>
</header>
<main>
  <div class="searchbar">
    <input id="filter" type="search" placeholder="Filter publications… (e.g. atlas/dcs)" aria-label="Filter publications">
  </div>
  <div id="content"><div class="empty">loading…</div></div>
</main>
<script>
"use strict";
let publications = [];
let openName = decodeURIComponent(location.hash.slice(1)) || null;
let detailCache = {};

const el = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

let detailPrimed = false;
async function refresh() {
  try {
    if (openName && !detailPrimed) {
      detailPrimed = true;
      detailCache[openName] = await fetch("/api/publications/" + openName).then(r => r.ok ? r.json() : null);
    }
    const [pubs, health] = await Promise.all([
      fetch("/api/publications").then(r => r.json()),
      fetch("/api/health").then(r => r.json()),
    ]);
    publications = pubs;
    el("stats").textContent =
      `${health.publications} publication(s) · registry v${health.version}`;
    if (openName) {
      detailCache[openName] = await fetch("/api/publications/" + openName).then(r => r.ok ? r.json() : null);
    }
    render();
  } catch (e) {
    el("stats").innerHTML = '<span class="error">registry unreachable</span>';
  }
}

function snippetPython(p) {
  return `from hypernova import Subscriber

with Subscriber("${p.name}") as sub:
    for update in sub.updates():
        print(update.values)`;
}

function snippetSupernova(p) {
  const fields = p.fields.map(f => `    <Field target="INSTANCE.${f.name}"/>`).join("\n");
  return `<DataSetReader publisherId="${p.publisherId}" publisherIdType="${p.publisherIdType === "UINT16" ? "UInt16" : p.publisherIdType}"\n               writerGroupId="${p.writerGroupId}" dataSetWriterId="${p.dataSetWriterId}">\n${fields}\n</DataSetReader>`;
}

function render() {
  const filter = el("filter").value.trim().toLowerCase();
  const shown = publications.filter(p => !filter || p.name.toLowerCase().includes(filter));
  if (!publications.length) {
    el("content").innerHTML =
      '<div class="empty">No publications registered yet.<br>' +
      'Register one with <code>hypernova register</code> or PUT /api/publications/&lt;name&gt;.</div>';
    return;
  }
  if (!shown.length) {
    el("content").innerHTML = '<div class="empty">Nothing matches the filter.</div>';
    return;
  }
  let html = '<table><tr><th>publication</th><th>fields</th><th>rate</th><th>state</th><th>address</th></tr>';
  for (const p of shown) {
    const live = p.live.stale
      ? '<span class="badge stale">stale' + (p.live.ageSeconds != null ? " " + Math.round(p.live.ageSeconds) + "s" : "") + "</span>"
      : '<span class="badge live">live</span>';
    const lease = (p.leaseExpired && p.live.stale)
      ? '<span class="badge lease">lease expired</span>' : "";
    const signed = p.live.signed
      ? '<span class="badge signed" title="frames carry a signature; the registry holds no key, so it is not verified here">signed</span>' : "";
    html += `<tr class="pub" data-name="${esc(p.name)}">
      <td class="name">${esc(p.name)}</td>
      <td>${p.fields.map(f => esc(f.name)).join(", ")}</td>
      <td>${p.live.rateHz ? p.live.rateHz + " Hz" : "—"}</td>
      <td>${live}${lease}${signed}</td>
      <td class="name">${esc(p.address)}</td></tr>`;
    if (openName === p.name) {
      html += `<tr><td colspan="5">${renderDetail(detailCache[p.name] || p)}</td></tr>`;
    }
  }
  html += "</table>";
  el("content").innerHTML = html;
  for (const row of document.querySelectorAll("tr.pub")) {
    row.addEventListener("click", async () => {
      const name = row.dataset.name;
      openName = openName === name ? null : name;
      history.replaceState(null, "", openName ? "#" + encodeURIComponent(openName) : " ");
      if (openName) {
        detailCache[name] = await fetch("/api/publications/" + name).then(r => r.ok ? r.json() : null);
      }
      render();
    });
  }
  for (const btn of document.querySelectorAll("button[data-copy]")) {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      navigator.clipboard.writeText(decodeURIComponent(btn.dataset.copy));
      btn.textContent = "copied ✓";
      setTimeout(() => (btn.textContent = btn.dataset.label), 1200);
    });
  }
}

function renderDetail(p) {
  let values = "";
  if (p.values && p.values.length) {
    values = '<table class="values"><tr><th>field</th><th>type</th><th>value</th><th>quality</th><th>source time</th></tr>' +
      p.values.map(v => `<tr>
        <td>${esc(v.name)}</td><td>${esc(v.type)}</td><td>${esc(v.value)}</td>
        <td class="${v.good ? "quality-good" : "quality-bad"}">${v.good ? "good" : esc(v.status)}</td>
        <td>${v.sourceTime ? esc(v.sourceTime.replace("T", " ").slice(0, 23)) : "—"}</td></tr>`).join("") +
      "</table>";
  } else {
    values = '<p class="empty" style="padding:12px 0">No values seen yet on ' + esc(p.address) + ".</p>";
  }
  const py = snippetPython(p), sx = snippetSupernova(p);
  return `<div class="detail">
    <h2>${esc(p.name)}</h2>
    <div class="coords">publisher ${p.publisherId} (${esc(p.publisherIdType)}) · writer group ${p.writerGroupId} · dataset writer ${p.dataSetWriterId}
      · ${p.live.messages} msg · ${p.live.lost} lost${p.description ? " · " + esc(p.description) : ""}</div>
    ${values}
    <div class="copyrow">
      <button data-copy="${encodeURIComponent(py)}" data-label="copy Python subscriber">copy Python subscriber</button>
      <button data-copy="${encodeURIComponent(sx)}" data-label="copy supernova DataSetReader">copy supernova DataSetReader</button>
    </div>
  </div>`;
}

el("filter").addEventListener("input", render);
refresh();
setInterval(refresh, 1000);
</script>
</body>
</html>
"""
