# Special Site Handling (English)

This document records site-specific URL matching and handler behavior in Surf.

Chinese version: `SPECIAL_SITES_zh.md`

---

## Overview

Surf uses the `SPECIAL_SITE_HANDLERS` mapping to apply custom logic for specific domains.
Before this mapping is checked, common short URLs such as `t.co`, `bit.ly`, `tinyurl.com`, `xhslink.com`, and `pca.st` are resolved to their final long URL so matching, default policies, fetching, and front matter `source` all use the canonical target.
Each site entry may define:
- `patterns`: URL regex list
- `handler`: handler function
- `default_no_proxy`: default no-proxy policy (overridable by CLI)
- `force_proxy`: default force-proxy policy (overridable by CLI)
- `default_no_translate`: default raw-language policy (overridable by CLI)
- `default_thread`: default thread direction (`after`) with author scope `all`, overridable by `--thread`, `--thread-author`, or `--no-thread`

---

## Supported Special Sites

- Pocket Casts Episodes
- Twitter/X
- WeChat Official Accounts
- Zhihu
- Xiaohongshu
- GitHub
- Wikipedia
- arXiv
- Bluesky
- Weibo
- Threads
- V2EX
- Reddit
- NCPSSD
- Douban

### Pocket Casts Episodes

**Domains**:
- `pca.st`
- `pocketcasts.com`

**Matching patterns**:
```regex
^https?://(www\.)?pca\.st/episode/[0-9a-f-]+/?$
^https?://(www\.)?pocketcasts\.com/podcast/.../<podcast-id>/.../<episode-id>
```

**Handler**: `Fetcher._fetch_pocketcasts_episode`

**Processing flow**:
1. Resolve `pca.st/episode/<id>` share links to the Pocket Casts canonical episode URL.
2. Extract the podcast name, episode title, podcast UUID, and episode UUID from the canonical URL as a minimum fallback.
3. Fetch the episode page and prefer JSON-LD, Open Graph, embedded Show Notes, publication date, duration, author, and audio URL.
4. If the regular request does not provide usable content, try loading the dynamic page with Playwright.
5. If the page does not expose a feed URL, query the public Apple Podcasts directory by podcast name and only accept an exact podcast-name match.
6. If a public RSS feed is available, fetch it and match the episode by GUID/UUID or title to recover Show Notes, publication date, duration, and audio URL.
7. Return a direct Markdown payload so the normal translation, Markdown, HTML, PDF, and front matter pipeline remains unchanged.

**Fallback behavior**:
- A CloudFront/WAF block does not immediately fail the task. If the share-link redirect can be resolved, Surf still emits the podcast name, episode title, UUIDs, and source link.
- Front matter `source` uses the final Pocket Casts long URL; the original `pca.st` link may be retained as an opening link in the body when needed.
- The generated episode title is `Episode Title - Podcast Name`; the default filename prefixes it with `[播客]`, for example `[播客] Episode Title - Podcast Name`.
- Show Notes are emitted under a `## Show Notes` section; they are not copied wholesale into front matter `description`, and the `Podcast`, `Podcast ID`, and `Episode ID` labels are protected from translation.
- The episode publication date is written to front matter `created` when available, and `translator` remains empty unless translation changes the output.
- With `-w/--transcribe`, the RSS audio enclosure is downloaded and converted to 16 kHz mono float32 PCM, then transcribed locally through `transcribe-cpp`.
- The optional backend is installed with `uv sync --extra transcribe`; `[Transcription].model_path` must point to a GGUF model and `ffmpeg` must be available.
- The transcript is appended under `## Transcript` with segment timestamps. Transcript text follows the same language mode as the rest of the document (`trans` / `raw` / `both` from CLI, config defaults, and site policy). Show Notes and Transcript are language-detected and translated independently so Chinese notes cannot suppress English transcript translation. The current first version does not automatically download models or enable diarization.

---

For exact regex patterns and handler names, see `SPECIAL_SITE_HANDLERS` in `surf.py`.

---

## Key Policies by Site

### Twitter/X
- Prefers `uvx --from twitter-cli twitter` backend by default.
- Reuses local/browser cookies when possible.
- Includes fallback chain for login walls and unreachable `x.com` scenarios.
- Supports thread expansion with `--thread after|before|both|off` and `--thread-author same|all`.
- For tweet/article pages, the source URL stays in front matter `source`; it is no longer repeated at the top of the body.
- Author/screen name is written to front matter `author` instead of a leading `Author:` paragraph.

### WeChat / Xiaohongshu
- Default: no proxy, no translation (unless overridden).
- Xiaohongshu enables image OCR by default (RapidOCR then Tesseract fallback; PaddleOCR available via --ocr-engine).

