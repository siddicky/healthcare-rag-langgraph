"""Build the multi-page artifact: original submission + original findings (each in an
isolated srcdoc iframe so neither stylesheet touches the other), the architecture page,
the live coach embed, and two launch pages for hosts that refuse to be framed.
Text updates from the repo fact-check are applied via replacements.json (exact-string,
must match exactly once)."""
import html, json, sys, pathlib

HERE = pathlib.Path(__file__).parent
SRC = HERE / "src"; IMG = HERE / "img"
import base64
def data_uri(name):
    return "data:image/jpeg;base64," + base64.b64encode((IMG / name).read_bytes()).decode()
sub_src = (SRC / "submission-main.dc.html").read_text()
fin_src = (SRC / "findings-body.html").read_text()

# ---- submission: unwrap the design-canvas artboard into a plain document ----
head = sub_src[sub_src.find("<helmet>") + len("<helmet>"):sub_src.find("</helmet>")]
body = sub_src[sub_src.find("</helmet>") + len("</helmet>"):sub_src.rfind("</x-dc>")]
submission_doc = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>nymble Take-Home Submission</title>" + head +
    "<style>html{scrollbar-gutter:stable}</style></head><body>" + body + "</body></html>"
)

# ---- findings: already a flat body (title + style + content + print script) ----
fin = fin_src.replace("<title>Nymble healthcare RAG findings (Copy)</title>",
                      "<title>Nymble healthcare RAG findings</title>")
findings_doc = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"></head><body>"
    + fin + "</body></html>"
)

REPL = json.loads((SRC / "replacements.json").read_text()) if (SRC / "replacements.json").exists() else {}

def apply(doc, pairs, label):
    for pair in pairs:
        old, new = pair[0], pair[1]
        allow_many = len(pair) > 2 and pair[2] == "all"
        n = doc.count(old)
        if n == 0 or (n > 1 and not allow_many):
            print(f"[{label}] expected {'>=1' if allow_many else '1'} match, got {n}: {old[:90]!r}", file=sys.stderr)
            sys.exit(1)
        doc = doc.replace(old, new)
        print(f"[{label}] {n}× {old[:60]!r}")
    return doc

submission_doc = apply(submission_doc, REPL.get("submission", []), "submission")
findings_doc = apply(findings_doc, REPL.get("findings", []), "findings")

# ---- findings: swap the two Studio graph figures for the 2026-08-26 captures ----
import re
_figs = re.findall(r'<img src="data:image/png;base64,[^"]+"', findings_doc)
assert len(_figs) == 2, len(_figs)
findings_doc = findings_doc.replace(_figs[0], '<img src="%s"' % data_uri("graph-rag.jpg"), 1)
findings_doc = findings_doc.replace(_figs[1], '<img src="%s"' % data_uri("graph-coach.jpg"), 1)
architecture_doc = (SRC / "architecture.html").read_text()
access_doc = (SRC / "access.html").read_text()
wiki_doc = (HERE / "wiki.html").read_text()

def srcdoc(d):
    return html.escape(d, quote=True)

