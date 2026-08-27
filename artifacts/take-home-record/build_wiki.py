import json, re, html, subprocess
from pathlib import Path
import markdown, yaml
ROOT=Path(__file__).resolve().parents[2]/'openwiki'
HERE=Path(__file__).parent
commit=subprocess.run(['git','log','-1','--format=%h %ad','--date=short','--','openwiki'],cwd=ROOT.parent,capture_output=True,text=True).stdout.strip()
pages={}
for p in sorted(ROOT.rglob('*.md')):
    rel=p.relative_to(ROOT).as_posix()
    src=p.read_text()
    meta={}
    m=re.match(r'^---\n(.*?)\n---\n',src,re.S)
    if m:
        try: meta=yaml.safe_load(m.group(1)) or {}
        except Exception: meta={}
        src=src[m.end():]
    src=re.sub(r'<!--.*?-->','',src,flags=re.S)
    _svgs=json.loads((HERE/'cache'/'mermaid-svg.json').read_text()) if (HERE/'cache'/'mermaid-svg.json').exists() else {}
    _n=[0]
    def _mm(mm):
        k=f'{rel}#{_n[0]}'; _n[0]+=1
        svg=_svgs.get(k)
        if svg and not svg.startswith('ERR'):
            return '<figure class="mermaid-fig">'+svg+'</figure>'
        return '<pre class="mermaid-src"><b>mermaid</b>\n'+html.escape(mm.group(1))+'</pre>'
    src=re.sub(r'```mermaid\n(.*?)```',_mm,src,flags=re.S)
    body=markdown.markdown(src,extensions=['tables','fenced_code','toc','sane_lists'])
    # rewire relative .md links
    def fix(mm):
        href=mm.group(1)
        if href.startswith(('http','#','mailto')): return mm.group(0)
        tgt=(p.parent/href.split('#')[0]).resolve()
        if tgt.suffix=='' and (tgt/'index.md').exists(): tgt=tgt/'index.md'
        try: t=tgt.relative_to(ROOT).as_posix()
        except ValueError: return f'href="#" data-ext="{html.escape(href)}"'
        if t in pages or (ROOT/t).exists(): return f'href="#w/{t}" data-wiki="{t}"'
        return f'href="#" data-src="{html.escape(t)}"'
    body=re.sub(r'href="([^"]+)"',fix,body)
    title=('Index' if p.name=='index.md' else None) or meta.get('title') or (re.search(r'^# (.+)$',src,re.M) or [None,rel])[1]
    pages[rel]={'title':title,'desc':meta.get('description',''),'type':meta.get('type',''),'html':body,'src':meta.get('openwiki',{}).get('source_paths',[]) if isinstance(meta.get('openwiki'),dict) else []}
arch=(HERE/'src'/'architecture.html').read_text()
head=arch.split('</style></head><body>')[0].replace('<title>System architecture</title>','<title>OpenWiki</title>')
head+="""
  .wiki{display:grid;grid-template-columns:270px minmax(0,1fr);gap:28px;margin-top:22px}
  .side{position:sticky;top:12px;align-self:start;max-height:calc(100vh - 24px);overflow:auto;background:var(--white);border:1px solid var(--rust-10);border-radius:16px;padding:14px 10px}
  .side .grp{font-size:10.5px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:var(--camel);margin:12px 8px 4px}
  .side a{display:block;padding:5px 8px;border-radius:8px;font-size:13.5px;color:var(--rust);text-decoration:none;line-height:1.3}
  .side a:hover{background:var(--birch)} .side a.on{background:var(--carrot);color:#fff}
  .art{background:var(--white);border:1px solid var(--rust-10);border-radius:20px;padding:30px 36px;box-shadow:var(--shadow-sm);min-width:0}
  .art h1{font-size:30px;margin:0 0 6px} .art h2{font-size:21px;margin:28px 0 8px;padding-top:14px;border-top:1px solid var(--rust-10)} .art h3{font-size:16px;margin:20px 0 6px}
  .art p,.art li{font-size:15px;line-height:1.65} .art ul,.art ol{padding-left:22px}
  .art table{border-collapse:collapse;width:100%;font-size:13.5px;margin:12px 0;display:block;overflow-x:auto}
  .art th,.art td{border:1px solid var(--rust-10);padding:6px 10px;text-align:left;vertical-align:top} .art th{background:var(--birch)}
  .art pre{background:#fff7e8;border:1px solid var(--rust-10);border-radius:12px;padding:12px 14px;overflow-x:auto;font-size:12.5px;line-height:1.5}
  .art pre.mermaid-src b{display:block;color:var(--camel);font-size:10.5px;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:6px}
  .art code{font-size:.92em} .art a{color:var(--carrot-accessible)}
  .art .mermaid-fig{margin:14px 0;padding:14px;background:#fff7e8;border:1px solid var(--rust-10);border-radius:12px;overflow-x:auto}
  .art .mermaid-fig svg{max-width:100%;height:auto;display:block;margin:0 auto;overflow:visible}\n  .art .mermaid-fig foreignObject,.art .mermaid-fig foreignObject div{overflow:visible!important;white-space:nowrap}
  .art .meta{font-size:12.5px;color:var(--camel);margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid var(--rust-10)}
  .art .meta code{font-size:11.5px}
  .crumb{font-family:var(--font-mono,monospace);font-size:12px;color:var(--camel);margin-bottom:8px}
  @media(max-width:900px){.wiki{grid-template-columns:1fr}.side{position:static;max-height:none}}
</style></head><body>"""
groups={}
for k in pages: groups.setdefault(k.split('/')[0] if '/' in k else '',[]).append(k)
order=['']+sorted(g for g in groups if g)
side=''
for g in order:
    side+=f'<div class="grp">{html.escape(g or "root")}</div>'
    keys=sorted(groups[g],key=lambda k:(not k.endswith('index.md'),not k.endswith('AGENTS.md'),k))
    for k in keys:
        side+=f'<a href="#w/{k}" data-wiki="{k}">{html.escape(pages[k]["title"])}</a>'
