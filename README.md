# Surf

**Turn a link into a clean document.**

Surf takes a web page, a local file, or a search query, strips the clutter, optionally
translates it, and writes Markdown, HTML, PDF, audio, or a Pastebin post. There are two
front ends over the same pipeline: a command line for scripting, and a local web page for
interactive use.

```
input ─▶ fetch ─▶ extract ─▶ (OCR) ─▶ translate ─▶ render ─▶ save
```

- **One command per document.** `surf https://example.com` writes a Markdown note.
- **Static HTML to JavaScript-heavy pages.** Plain HTTP by default, a headless browser
  (Obscura, or Playwright) when a page needs one.
- **Real translation.** Detects the language, translates with an OpenAI Chat Completions,
  OpenAI Responses, or Anthropic Messages LLM, and records what happened in the
  front matter.
- **Five outputs from one pipeline.** Markdown, HTML, PDF, audio, Pastebin — including a
  browser-less PDF chain.
- **Site-aware.** Twitter/X, Reddit, Bluesky, Weibo, Threads, V2EX, WeChat, Zhihu,
  Xiaohongshu, NCPSSD, Pocket Casts, Xiaoyuzhou, GitHub, Wikipedia, Foreign Affairs.
- **Failures are not lost.** A URL that cannot be fetched is written to disk so you can
  retry it later.

---

## Install

