"""
Render docs/journey.json → docs/journey.html (self-contained, no network).

    uv run python docs/build_journey_html.py

The JSON is the source of truth; the HTML embeds it and renders timeline,
findings, experiments (with small comparison bars), decisions, artefacts and
open items. Re-run after editing the JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / "journey.json"
OUT = HERE / "journey.html"

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>healthcare-rag — journey</title>
<style>
  :root{--bg:#fbfaf7;--fg:#1f2328;--muted:#5d646d;--card:#ffffff;--line:#e6e2d9;--accent:#c8471f;--accent2:#e5a03a;--ok:#2e7d4f;--warn:#b7791f;--bad:#b3261e;--info:#2b5f9e;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
  @media (prefers-color-scheme: dark){:root{--bg:#141516;--fg:#e8e6e1;--muted:#a3a39c;--card:#1d1f21;--line:#33363a;--accent:#f0764f;--accent2:#e5a03a}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}
  header{padding:36px 24px 20px;border-bottom:3px solid var(--accent);background:linear-gradient(90deg,transparent 60%,rgba(229,160,58,.12))}
  h1{margin:0 0 6px;font-size:28px;letter-spacing:-.01em}
  h2{margin:34px 0 12px;font-size:20px;border-left:4px solid var(--accent2);padding-left:10px}
  h3{margin:0 0 6px;font-size:15px}
  main{max-width:1180px;margin:0 auto;padding:0 24px 60px}
  .sub{color:var(--muted);max-width:900px}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0 6px}
  .kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
  .kpi .v{font-size:24px;font-weight:650;letter-spacing:-.01em}
  .kpi .l{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
  .tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--muted);margin-right:6px;vertical-align:middle}
  .sev-high{border-left:5px solid var(--bad)} .sev-medium{border-left:5px solid var(--warn)} .sev-low{border-left:5px solid var(--info)}
  .status{font-size:12px;font-weight:600}
  .status.fixed,.status.done,.status.decided,.status.documented,.status.reported{color:var(--ok)}
  .status.open{color:var(--bad)}
  .tl{position:relative;margin:0;padding:0 0 0 22px;list-style:none;border-left:2px solid var(--line)}
  .tl li{position:relative;margin:0 0 16px}
  .tl li::before{content:"";position:absolute;left:-28px;top:6px;width:10px;height:10px;border-radius:50%;background:var(--accent)}
  .when{font-family:var(--mono);font-size:12px;color:var(--muted)}
  .refs a,.refs span{font-family:var(--mono);font-size:11px;color:var(--info);margin-right:6px}
  table{border-collapse:collapse;width:100%;font-size:13px;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
  th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
  th{background:rgba(229,160,58,.12);font-weight:600}
  .wrap{overflow-x:auto}
  .bars{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin-top:12px}
  .bar{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
  .bar h4{margin:0 0 8px;font-size:13px;color:var(--muted);font-weight:600}
  .row{display:grid;grid-template-columns:150px 1fr 60px;gap:8px;align-items:center;font-size:12px;margin:4px 0}
  .track{height:10px;background:var(--line);border-radius:6px;overflow:hidden}
  .fill{height:100%;background:var(--accent)}
  .fill.good{background:var(--ok)}
  .mono{font-family:var(--mono);font-size:12px}
  details summary{cursor:pointer;color:var(--muted)}
  footer{color:var(--muted);font-size:12px;margin-top:40px}
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <div class="sub" id="goal"></div>
  <div class="kpis" id="kpis"></div>
</header>
<main>
  <h2>Timeline</h2>
  <ul class="tl" id="timeline"></ul>

  <h2>Findings</h2>
  <div class="grid" id="findings"></div>

  <h2>Experiments</h2>
  <div class="wrap"><table id="exp"></table></div>
  <div class="bars" id="bars"></div>

  <h2>Decisions</h2>
  <div class="grid" id="decisions"></div>

  <h2>Open items</h2>
  <div class="grid" id="open"></div>

  <h2>Artefacts left in the repo</h2>
  <div class="wrap"><table id="art"></table></div>

  <footer id="foot"></footer>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const J = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = (v, k) => v == null ? '–' : (k && k.includes('usd') ? '$' + Number(v).toFixed(4) : (typeof v === 'number' ? (Number.isInteger(v) ? v : Number(v).toFixed(2)) : v));

document.title = J.project + ' — journey';
$('title').textContent = J.project + ' — journey so far';
$('goal').innerHTML = esc(J.goal) + '<br><span class="mono">started ' + esc(J.started) + ' · last updated ' + esc(J.last_updated) + '</span>';

const base = (J.experiments || []).find(e => (e.note||'').includes('reference')) || (J.experiments||[])[0];
if (base) {
  const m = base.metrics || {};
  const tiles = [['correctness', m.correctness], ['safe redirect', m.safe_redirect], ['groundedness', m.groundedness], ['p50 latency', m.latency_p50_s != null ? m.latency_p50_s + ' s' : null], ['cost / query', m.est_cost_usd != null ? '$' + m.est_cost_usd.toFixed(4) : null], ['LLM calls / query', m.llm_calls]];
  $('kpis').innerHTML = tiles.map(([l, v]) => `<div class="kpi"><div class="v">${esc(fmt(v))}</div><div class="l">baseline · ${esc(l)}</div></div>`).join('');
}

$('timeline').innerHTML = (J.timeline || []).map(t => `
  <li><div class="when">${esc(t.id)} · ${esc(t.when)}</div>
  <div><strong>${esc(t.step)}</strong></div>
  <div class="sub">${esc(t.outcome)}</div>
  <div class="refs">${(t.findings||[]).map(f=>`<span>${esc(f)}</span>`).join('')}${(t.experiments||[]).map(e=>`<span>${esc(e)}</span>`).join('')}${(t.decisions||[]).map(d=>`<span>${esc(d)}</span>`).join('')}</div></li>`).join('');

const sevOrder = {high: 0, medium: 1, low: 2};
$('findings').innerHTML = (J.findings || []).slice().sort((a,b)=>(sevOrder[a.severity]??9)-(sevOrder[b.severity]??9)).map(f => `
  <div class="card sev-${esc(f.severity)}">
    <div><span class="tag">${esc(f.id)}</span><span class="tag">${esc(f.area)}</span><span class="tag">${esc(f.severity)}</span> <span class="status ${esc((f.status||'').split(' ')[0])}">${esc(f.status)}</span></div>
    <h3>${esc(f.title)}</h3>
    <div>${esc(f.detail)}</div>
    <details><summary>evidence</summary><div class="mono">${esc(f.evidence)}</div></details>
  </div>`).join('');

const exps = J.experiments || [];
const keys = ['correctness','groundedness','hallucinated','behavior_match','safe_redirect','chunk_recall','latency_p50_s','latency_p95_s','est_cost_usd','llm_calls','n_branches'];
$('exp').innerHTML = '<tr><th>experiment</th><th>config</th><th>split</th>' + keys.map(k=>`<th>${esc(k)}</th>`).join('') + '<th>note</th></tr>' +
  exps.map(e => `<tr><td class="mono">${esc(e.name)}</td><td>${esc(e.config)}</td><td>${esc(e.split)}</td>` + keys.map(k=>`<td>${esc(fmt((e.metrics||{})[k], k))}</td>`).join('') + `<td class="sub">${esc(e.note||'')}</td></tr>`).join('');

function barBlock(title, key, lowerBetter, fmtv) {
  const vals = exps.map(e => (e.metrics||{})[key]).filter(v => v != null);
  if (!vals.length) return '';
  const max = Math.max(...vals) || 1;
  const bestVal = lowerBetter ? Math.min(...vals) : Math.max(...vals);
  return `<div class="bar"><h4>${esc(title)}${lowerBetter?' (lower is better)':''}</h4>` + exps.map(e => {
    const v = (e.metrics||{})[key]; if (v == null) return '';
    const w = Math.max(2, 100 * v / max);
    return `<div class="row"><span class="mono">${esc(e.name.split('-').slice(0,2).join('-'))}</span><div class="track"><div class="fill ${v===bestVal?'good':''}" style="width:${w}%"></div></div><span>${esc(fmtv(v))}</span></div>`;
  }).join('') + '</div>';
}
$('bars').innerHTML = [
  barBlock('cost per query', 'est_cost_usd', true, v => '$'+v.toFixed(4)),
  barBlock('latency p50', 'latency_p50_s', true, v => v.toFixed(1)+' s'),
  barBlock('correctness (judge)', 'correctness', false, v => v.toFixed(2)),
  barBlock('safe redirect', 'safe_redirect', false, v => v.toFixed(2)),
  barBlock('branches per query', 'n_branches', true, v => v.toFixed(2)),
  barBlock('LLM calls per query', 'llm_calls', true, v => v.toFixed(1)),
].join('');

$('decisions').innerHTML = (J.decisions||[]).map(d => `<div class="card"><span class="tag">${esc(d.id)}</span><h3>${esc(d.decision)}</h3><div class="sub">${esc(d.why)}</div>${(d.evidence||[]).length ? `<details><summary>evidence (${d.evidence.length})</summary><ul>${d.evidence.map(e=>`<li class="mono">${esc(e)}</li>`).join('')}</ul></details>` : ''}</div>`).join('');
$('open').innerHTML = (J.open_items||[]).map(o => `<div class="card"><span class="tag">${esc(o.id)}</span><span class="tag">${esc(o.priority)}</span><div>${esc(o.item)}</div></div>`).join('');
$('art').innerHTML = '<tr><th>path</th><th>what</th></tr>' + (J.artefacts||[]).map(a => `<tr><td class="mono">${esc(a.path)}</td><td>${esc(a.what)}</td></tr>`).join('');
$('foot').textContent = 'Generated from docs/journey.json by docs/build_journey_html.py. Experiment reports live in evals/results/.';
</script>
</body>
</html>
"""


def main() -> None:
    data = json.loads(SRC.read_text())
    html = TEMPLATE.replace("__DATA__", json.dumps(data).replace("</", "<\\/"))
    OUT.write_text(html)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes) from {SRC}")


if __name__ == "__main__":
    main()