body=f"""
<div class="topbar"></div>
<div class="page" style="max-width:1400px">
  <div class="masthead">
    <div class="brandmark"><span class="dot1"></span><span class="dot2"></span><span class="eyebrow">nymble health &nbsp;&middot;&nbsp; healthcare-rag &middot; openwiki</span></div>
    <div class="eyebrow">repo wiki</div>
  </div>
  <section class="hero" style="padding-top:36px;padding-bottom:8px">
    <div class="pill-tag">Generated by <b>OpenWiki</b> &nbsp;&middot;&nbsp; {len(pages)} pages &nbsp;&middot;&nbsp; last wiki commit <b>{html.escape(commit)}</b> &nbsp;&middot;&nbsp; refreshed by <code>.github/workflows/openwiki-update.yml</code></div>
    <h1 style="font-size:34px">The repository's own evidence index, readable here.</h1>
    <p class="sub">Every page below is <code>openwiki/**.md</code> from <code>main</code>, rendered at build time. Links between pages work in place; links to source files name the path. Source code and tests remain authoritative &mdash; a page's unknowns are verification gaps, not requirements.</p>
  </section>
  <div class="wiki">
    <nav class="side">{side}</nav>
    <article class="art" id="art"></article>
  </div>
</div>
<script>
var W={json.dumps(pages)};
function esc(s){{return s.replace(/[&<>"]/g,function(c){{return{{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]}})}}
function open(k){{
  if(!W[k]) k='index.md';
  var p=W[k], a=document.getElementById('art');
  var src=p.src&&p.src.length?'<div>source: '+p.src.map(function(s){{return '<code>'+esc(s)+'</code>'}}).join(' ')+'</div>':'';
  a.innerHTML='<div class="crumb">openwiki/'+esc(k)+'</div><div class="meta">'+(p.type?'<b>'+esc(p.type)+'</b> &middot; ':'')+esc(p.desc)+src+'</div>'+p.html;
  document.querySelectorAll('.side a').forEach(function(x){{x.classList.toggle('on',x.dataset.wiki===k)}});
  a.querySelectorAll('a[data-wiki]').forEach(function(x){{x.onclick=function(e){{e.preventDefault();open(x.dataset.wiki);window.scrollTo(0,0)}}}});
  a.querySelectorAll('a[data-src]').forEach(function(x){{x.title='repo file: '+x.dataset.src;x.onclick=function(e){{e.preventDefault()}}}});
  try{{localStorage.setItem('hc-wiki-page',k)}}catch(e){{}}
}}
document.querySelectorAll('.side a').forEach(function(x){{x.onclick=function(e){{e.preventDefault();open(x.dataset.wiki);document.getElementById('art').scrollIntoView({{block:'start'}})}}}});
var want='';try{{want=localStorage.getItem('hc-wiki-page')||''}}catch(e){{}}
open(want||'quickstart.md');
</script>
</body></html>"""
(HERE/'wiki.html').write_text(head+body)
print(len(pages),'pages', len(head+body)//1024,'KB')