Surf is distributed as a Python package and is easiest to run through
[uv](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/dodorz/surf.git && cd surf
uv sync
```

That installs the core dependencies, including the OCR engines (PaddleOCR, RapidOCR,
pytesseract — the Tesseract binary itself is a system package).

Surf needs a CPython where every binary dependency publishes wheels, so the
interpreter is pinned to **3.13** in `.python-version` (the newest version covered by
PaddlePaddle, ONNX Runtime and RapidOCR on Linux, macOS and Windows). `uv sync` and
`uv run` download that interpreter automatically when it is missing — you do not need
to install Python yourself, and `uv run surf ...` keeps working on every platform. On
interpreters or CPUs where the PaddlePaddle runtime is unavailable, PaddleOCR is
skipped automatically and the OCR chain falls back to RapidOCR and Tesseract.

Optional extras, only if you need them:

| Extra | Adds | Install |
| --- | --- | --- |
| `browser` | Playwright: interactive login, special-site page automation, NCPSSD full-text PDF, the Playwright PDF engine | `uv sync --extra browser && uv run playwright install chromium` |
| `pdf` | WeasyPrint: browser-less PDF rendering | `uv sync --extra pdf` |
| `transcribe` | Local podcast transcription via transcribe.cpp | `uv sync --extra transcribe` |
| `curlcffi` | Browser-like TLS fingerprint to get past some Cloudflare blocks | `uv sync --extra curlcffi` |

Surf's default headless backend is **Obscura**, a separate binary. Install it from the
[Obscura releases](https://github.com/h4ckf0r0day/obscura/releases) and make sure it is on
`PATH` (or set `[Browser] obscura_executable`). Surf starts and reuses a managed
`obscura serve` process over the Chrome DevTools Protocol, so no Playwright install is
needed for ordinary dynamic pages.

Then create a configuration file and add at least one LLM provider:

```bash
cp config.ini.example config.ini
```

Surf looks for `config.ini` next to the script first, then
`$XDG_CONFIG_HOME/surf/config.ini` (default `~/.config/surf/config.ini`).

---

## Quick start

```bash
uv run surf "https://example.com"                  # Markdown, translated to Chinese
uv run surf "https://example.com" -r               # keep the original language
uv run surf "https://example.com" -b               # bilingual
uv run surf "https://example.com" -h               # HTML
uv run surf "https://example.com" -p               # PDF
uv run surf "https://example.com" -a               # text-to-speech audio
uv run surf "https://example.com" -P               # publish to Pastebin
uv run surf "https://example.com" -o ~/note.md     # explicit output path
uv run surf "https://example.com" -O               # print to stdout
```

Short flags can be combined: `surf -hrn URL` is `-h -r -n` (HTML, original language, no
proxy).

The web UI runs the same pipeline:

```bash
uv run python surf_web.py                # http://127.0.0.1:18473
```

---

## Inputs

Surf accepts one positional argument. What it means depends on the input itself.

### A URL

```bash
uv run surf "https://example.com/article"
```

If the argument is text that contains a URL — a Xiaohongshu share message, for instance —
the first `http(s)` URL is extracted. Common short links (`t.co`, `bit.ly`,
`tinyurl.com`, `xhslink.com`, `xhslink.cn`, `pca.st/episode/...`) are resolved to their
final URL before site rules run, and the resolved URL is what lands in the front matter.

The CLI handles one URL per invocation; use a shell loop or `-S/--search` for batches. The
Web UI does accept several URLs at once and renders one card per URL.

Text with no URL at all is only meaningful in the Web UI, which saves it as a post with
the first sentence as the title. The CLI requires a URL, a local path, or `-S` keywords.

### A local file

Local HTML, Markdown, text, and PDF files go through the same pipeline as web pages.

```bash
uv run surf /tmp/article.html          # OS path
uv run surf ~/docs/readme.md           # translated like any other document
uv run surf file:///tmp/article.html   # file:// URI
uv run surf "C:\Temp\notes.txt"        # Windows path
```

Recognised types: `.html`, `.htm`, `.md`, `.txt`, `.rst`, `.adoc`, `.pdf`. Anything else is
read as plain text. On Windows, Unix-style paths in arguments and config values are
accepted and normalised (`~/Note/x.md` resolves under `%USERPROFILE%`).

### A PDF

When the input resolves to a PDF — by URL, by `file://`, or by local path — the output
format decides what happens:

```bash
uv run surf "https://example.com/paper.pdf"        # extract text → Markdown
uv run surf "https://example.com/paper.pdf" -h     # extract text → HTML
uv run surf "https://example.com/paper.pdf" -p     # keep the original PDF, byte for byte
```

Detection uses `Content-Type`, the response body, and `Content-Disposition`, so the URL
does not have to end in `.pdf`. Text extraction uses `pypdf`; scanned PDFs are not OCR'd,
so use `-p` to keep the source. If the PDF host is dead, Surf asks the Wayback Machine for
a snapshot and uses the archived raw PDF, recording it in the front matter `archive`
field.

### Keywords

With `-S/--search`, the argument is a query instead of a URL:

```bash
uv run surf -S "python asyncio tutorial"
```

Surf searches, prints a numbered "title + URL" list, and fetches only what you select. See
[Keyword search](#keyword-search).

### A social thread

```bash
uv run surf -t       "https://x.com/user/status/123"   # later posts (default range)
uv run surf --thread before "https://x.com/user/status/123"
uv run surf --thread both --thread-author same "https://x.com/user/status/123"
uv run surf --no-thread "https://bsky.app/profile/user.bsky.social/post/abc123"
```

Twitter/X, Bluesky, Weibo, and Threads expand threads by default (`after`, all authors).
V2EX saves only the main post unless `-t/--thread` is given, in which case replies are
included.

---

## Outputs

`-f/--format` picks the output type; the single-letter flags are shorthand for it.

| Format | Flag | Shorthand |
| --- | --- | --- |
| Markdown | `-f md` | default |
| HTML | `-f html` | `-h` |
| PDF | `-f pdf` | `-p` |
| Audio | `-f audio` | `-a` |
| Pastebin | `-f publish` | `-P` |

### Markdown

The default. Files are written to `[Output] md_dir` and named after the title, with a YAML
front matter block recording `title`, `source`, `created`, `description`, and — when
translation actually changed the text — `translator`.

```bash
uv run surf "https://example.com" --no-front-matter   # skip the front matter block
uv run surf "https://example.com" --archive           # also archive the source URL
```

Titles used as filenames keep CJK punctuation and valid symbols; only characters the
filesystem rejects are removed. `--archive` submits the final source URL to the Internet
Archive and writes the snapshot into the front matter; the default comes from
`[Output] archive`.

### HTML

```bash
uv run surf "https://example.com" -h
uv run surf "https://example.com" -h --html-inline     # self-contained file
```

`--html-inline` embeds external CSS/JS so the file works offline on its own (`[Output]
html_inline` sets the default).

### PDF

PDFs are rendered from the document's HTML, so a browser is not strictly required. The
`[Output] pdf_engine` chain is tried in order:

| Engine | What it uses | Needs |
| --- | --- | --- |
| `playwright` | Installed Chromium through Playwright | `--extra browser` |
| `chrome` | Local `google-chrome` / `chromium` / Edge with `--headless=new --print-to-pdf` | a Chrome binary |
| `weasyprint` | Pure-Python HTML/CSS renderer, downloads and embeds remote images | `--extra pdf`, system Pango |
| `typst` | Converts the HTML to Typst markup and runs `typst` | the `typst` binary |
| `pandoc` | Hands the HTML to `pandoc`, which drives typst/weasyprint/xelatex/… | `pandoc` + an engine |

```ini
[Output]
pdf_engine = auto          ; playwright, chrome, weasyprint, typst, pandoc
typst_executable = typst   ; env: SURF_TYPST_EXECUTABLE
typst_fonts =              ; extra font dirs, comma or ':' separated
pandoc_executable = pandoc ; env: SURF_PANDOC_EXECUTABLE
pandoc_pdf_engine =        ; empty = auto-detect
pandoc_args =              ; e.g. --toc -V CJKmainfont="Noto Serif CJK SC"
```

Pick or reorder engines with a comma-separated list, e.g. `pdf_engine = typst,weasyprint`.
Chrome is auto-detected, or set `[Browser] chrome_executable` / `SURF_CHROME_EXECUTABLE`.
CommonMark tables and fenced code blocks render correctly. CJK text works with any
installed CJK font.

### Audio and speech

```bash
uv run surf "https://example.com" -a                              # save MP3
uv run surf "https://example.com" -s                              # read it aloud
uv run surf "https://example.com" -a --tts-voice en-US-AriaNeural
uv run surf "https://example.com" -a --tts-rate +10% --tts-volume -20%
```

Speech comes from `edge-tts`; voice, rate, and volume default to the `[TTS]` section and
can be overridden per run. For Pocket Casts and Xiaoyuzhou episodes, `-a` downloads the
original episode audio instead (keeping the source extension, e.g. `.m4a`), bounded by
`[Transcription] max_audio_mb` and `audio_timeout`.

### Pastebin

```bash
uv run surf "https://example.com" -P
```

Publishing writes no local file and prints the paste URL. It needs a `[publish.pastebin]
api_dev_key`; `api_paste_expire_date` and `api_paste_private` are optional.

### Choosing where it goes

```bash
uv run surf "https://example.com" -o output.md      # explicit file
uv run surf "https://example.com" -o ~/Note/out.md  # ~ expands on Windows too
uv run surf "https://example.com" -o -              # stdout
uv run surf "https://example.com" -O                # same as -o -
uv run surf -p "https://example.com" -o out.pdf
```

`-o` overrides the configured directory. Without it, each format uses its own directory:
`md_dir`, `html_dir`, `pdf_dir`, `audio_dir`.

---

## Translation

```bash
uv run surf "https://example.com"          # -l trans (default)
uv run surf "https://example.com" -r       # -l raw, no translation
uv run surf "https://example.com" -b       # -l both, original + translation
uv run surf "https://example.com" --llm l2 # pick another configured provider
```

Translation runs only when the detected language differs from `[Output]
target_language`, and long documents are split into segments so they fit the model's
context. Front matter records `translator` only when the content or title actually
changed; language detection alone does not count.

`title` and `description` are flattened to a single line — line breaks, tabs,
non-breaking spaces and repeated whitespace collapse, and spaces between CJK characters
are dropped — so the YAML stays valid. A trailing sentence-ending period is removed from
`title`, and GitHub's auto-generated "Contribute to … development by creating an account
on GitHub." sentence is stripped before translation so translated output never carries a
translated copy of it.

### LLM API styles

Each `[LLM.<name>]` section talks to its provider with one of three wire formats,
selected by `api_style`:

| `api_style` | Endpoint | Notes |
| --- | --- | --- |
| `chat` (default) | `/chat/completions` | OpenAI Chat Completions style, works with every OpenAI-compatible gateway |
| `responses` | `/responses` | OpenAI Responses style, sent through the official SDK |
| `anthropic` | `/v1/messages` | Anthropic Messages style, sent with `requests` |

```ini
[LLM.chat_gateway]
api_style = chat
base_url = https://api.deepseek.com/v1
api_key = sk-...
model = deepseek-chat

[LLM.openai_responses]
api_style = responses
base_url = https://api.openai.com/v1
api_key = sk-...
model = gpt-4.1-mini

[LLM.claude]
api_style = anthropic
base_url = https://api.anthropic.com
api_key = sk-ant-...
model = claude-3-5-sonnet-latest
max_tokens = 8192
```

`base_url` for `anthropic` may be the host, the host plus `/v1`, or the full
`/v1/messages` URL. Auth defaults to the `x-api-key` header; set
`auth_header = bearer` (or `both`) for Anthropic-compatible gateways that expect
`Authorization: Bearer`. The API version defaults to `2023-06-01` and can be changed
with `anthropic_version`. `max_tokens` is optional (`max_tokens` for chat/anthropic,
`max_output_tokens` for responses; Anthropic defaults to 4096) and `timeout` only
applies to the Anthropic Messages request (default 120s).

---

## Fetching

### Proxy

```bash
uv run surf "https://example.com" -x env               # http_proxy / https_proxy / no_proxy
uv run surf "https://example.com" -x win               # Windows Internet Settings (Windows only)
uv run surf "https://example.com" -c http://127.0.0.1:7890
uv run surf "https://example.com" -x custom --set-proxy http://127.0.0.1:7890
uv run surf "https://example.com" -n                   # direct connection
```

Modes are `env`, `win`, `custom`, and `no`; `-c` is `-x custom` with an optional inline
URL, and `-n` is `-x no`. Without a flag the CLI resolves a proxy implicitly — reasonable
on a Windows desktop where many sites need a local proxy. If an implicit proxy fails with
a proxy/TLS handshake error, Surf retries once without it before falling back to a
browser. Explicit modes are never silently changed.

The [Web UI](#web-ui) starts at `no` instead, because it usually runs on a server where
public sites are directly reachable.

### Content extraction

```bash
uv run surf "https://example.com" -e trafilatura   # -T
uv run surf "https://example.com" -e readability   # -R
uv run surf "https://example.com" -e raw           # -U, keep the original HTML
```

`auto` (the default, `[Content] extractor`) tries Readability and falls back to
Trafilatura. Both remove ads, sidebars, and navigation. Content-like inline SVG diagrams
are converted to Markdown image references while decorative icons are dropped.

### Headless browser

```bash
uv run surf "https://example.com" --browser
uv run surf "https://example.com" -B playwright
uv run surf "https://example.com" -B obscura
uv run surf "https://example.com" -B auto          # follow [Browser] backend
```

Use `--browser` to force rendering through a headless browser instead of plain HTTP, and
`-B/--browser-backend` to choose which one. Obscura is the default and needs no Python
extra:

- `[Browser] obscura_fetch = auto` uses raw CDP and falls back to the Playwright client on
  protocol errors; `native` never falls back; `playwright` uses the legacy client.
- A configured CDP endpoint that is already alive is treated as external: Surf connects
  to it but does not start or stop it.
- Requests from different web-server threads coordinate access, and a managed Obscura
  service is safely replaced when the proxy changes.
- Bloomberg-family sites force Obscura stealth mode even when `obscura_stealth = false`,
  because the default headless profile cannot pass PerimeterX.
- Zhihu is the exception: its browser fallback always opens a visible Playwright window,
  because the `zse-ck` challenge does not resolve headlessly. That needs a graphical
  session on Linux.

If a headed browser is impossible, archive.today's CAPTCHA fallback now fails fast instead
of launching a browser that cannot work.

### Image OCR

```bash
uv run surf "https://example.com/article" --ocr
uv run surf "https://example.com/article" --ocr --ocr-engine tesseract --ocr-lang eng
uv run surf "https://www.xiaohongshu.com/explore/..." --no-ocr
uv run surf --ocr /path/to/image.png                 # OCR a local image directly
```

OCR appends recognised text below each article image. The default engine is `auto`
(PaddleOCR → RapidOCR → Tesseract); `--ocr-engine` forces one — with `rapidocr`, Tesseract
is still the fallback. `--ocr-lang` applies to Tesseract only. Xiaohongshu enables OCR by
default; every other site needs `--ocr` or `[OCR] enabled = true`. Xiaohongshu notes also
move their `#hashtags` into front matter `tags` (the in-body tag links are dropped) and turn
the footer's relative date (`3天前`, `2周前`, ...) into an absolute `created`. A failing image is
skipped rather than aborting the fetch, and engines run in a short-lived child process so
their memory is not retained by a web worker.

Tesseract hands over plain text and keeps the author's paragraphs: wrapped lines inside a
paragraph are joined back together and only the blank lines stay as paragraph breaks.
PaddleOCR and RapidOCR return one box per visual line, so the recognised text is regrouped
by detector geometry — a line that sits one normal line-height below the previous one, at
the same left margin, continues that paragraph; extra leading, an indented first line or a
different text size starts a new one. That keeps wrapped sentences whole and stops every
visual line from becoming a hard break.

---

## Podcasts and transcription

Pocket Casts and Xiaoyuzhou episode links are resolved to Show Notes, publication date,
duration, and the audio enclosure:

```bash
uv run surf "https://pca.st/episode/ef99d8d4-a16c-48c3-a844-9a88163450d3" -r
uv run surf "https://www.xiaoyuzhoufm.com/episode/69018660740634ca4785c3b8" -a
```

The front matter title is `Episode Title - Podcast Name` and the default filename adds a
`[播客]` prefix. Show Notes are emitted under `## Show Notes`; when a date is available it
becomes `created`. Podcast metadata labels stay untranslated even when the notes are
translated. If the episode page is blocked, the information in the share redirect is
preserved.

Local transcription needs the optional backend and a GGUF model:

```bash
uv sync --extra transcribe
uv run surf "https://pca.st/episode/..." -w
uv run surf "https://pca.st/episode/..." -w --transcript-timestamps segment
```

```ini
[Transcription]
backend = auto
model_path = /path/to/whisper-small-Q8_0.gguf
language = auto
timestamps = none        ; none = plain paragraphs, segment = [HH:MM:SS] per line
ffmpeg_path = ffmpeg
max_audio_mb = 2048
audio_timeout = 120
```

Transcript text follows the active language mode; Show Notes and the transcript are
translated independently.

---

## Sites with special handling

Surf ships handlers for Twitter/X, Reddit, Bluesky, Weibo, Threads, V2EX, WeChat Official
Accounts, Zhihu, Xiaohongshu, NCPSSD, Pocket Casts, Xiaoyuzhou, GitHub, Wikipedia, and
Foreign Affairs. Highlights:

Site defaults are part of the same system: WeChat, Zhihu, Xiaohongshu, NCPSSD, V2EX, and BBC
default to no proxy and no translation (Xiaohongshu also enables image OCR), and every one
of those defaults can be overridden by an explicit flag.

- **Twitter/X** prefers `uvx --from twitter-cli twitter` and reuses local browser cookies,
  resolves `t.co` links, normalises `/user/article/<id>` to `/i/article/<id>`, keeps the
  main tweet DOM so inline emphasis and media survive, and falls back to syndication,
  fxTwitter, and `api.fxtwitter.com` when X itself is unreachable.
- **Short posts** (Twitter/X, Bluesky, Weibo, Threads) are titled
  `First sentence - Author on Site`; long-form articles keep their own title.
- **Reddit** reads the JSON comments endpoint and reuses saved cookies; replies appear
  only with an explicit `--thread after|both` or `-t`.
- **Xiaohongshu** fetches the server-rendered note page with saved cookies first and only
  falls back to a browser; expired auth and risk-control responses are reported as errors
  instead of being emitted as content.
- **GitHub** fetches the resolved README/blob file while keeping the page URL you entered
  in the front matter `source`. Embedded HTML fragments in Markdown are converted back to
  Markdown; fenced code examples are preserved.
- **Foreign Affairs** strips the share/citation modal and subscription pitch so freely
  readable essays are not mistaken for paywalled ones.
- **Paywalls** trigger an archive-first fallback: `archive.today/newest/<url>` over plain
  HTTP, then the Wayback availability API with the toolbar-free `id_` document, then the
  `archive.ph` / `archive.is` / `archive.fo` / … chain through a browser, ending with a
  visible CAPTCHA window. Tracking parameters and fragments are stripped when looking up
  snapshots, and if Readability only recovers a teaser, Trafilatura is run as well.

Matching rules, per-site actions, and maintenance notes live in `SPECIAL_SITES.md`
(English) and `SPECIAL_SITES_zh.md` (中文). Keep them in sync with the code.

---

## Authentication

Some sites need a login. Surf stores a Playwright auth state per site and reuses it.

```bash
uv run surf --login xiaohongshu
uv run surf --export-auth xiaohongshu ~/Note/xhs_state.json   # move it to another machine
uv run surf --import-auth xiaohongshu ~/Note/xhs_state.json
uv run surf --clear-auth xiaohongshu
uv run surf --clear-auth all
```

Supported sites: `xiaohongshu`, `twitter` (alias `x`), `reddit`, `zhihu`, `douban`,
`ncpssd`. State lives in `%LOCALLAPPDATA%\surf\` on Windows and
`~/.local/cache/surf/` on Linux and macOS.

`--login` needs a desktop session. On headless Linux it fails fast instead of trying to
open a browser — log in on a desktop machine and move the state with `--export-auth` /
`--import-auth`.

Notes per site:

- **Twitter/X** first tries to import cookies from your real browser (or
  `TWITTER_AUTH_TOKEN` / `TWITTER_CT0`) through `twitter-cli`, which is usually more
  reliable than X's automated login page; if that is unavailable it opens a visible
  Playwright window and keeps a persistent profile.
- **Twitter/X** also accepts `--twitter-backend auto|cli|native`, `--twitter-browser`,
  and `--twitter-profile` for cookie import. `--twitter-cli-bin` is deprecated and
  ignored.
- **Zhihu** cookies feed API and mirror requests and help with browser verification.
- **NCPSSD** login is required by some secure full-text PDF downloads.

### Cloudflare cookies

Sites behind Cloudflare WAF can be reached by reusing a `cf_clearance` cookie from your
own browser:

1. Visit the site in Chrome and pass the challenge.
2. DevTools → Application → Cookies → pick the domain.
3. Copy the `cf_clearance` value.

```bash
uv run surf --set-cf-clearance example.com "cf_clearance=VALUE"
uv run surf --clear-cf-clearance example.com
uv run surf --clear-cf-clearance all
```

Or write it into the config file:

```ini
[Cloudflare]
example.com = cf_clearance=VALUE
```

The cookie must come from Chrome or another Chromium-based browser — Surf impersonates a
Chrome TLS fingerprint, so Firefox and Safari cookies will not work. These cookies expire
within 30 minutes to 24 hours; when one expires, the normal challenge handling takes over
again.

---

## Keyword search

```bash
uv run surf -S "python asyncio tutorial"                    # interactive picker
uv run surf -S "python asyncio tutorial" --search-top 3     # first 3, no prompt
uv run surf -S "python asyncio tutorial" --feel-lucky       # first result only
uv run surf -S "python asyncio tutorial" --search-merge     # one merged document
uv run surf -S "python asyncio tutorial" --search-prefix asyncio
uv run surf -S "python asyncio tutorial" --search-preview 3 --search-delay 2
```

`-S` turns the positional argument into keywords. Surf searches, prints the first
`--search-preview` results as a numbered list, and runs the selected URLs through the
normal fetch → extract → translate → save pipeline. One failing result is logged and
skipped instead of aborting the batch.

**Engines** — `--search-engine auto|duckduckgo|bing|searxng|metaso|deepseek` (default:
`[Search] engine`, `auto`). `auto` tries DuckDuckGo, then Bing, then any configured
DeepSeek/SearXNG/Metaso API.

```ini
[Search]
engine = auto
limit = 5             ; results retrieved per run, 1-20
preview = 5           ; rows shown in the interactive picker
searxng_url =         ; self-hosted instance with the JSON API enabled
metaso_api_key =      ; https://metaso.cn open API
deepseek_api_key =    ; POST https://api.deepseek.com/v1/tools/search
deepseek_url =        ; optional endpoint override
```

DeepSeek search supports `site:`, `lang:`, `after:`, `before:`, exact phrases, and
exclusion.

**Selecting** — enter `1`, `1,3-5`, `1 3 5`, or `all`; an empty line cancels. Invalid input
is re-prompted up to three times. When stdin is not a terminal (scripts, CI), all
retrieved results are processed; prefer `--search-top N` or `--feel-lucky` to keep batches
deterministic.

**Output** — one file per result by default, or one merged document with
`--search-merge`. No search-specific directory is created; files land in the regular
`[Output]` directories and are always named `[<prefix>]<title>`. The prefix defaults to
the keywords (truncated to 40 characters); `--search-prefix` overrides it, trailing
separators are normalised, and an already bracketed prefix is kept as-is. In search mode
`-o/--output` accepts only `-` (stdout). Every file records `search_query` in the front
matter, and tracking parameters (`utm_*`, `fbclid`, `gclid`, `dclid`, `spm`, …) are stripped
from the result URL before fetching and from the front matter `source`.

All other options (`-l`, `-p`, `-h`, `-a`, `-e`, proxy flags, `--archive`, …) apply to
every result.

---

## Web UI

```bash
uv run python surf_web.py                              # http://127.0.0.1:18473
uv run python surf_web.py --port 8080
uv run python surf_web.py --host 0.0.0.0 --root /surf
```

The page is one screen: a composer at the top, an options panel below it, then result
cards. Everything the composer needs is visible; everything that is a per-run preference
lives in the collapsible options panel, whose header summarises the current choices
(language · proxy · OCR · format).

### What the page does

- **Two input modes.** *Link / text* fetches URLs or saves pasted text as a post;
  *keyword search* runs the same search pipeline as `-S` and shows a checkbox list of
  hits, from which you fetch only what you want.
- **One primary action: 保存.** The large accent button at the right of the composer is
  the everyday path — it fetches with the current options and queues the file in the
  chosen format, without rendering anything first. Two quieter buttons sit beside it.
- **预览 (preview).** Renders result cards so you can read the content and pick a format
  per document. In search mode the same button becomes 搜索 and lists hits to select.
- **全文保存 (save as-is).** Keeps the pasted text intact even when it contains URLs, and
  saves it directly as a post — the shortcut for "this is my note, do not fetch anything".
  It is a one-shot mode: the next 保存 press goes back to normal link behaviour.
- **One card per document.** Each card has an editable save directory and filename, then
  format buttons (MD / HTML / PDF / 音频 / Pastebin) so you can export the same result in
  any format without re-fetching. A 朗读 button generates speech on the spot.
- **Three preview tabs.** Markdown, HTML, and the raw extracted text sit side by side.
- **Merge card.** When one run produces several results, an extra card merges them into a
  single Markdown/HTML/PDF/audio document.
- **Live translation.** With translation enabled the card appears immediately with the
  original text, then refreshes in place when the background translation job finishes.
- **Save queue.** Background saves are listed with their state (queued, saving, done,
  failed) and a task counter in the top bar; the panel hides itself once the queue drains.
- **Site-aware defaults.** Typing a URL applies that site's defaults — sites that keep the
  original language hide the LLM provider until you choose a translation mode, sites whose
  effective OCR default is off hide the engine selector, and the podcast transcription
  toggle only appears for Pocket Casts and Xiaoyuzhou episodes.
- **Proxy default.** The Web UI starts at `no` proxy (see [Proxy](#proxy)). Pick another
  mode when your deployment needs it.
- **Keyboard.** `Ctrl`/`Cmd` + `Enter` submits the composer.

GitHub repository and Markdown URLs use the ordinary language default in the Web UI:
non-Chinese content is translated unless you choose raw mode.

### Deploying

The built-in server is Flask's development server, so its production warning is expected.
For anything long-running, point a WSGI server at `surf_web:app`:

```bash
uv run waitress-serve --listen=0.0.0.0:18473 --threads=4 surf_web:app
```

Waitress keeps one copy of the application in memory (a thread pool instead of prefork
workers). That matters on small hosts: each gunicorn worker pays its own ~70 MB Python
baseline.

If you expose it to the internet, put it behind a reverse proxy with HTTPS and open only
the port you need.

### Behind a reverse proxy

Serving under a path prefix (`http://IP:8000/surf`) needs `--root`:

```bash
uv run python surf_web.py --host 127.0.0.1 --port 18473 --root /surf
```

```nginx
location /surf/ {
    proxy_pass http://127.0.0.1:18473/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

The trailing `/` in `proxy_pass` strips the prefix before forwarding
(`/surf/api/process` → `/api/process`), so `--root` is not needed in that case. If
`proxy_pass` has no trailing slash and the path is forwarded as-is, `--root /surf` is
required.

---

## Configuration

`config.ini.example` documents every option; the sections that matter most:

```ini
[LLM]
provider = LLM1                     ; default provider name

[LLM.LLM1]
api_style = chat                    ; chat, responses, or anthropic
base_url = https://api.openai.com/v1
api_key = your_api_key_here
model = gpt-3.5-turbo

[Output]
target_language = zh-cn             ; translation target
md_dir = ./notes
pdf_dir = ./pdf
html_dir = ./web
audio_dir = ./audio
pdf_engine = auto                   ; playwright, chrome, weasyprint, typst, pandoc
archive = false                     ; default for --archive
no_front_matter = false             ; default for --no-front-matter
html_inline = false                 ; default for --html-inline

[Content]
extractor = auto                    ; auto, readability, trafilatura, raw

[Network]
proxy_mode = env                    ; env, win, custom, no
custom_proxy =

[TTS]
voice = zh-CN-XiaoxiaoNeural
rate = +0%
volume = +0%

[Browser]
backend = obscura                   ; obscura or playwright
obscura_executable = obscura
obscura_endpoint = http://127.0.0.1:9222
obscura_fetch = auto                ; auto, native, playwright
obscura_stealth = false
obscura_navigation_timeout = 60
chrome_executable =                 ; for the Playwright-free PDF fallback
curlcffi_mode = auto                ; false, true, auto

[OCR]
enabled = false                     ; Xiaohongshu overrides this to true
engine = auto                       ; auto, paddleocr, rapidocr, tesseract
lang = chi_sim+eng
max_images = 8

[publish.pastebin]
api_dev_key = your_pastebin_api_key_here
```

`SURF_LOG_LEVEL` (e.g. `SURF_LOG_LEVEL=INFO`) raises log verbosity for long-running
deployments; `--verbose` does the same for a single CLI run. Surf logs only warnings by
default.

---

## CLI reference

Grouped by the same sections as `--help`.

**Input**

| Option | Meaning |
| --- | --- |
| `URL \| PATH \| KEYWORDS` | The single positional argument. Not needed for auth commands. |

**Output**

| Option | Meaning |
| --- | --- |
| `-f, --format {md,pdf,html,audio,publish}` | Output format (default `md`) |
| `-h` `-p` `-a` `-P` | Shorthands for `-f html/pdf/audio/publish` |
| `-o, --output PATH` | Output path, or `-` for stdout |
| `-O` | Same as `-o -` |
| `--html-inline` | Inline CSS/JS into HTML |
| `--no-front-matter` | Omit YAML front matter |
| `--archive` | Submit the source URL to the Wayback Machine |

**Language and audio**

| Option | Meaning |
| --- | --- |
| `-l, --lang {trans,raw,both}` | Translation mode (default `trans`) |
| `-r` `-b` | Shorthands for `-l raw` / `-l both` |
| `--llm PROVIDER` | LLM provider for this run |
| `-s, --speak` | Read the result aloud |
| `--tts-voice` `--tts-rate` `--tts-volume` | Override `[TTS]` for this run |
| `-w, --transcribe` | Transcribe a podcast episode locally |
| `--transcript-timestamps {none,segment}` | Timestamp style for transcripts |

**Extraction and rendering**

| Option | Meaning |
| --- | --- |
| `-e, --extractor {auto,readability,trafilatura,raw}` | Extraction strategy |
| `-R` `-T` `-U` | Shorthands for `readability` / `trafilatura` / `raw` |
| `--browser` | Force headless browser rendering |
| `-B, --browser-backend {auto,obscura,playwright}` | Which headless backend |

**Network**

| Option | Meaning |
| --- | --- |
| `-x, --proxy {env,win,custom,no}` | Proxy mode (`win` is Windows-only) |
| `-c [PROXY]` | Custom proxy, optionally inline |
| `-n` | No proxy |
| `--set-proxy URL` | Proxy URL for `-x custom` |

**Threads and social sites**

| Option | Meaning |
| --- | --- |
| `-t, --thread [{after,before,both,off}]` | Thread range |
| `--no-thread` | Disable thread expansion |
| `--thread-author {same,all}` | Author scope (default `all`) |
| `--twitter-backend {auto,cli,native}` | Twitter/X backend |
| `--twitter-browser` `--twitter-profile` | Cookie source for `twitter-cli` |

**OCR**

| Option | Meaning |
| --- | --- |
| `--ocr` / `--no-ocr` | Enable or disable image OCR |
| `--ocr-engine {rapidocr,tesseract,paddleocr,auto}` | Engine or fallback chain |
| `--ocr-lang LANGS` | Tesseract languages |

**Search** (`-S/--search` only)

| Option | Meaning |
| --- | --- |
| `-S, --search` | Treat the input as keywords |
| `--search-engine` | `auto`, `duckduckgo`, `bing`, `searxng`, `metaso`, `deepseek` |
| `--search-limit N` | Results to retrieve (max 20) |
| `--search-preview N` | Rows in the picker |
| `--search-top N` / `--feel-lucky` | Skip the picker |
| `--search-merge` | One merged document |
| `--search-prefix PREFIX` | Filename prefix |
| `--search-delay SECONDS` | Pause between fetches |

**Authentication and cookies**

| Option | Meaning |
| --- | --- |
| `--login SITE` | Interactive login |
| `--clear-auth SITE` | Delete saved auth (`all` for every site) |
| `--export-auth SITE FILE` / `--import-auth SITE FILE` | Move auth state between machines |
| `--set-cf-clearance DOMAIN COOKIE` | Store a Cloudflare clearance cookie |
| `--clear-cf-clearance DOMAIN` | Delete it (`all` for every domain) |

**Runtime**

| Option | Meaning |
| --- | --- |
| `--config FILE` | Use a specific config file |
| `--verbose` | Verbose logging |
| `--version` / `--help` | Version / this reference |

---

## Exit status and failure notes

Exit status is `0` on success and non-zero when the input could not be processed (the CLI
uses `1` for a failed fetch/convert/save and `130` for Ctrl+C).

When the CLI or the Web UI cannot fetch, convert, or save a web URL — including search
results — Surf writes the URL itself to `[Output] md_dir` as `获取失败_<title>.md` (or a
host/path slug when there is no title). Existing unrelated files with the same name are
never overwritten, and an existing note for the same URL is reused, so these files double
as a retry queue.

---

## More documentation

| File | Contents |
| --- | --- |
| `SPECIAL_SITES.md` / `SPECIAL_SITES_zh.md` | Every URL rule and handler action, in detail |
| `config.ini.example` | All configuration keys with comments |
| `surf.py --help` | The CLI reference above, from the source |
| `surf_web.py --help` | Web server options |
