# Take-home record artifact

Source and build for the eight-tab submission record published at
https://claude.ai/code/artifact/c3176b99-18fc-4e7d-8e20-de1365613c03.
The built file is `nymble-take-home-record.html`, a single self-contained
page (Google Fonts are the only external request) that opens from disk.

Tabs: Submission, Findings, Architecture, Access, Live coach, Fly metrics,
LangSmith, OpenWiki.

## Layout

| path | what |
|---|---|
| `src/submission-main.dc.html` | the original submission page, extracted from the Claude Design canvas export, unmodified |
| `src/findings-body.html` | the original findings page body, unmodified |
| `src/replacements.json` | exact-match string edits applied to both originals at build time (re-validated numbers, links, swapped figures). A pair with a third element `"all"` may match more than once; anything else must match exactly once or the build stops |
| `src/architecture.html` | Architecture tab, hand-authored from `flyctl` output and the deploy config, 2026-08-26 |
| `src/access.html` | Access tab, vendor-tier screenshot evidence (embedded, identifiers redacted) |
| `img/` | screenshots embedded as data URIs at build time |
| `cache/mermaid-src.json` | the 27 mermaid blocks harvested from `openwiki/` |
| `cache/mermaid-svg.json` | those blocks rendered to SVG with the Nymble theme (see below) |
| `build_wiki.py` | renders `../../openwiki/**.md` into the OpenWiki tab (`wiki.html`, intermediate, not committed) |
| `build_combined.py` | applies replacements, wraps each page in a srcdoc frame, emits the tab shell and the final HTML |
| `build_docx.py` | renders `docs/writeup.md` to a `.docx` with the Nymble styles mapped to Title / Heading 1-3 for Google Docs |
| `mermaid-render.html`, `sink.py` | the mermaid render step: a page that loads mermaid 11 from CDN, renders every block with Nunito Sans loaded, and POSTs the SVGs to the sink |

## Rebuild

```
cd artifacts/take-home-record
uv run --with markdown --with pyyaml python build_wiki.py
uv run python build_combined.py
```

To refresh the diagrams after `openwiki/` changes:

```
python3 -m http.server 8765 &      # serves this directory
python3 sink.py &                  # writes cache/mermaid-svg.json
# open http://127.0.0.1:8765/mermaid-render.html in a browser; it POSTs when done
```

Then rebuild. The artifact CSP blocks CDN scripts, which is why rendering
happens here and not in the page.

To render the write-up for Google Docs:

```
uv run --with python-docx python build_docx.py ../../docs/writeup.md out.docx
```

## Publishing

Republish the built HTML to the same artifact URL from Claude Code
(`Artifact` tool, same file path keeps the URL). Pages 5-7 are launch cards
because the artifact sandbox refuses external frames; hosted anywhere else,
`chat` could return to `kind: "embed"` in `PAGES` (nymble.site sends no frame
restrictions; fly-metrics.net and smith.langchain.com do).