### Zhihu
- Default: no proxy, no translation.
- Uses Zhihu-specific API/mirror/browser chain.
- Reuses saved Zhihu cookies for API/mirror requests when available.
- Filters links starting with `https://zhida.zhihu.com/search` from the extracted body while preserving visible text.
- Source URL, author, created time, and updated time are written to front matter instead of leading body paragraphs.
- Upvote/comment counts are not emitted into the body.

### Social Thread Sites (Twitter/X, Bluesky, Weibo, Threads)
- Default thread direction: `after`; default author scope: `all`.
- Short-post title normalization: `First sentence - Author on Site`.

### Reddit
- Scope: Reddit post/comment permalinks on `www.reddit.com`, `old.reddit.com`, `new.reddit.com`, and `redd.it` short links.
- Normalizes supported Reddit URLs to canonical `www.reddit.com` post URLs, then fetches the `.json?raw_json=1` comments endpoint.
- Reuses saved `reddit.com` cookies from `surf --login reddit` for both direct requests and browser fallback when available.
- Main post content is extracted from the top-level `t3` payload; link posts preserve the outbound target as `Link: ...`.
- Replies are included only when the user explicitly enables thread fetching with `--thread after|both` or `-t`; there is no Reddit-specific default thread expansion.
- `--thread-author same` keeps only comments written by the original post author.

### Douban
- Scope: topic posts matching `https://www.douban.com/topic/<id>/`.
- Normalizes `https://www.douban.com/doubanapp/dispatch?uri=...` into the canonical `https://www.douban.com/...` page URL before site matching/fetching.
- Reuses saved `douban.com` cookies from `surf --login douban` for direct requests and browser fallback when available.
- For topic posts, front matter extracts the author from `div.article-main div.article-meta a.author-name`.
- For topic posts, front matter extracts the created time from `div.article-main div.article-meta div.topic-meta span.create-time`.
- Front matter `source` strips Douban tracking query suffixes such as `?_spm_id=...&_dtcc=1`.

### V2EX
- Scope: `https://v2ex.com/t/<id>` and `https://www.v2ex.com/t/<id>` topic pages.
- Forces configured proxy by default, preferring `[Network].custom_proxy` when present; explicit `-x/--proxy` or `-n` still wins.
- Default: raw language, main topic only.
- Use `-t` / `--thread` to include replies. Reply pages (`?p=...`) are fetched and deduplicated when pagination is present.
- Uses a V2EX-specific DOM parser and direct Markdown payload so generic readability does not mistake replies for the main post.

### arXiv
- Scope: `https://arxiv.org/abs/<id>`, `https://arxiv.org/pdf/<id>`, and `https://arxiv.org/html/<id>`.
- Fetches the HTML version of the paper (`/html/<id>v1`) which has the full paper content.
- Extracts metadata (title, authors, abstract, subjects) from the abstract page.
- Falls back to abstract page content extraction if the HTML version is unavailable.

### GitHub
- Repo URLs such as `https://github.com/USER/REPO` fetch the best matching README Markdown file directly, while front matter `source` remains the repo URL.
- Branchless Markdown URLs such as `https://github.com/USER/REPO/PATH/TO/FILE.md` fetch from `main`/`master` candidates, while front matter `source` remains the branchless URL.
- Embedded HTML fragments inside directly fetched Markdown are converted back to Markdown while fenced code examples are preserved.
- Relative links inside fetched Markdown are resolved against the actual blob URL used for content, not the preserved source URL.
- Surf Web uses the normal language default for GitHub repo and Markdown URLs: non-Chinese Markdown is translated to Chinese unless raw mode is selected manually.

### NCPSSD
- Scope: `.../Literature/secure/articleinfo?params=...` pages.
- Default output format for this scope: PDF (implicit `-p`) unless user explicitly chooses another format.
- In PDF mode, prioritizes original full-text download via the page’s `全文下载` flow.
- Reuses `--login ncpssd` auth state.
- Original PDF filename prefers: `PaperTitle-Author-Journal.pdf`.
- If metadata is not present in DOM, falls back to page async API metadata.

---

## Fallback Behavior

If a special handler fails or returns `None`, Surf falls back to generic fetch/conversion flow unless the site explicitly disables generic fallback.

---

## Documentation Maintenance

When changing any special-site behavior, keep both docs synchronized:
- `SPECIAL_SITES_zh.md` (Chinese)
- `SPECIAL_SITES.md` (English)

Typical change triggers:
- Add/remove special sites
- Update URL patterns
- Change handler logic or fallback order
- Change default policies (proxy/translation/thread/output)
- Change auth-dependent behavior

---

**Last Updated**: 2026-06-12
**Doc Version**: 1.0