# ---- page list ---------------------------------------------------------------
LS_ORG = "6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6"
PAGES = [
    {"id": "submission",   "label": "Submission",   "kind": "doc",   "doc": submission_doc},
    {"id": "findings",     "label": "Findings",     "kind": "doc",   "doc": findings_doc},
    {"id": "architecture", "label": "Architecture", "kind": "doc",   "doc": architecture_doc},
    {"id": "access",       "label": "Access",       "kind": "doc",   "doc": access_doc},
    {"id": "chat",         "label": "Live coach",   "kind": "launch",
     "url": "https://www.nymble.site/chat",
     "title": "Nymble AI Coach — live member app",
     "blurb": "nymble.site/chat · Next.js on Vercel → hc-rag-server-prod",
     "why": "The Artifact sandbox's Content-Security-Policy has no frame-src allowance, so no external site can render inside this page — nymble.site itself sends no frame restrictions and embeds fine elsewhere. The button opens the production app in a new tab; below is the signed-in coach as captured on 2026-08-26.",
     "preview": data_uri("coach-preview.jpg"),
     "preview_alt": "The Nymble Coach chat screen: 'Talking with Nymble AI Coach', six quick-action prompts (log weight, log injection, calendar, move check-in, upload intake form, weigh-in reminder) and the message composer.",
     "facts": [
        ("Sign-in", "Supabase email + password. The JWT is the only credential the browser holds; every thread, run and cron is filtered by it server-side."),
        ("Medical answers", "Only via the coach's medical_lookup tool, relayed verbatim from the RAG graph (safety gate first). The model never paraphrases a drug answer."),
        ("Cards in the transcript", "Schedule change, memory review, document upload and reminders each gate a real write behind an explicit confirm/decline; the resolved state persists inline."),
        ("Transport", "CopilotKit v2 headless: /api/copilotkit on Vercel proxies AG-UI/SSE to LANGGRAPH_DEPLOYMENT_URL — the member never talks to Fly directly."),
     ]},
    {"id": "traces",       "label": "LangSmith",    "kind": "launch",
     "url": f"https://smith.langchain.com/o/{LS_ORG}/projects",
     "title": "LangSmith traces",
     "blurb": f"smith.langchain.com · org {LS_ORG[:8]}… · project healthcare-rag",
     "why": "LangSmith sets Content-Security-Policy: frame-ancestors 'self', so traces can't be framed here. The button opens the workspace's projects list in a new tab.",
     "facts": [
        ("Tracing project", "LANGSMITH_PROJECT=healthcare-rag — every graph run (safety_gate → … → finalize) lands here when tracing is on."),
        ("Feedback project", f"LANGSMITH_FEEDBACK_PROJECT_ID=888422ad… — thumbs-up/down from the coach action bar arrives via the server proxy. Open: smith.langchain.com/o/{LS_ORG[:8]}…/projects/p/888422ad-5acf-47d7-8b61-cf6084305b63"),
        ("Posture", "Opt-in and off by default in production (LANGSMITH_TRACING secret). Studio is allowed in — CORS includes smith.langchain.com and the perimeter passes StudioUser principals through."),
        ("Eval runs", "Every row in evals/results/*.json carries a LangSmith run URL; graph-final and the retrieval-gate runs recorded zero root runs because the account was over its monthly trace quota — their local rows are authoritative."),
     ]},
    {"id": "metrics",      "label": "Fly metrics",  "kind": "launch",
     "url": "https://fly-metrics.net/d/fly-app/fly-app?orgId=205513&from=1787734413408&to=1787738013408",
     "title": "Fly.io app metrics — Grafana",
     "blurb": "fly-metrics.net · org 205513 · dashboard fly-app · 1-hour window",
     "why": "Grafana on fly-metrics.net answers with X-Frame-Options: deny and redirects to its OAuth login, so it can't render inside this page. The button opens it in a new tab with your Fly session.",
     "facts": [
        ("Apps on the dashboard", "hc-rag-server-prod (2× shared-1x, 2 GB) · hc-rag-weaviate-prod (1×, 256 MB) · hc-rag-server-prod-db (PG 17 + pgvector, 10 GB)"),
        ("What to look at", "HTTP response codes and p50/p95 on hc-rag-server-prod; memory on the Weaviate machine (256 MB is the tightest budget in the system); disk on pg_data."),
        ("Health signal", "GET /ok every 15 s from Fly's checker — 503 until the graph is ready, 200 after. 1/1 passing on both prod apps at capture time."),
        ("Time window", "Captured range in the link: 2026-08-26 08:40Z → 09:40Z (the hour after release v25). Adjust the picker for the deploy timeline."),
     ]},
    {"id": "wiki",         "label": "OpenWiki",     "kind": "doc",   "doc": wiki_doc},
]

