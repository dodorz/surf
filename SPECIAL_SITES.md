# Special Site Handling (English)

This document records site-specific URL matching and handler behavior in Surf.

Chinese version: `SPECIAL_SITES_zh.md`

---

## Overview

Surf uses the `SPECIAL_SITE_HANDLERS` mapping to apply custom logic for specific domains.
Before this mapping is checked, common short URLs such as `t.co`, `bit.ly`, `tinyurl.com`, and `xhslink.com` are resolved to their final long URL so matching, default policies, fetching, and front matter `source` all use the canonical target.
Each site entry may define:
- `patterns`: URL regex list
- `handler`: handler function
- `default_no_proxy`: default no-proxy policy (overridable by CLI)
- `force_proxy`: default force-proxy policy (overridable by CLI)
- `default_no_translate`: default raw-language policy (overridable by CLI)
- `default_thread`: default thread mode (`backward`), overridable by `--thread` / `--no-thread`

---

## Supported Special Sites

1. Twitter/X
2. WeChat Official Accounts
3. Zhihu
4. Xiaohongshu
5. GitHub
6. Wikipedia
7. Bluesky
8. Weibo
9. Threads
10. V2EX
11. NCPSSD

For exact regex patterns and handler names, see `SPECIAL_SITE_HANDLERS` in `surf.py`.

---

## Key Policies by Site

### Twitter/X
- Prefers `uvx --from twitter-cli twitter` backend by default.
- Reuses local/browser cookies when possible.
- Includes fallback chain for login walls and unreachable `x.com` scenarios.
- Supports same-author thread expansion with `--thread`.

### WeChat / Xiaohongshu
- Default: no proxy, no translation (unless overridden).
- Xiaohongshu enables image OCR by default.

### Zhihu
- Default: no proxy, no translation.
- Uses Zhihu-specific API/mirror/browser chain.
- Reuses saved Zhihu cookies for API/mirror requests when available.

### Social Thread Sites (Twitter/X, Bluesky, Weibo, Threads)
- Default thread mode: `backward`.
- Short-post title normalization: `First sentence - Author on Site`.

### V2EX
- Scope: `https://v2ex.com/t/<id>` and `https://www.v2ex.com/t/<id>` topic pages.
- Forces configured proxy by default, preferring `[Network].custom_proxy` when present; explicit `-x/--proxy` or `-n` still wins.
- Default: raw language, main topic only.
- Use `-t` / `--thread` to include replies. Reply pages (`?p=...`) are fetched and deduplicated when pagination is present.
- Uses a V2EX-specific DOM parser and direct Markdown payload so generic readability does not mistake replies for the main post.

### GitHub
- Repo URLs such as `https://github.com/USER/REPO` fetch the best matching README Markdown file directly, while front matter `source` remains the repo URL.
- Branchless Markdown URLs such as `https://github.com/USER/REPO/PATH/TO/FILE.md` fetch from `main`/`master` candidates, while front matter `source` remains the branchless URL.
- Relative links inside fetched Markdown are resolved against the actual blob URL used for content, not the preserved source URL.

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

**Last Updated**: 2026-04-17
**Doc Version**: 1.0