def launch_pane(p, embed):
    facts = "".join(
        f"<div class='fact'><div class='k'>{html.escape(k)}</div><div class='v'>{html.escape(v)}</div></div>"
        for k, v in p.get("facts", [])
    )
    frame = (
        f"<div class='chatframe'><iframe id='{p['id']}-frame' title='{html.escape(p['title'])}' data-src='{html.escape(p['url'])}' "
        f"allow='clipboard-write' referrerpolicy='no-referrer-when-downgrade'></iframe>"
        f"<div class='chatfallback' id='{p['id']}-fallback' hidden><p><b>The page didn't load inside this tab.</b> "
        f"The host's frame policy or the sandbox blocked the embed — the app itself is unaffected.</p>"
        f"<a class='open' href='{html.escape(p['url'])}' target='_blank' rel='noopener'>Open ↗</a></div></div>"
        if embed else
        f"<div class='launch'><div class='launch-card'>"
        f"<div class='eyebrow'>External · opens in a new tab</div>"
        f"<h2>{html.escape(p['title'])}</h2>"
        f"<p class='why'>{html.escape(p['why'])}</p>"
        f"<a class='open big' href='{html.escape(p['url'])}' target='_blank' rel='noopener'>Open {html.escape(p['label'])} ↗</a>"
        f"<code class='url'>{html.escape(p['url'])}</code>"
        + (f"<a href='{html.escape(p['url'])}' target='_blank' rel='noopener' class='preview'><img src='{p['preview']}' alt='{html.escape(p['preview_alt'])}'></a>" if p.get("preview") else "")
        + f"<div class='facts'>{facts}</div>"
        f"</div></div>"
    )
    note = f"<div class='chatnote'>{html.escape(p['note'])}</div>" if p.get("note") else ""
    return (
        f"<div id='pane-{p['id']}' role='tabpanel' aria-labelledby='tab-{p['id']}' class='chatpane' hidden>"
        f"<div class='chatbar'><span><b>{html.escape(p['title'])}</b> · {html.escape(p['blurb'])}</span>"
        f"<a class='open' href='{html.escape(p['url'])}' target='_blank' rel='noopener'>Open in a new tab ↗</a></div>"
        f"{note}{frame}</div>"
    )

tabs_html = "".join(
    f"<button role=\"tab\" id=\"tab-{p['id']}\" aria-controls=\"pane-{p['id']}\" aria-selected=\"{'true' if i == 0 else 'false'}\">{html.escape(p['label'])} <span>{i+1}</span></button>"
    for i, p in enumerate(PAGES)
)
panes_html = ""
for i, p in enumerate(PAGES):
    if p["kind"] == "doc":
        hidden = "" if i == 0 else " hidden loading=\"lazy\""
        panes_html += (f"<iframe id=\"pane-{p['id']}\" role=\"tabpanel\" aria-labelledby=\"tab-{p['id']}\" "
                       f"title=\"{html.escape(p['label'])}\" srcdoc=\"{srcdoc(p['doc'])}\"{hidden}></iframe>")
    else:
        panes_html += launch_pane(p, embed=(p["kind"] == "embed"))

page_ids = json.dumps([p["id"] for p in PAGES])
embed_ids = json.dumps([p["id"] for p in PAGES if p["kind"] == "embed"])
banner = REPL.get("banner", "")

shell = f"""<meta charset="utf-8"><title>nymble Take-Home Record</title>
<style>
  :root {{
    --rust:#631300; --carrot:#ea492a; --carrot-acc:#c2390f; --gold:#eda94f; --camel:#9e4d14;
    --birch:#faf3e3; --white:#fff; --warm-white:#fdf8f0; --rust-10:rgba(99,19,0,.1); --rust-16:rgba(99,19,0,.16); --gold-20:rgba(237,169,79,.2);
    --font-head:'Quicksand','Georgia',serif; --font-body:'Nunito Sans','SF Pro Text',-apple-system,sans-serif;
    --font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    color-scheme: light;
  }}
  html, body {{ height:100%; margin:0; background:var(--birch); color:var(--rust); font-family:var(--font-body); }}
  body {{ display:flex; flex-direction:column; overflow:hidden; }}
  .bar {{ flex:0 0 auto; position:relative; display:flex; align-items:center; justify-content:space-between; gap:16px;
         padding:10px 20px; background:var(--birch); border-bottom:1px solid var(--rust-10); }}
  .bar::before {{ content:""; position:absolute; left:0; right:0; top:0; height:4px;
         background:linear-gradient(90deg,var(--carrot) 0 62%,var(--gold) 62% 100%); }}
  .brand {{ font-family:var(--font-head); font-weight:700; font-size:15px; white-space:nowrap; }}
  .brand small {{ font-family:var(--font-body); font-weight:600; font-size:11px; letter-spacing:.1em; text-transform:uppercase; color:var(--camel); margin-left:10px; }}
  .tabs {{ display:flex; flex-wrap:wrap; justify-content:center; gap:2px; background:var(--white); border:1px solid var(--rust-10); border-radius:100px; padding:3px; overflow:visible; }}
  .tabs::-webkit-scrollbar {{ display:none; }}
  .tabs button {{ font:600 13.5px var(--font-body); color:var(--camel); background:transparent; border:0; cursor:pointer;
         padding:8px 15px; border-radius:100px; white-space:nowrap; }}
  .tabs button[aria-selected="true"] {{ background:var(--carrot); color:var(--white); }}
  .tabs button:focus-visible {{ outline:3px solid var(--gold); outline-offset:2px; }}
  .tabs button span {{ opacity:.7; font-weight:400; margin-left:5px; }}
  .note {{ font-size:12px; color:var(--camel); white-space:nowrap; }}
  .panes {{ flex:1 1 auto; position:relative; min-height:0; }}
  .panes > iframe {{ position:absolute; inset:0; width:100%; height:100%; border:0; background:var(--birch); }}
  .panes > iframe[hidden] {{ display:none; }}
  .chatpane {{ position:absolute; inset:0; display:flex; flex-direction:column; }}
  .chatpane[hidden] {{ display:none; }}
  .chatbar {{ flex:0 0 auto; display:flex; align-items:center; justify-content:space-between; gap:12px; padding:8px 20px;
             font-size:13px; color:var(--camel); background:var(--white); border-bottom:1px solid var(--rust-10); }}
  .chatbar b {{ color:var(--rust); }}
  .chatnote {{ flex:0 0 auto; font-size:12.5px; color:var(--camel); padding:6px 20px; background:var(--warm-white); border-bottom:1px solid var(--rust-10); }}
  .open {{ color:var(--white); background:var(--carrot); text-decoration:none; font-weight:700; font-size:13px; padding:7px 14px; border-radius:100px; white-space:nowrap; }}
  .open.big {{ font-size:16px; padding:12px 24px; display:inline-block; margin:8px 0 14px; }}
  .chatframe {{ flex:1 1 auto; position:relative; min-height:0; }}
  .chatframe iframe {{ position:absolute; inset:0; width:100%; height:100%; border:0; background:var(--birch); }}
  .chatfallback {{ position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:14px;
                  text-align:center; padding:32px; background:var(--birch); color:var(--camel); font-size:15px; }}
  .chatfallback[hidden] {{ display:none; }}
  .chatfallback b {{ color:var(--rust); }}
  .launch {{ flex:1 1 auto; overflow:auto; padding:40px 24px; }}
  .launch-card {{ max-width:820px; margin:0 auto; background:var(--white); border:1px solid var(--rust-10); border-radius:20px; padding:36px 40px; box-shadow:0 4px 24px rgba(99,19,0,.08); }}
  .launch .eyebrow {{ font-size:11px; font-weight:700; letter-spacing:.12em; text-transform:uppercase; color:var(--camel); margin-bottom:10px; }}
  .launch h2 {{ font-family:var(--font-head); font-size:30px; margin:0 0 12px; letter-spacing:-.02em; }}
  .launch .why {{ margin:0 0 6px; color:var(--camel); font-size:15px; line-height:1.55; max-width:64ch; }}
  .launch .url {{ display:block; font-family:var(--font-mono); font-size:12px; color:var(--carrot-acc); word-break:break-all; margin-bottom:24px; }}
  .preview {{ display:block; margin:0 0 24px; border:1px solid var(--rust-16); border-radius:16px; overflow:hidden; box-shadow:0 8px 32px rgba(99,19,0,.12); }}
  .preview img {{ display:block; width:100%; height:auto; }}
  .facts {{ display:grid; grid-template-columns:1fr 1fr; gap:16px 24px; border-top:1px solid var(--rust-10); padding-top:22px; }}
  .fact .k {{ font-size:11px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--camel); margin-bottom:4px; }}
  .fact .v {{ font-size:14px; line-height:1.5; }}
  @media (max-width:1600px) {{ .note {{ display:none; }} }}
  @media (max-width:720px) {{ .note, .brand small {{ display:none; }} .bar {{ padding:10px 12px; }} .tabs button {{ padding:8px 11px; font-size:12.5px; }} .facts {{ grid-template-columns:1fr; }} .launch-card {{ padding:26px 22px; }} }}
</style>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@400;600;700&family=Quicksand:wght@700&display=swap">

<header class="bar">
  <div class="brand">healthcare-rag <small>nymble health · take-home</small></div>
  <div class="tabs" role="tablist" aria-label="Pages">{tabs_html}</div>
  <div class="note">{html.escape(banner)}</div>
</header>
<div class="panes">{panes_html}</div>
<script>
(function(){{
  var PAGES={page_ids}, EMBEDS={embed_ids};
  var tabs=[].slice.call(document.querySelectorAll('[role=tab]'));
  var loaded={{}};
  function loadEmbed(id){{
    if(loaded[id]) return; loaded[id]=true;
    var f=document.getElementById(id+'-frame'), fb=document.getElementById(id+'-fallback');
    if(!f) return;
    var settled=false;
    f.addEventListener('load',function(){{ settled=true; }});
    f.addEventListener('error',function(){{ settled=true; fb.hidden=false; }});
    f.src=f.getAttribute('data-src');
    setTimeout(function(){{ if(!settled) fb.hidden=false; }},12000);
  }}
  function show(id){{
    if(PAGES.indexOf(id)<0) id=PAGES[0];
    tabs.forEach(function(t){{ t.setAttribute('aria-selected', t.id==='tab-'+id ? 'true':'false'); }});
    PAGES.forEach(function(k){{ document.getElementById('pane-'+k).hidden=(k!==id); }});
    if(EMBEDS.indexOf(id)>=0) loadEmbed(id);
    try{{ history.replaceState(null,'','#'+id); }}catch(e){{}}
    try{{ localStorage.setItem('hc-rag-page',id); }}catch(e){{}}
  }}
  tabs.forEach(function(t){{ t.addEventListener('click',function(){{ show(t.id.replace('tab-','')); }}); }});
  document.addEventListener('keydown',function(e){{
    if(e.target.getAttribute('role')!=='tab') return;
    if(e.key==='ArrowRight'||e.key==='ArrowLeft'){{ var i=tabs.indexOf(e.target); var n=tabs[(i+(e.key==='ArrowRight'?1:tabs.length-1))%tabs.length]; n.focus(); n.click(); }}
  }});
  var want=(location.hash||'').replace('#','');
  if(PAGES.indexOf(want)<0){{ try{{ want=localStorage.getItem('hc-rag-page')||''; }}catch(e){{ want=''; }} }}
  if(PAGES.indexOf(want)>0) show(want);
}})();
</script>
"""
out = HERE / "nymble-take-home-record.html"
out.write_text(shell)
print("wrote", out, f"{out.stat().st_size/1e6:.2f} MB", "pages:", [p["id"] for p in PAGES])
