# Nodriver for FreeGOG Cloudflare Bypass

**Date:** 2026-07-24
**Status:** approved

## Problem

`freegogpcgames.com` is behind Cloudflare Turnstile (managed challenge). Four
approaches were evaluated:

| Approach | Result |
|----------|--------|
| Plain `requests` | 403 — "Just a moment..." challenge page |
| cloudscraper (PyPI 1.2.71) | No Turnstile support |
| cloudscraper (GitHub 3.8.3) | Turnstile disabled by default; requires Playwright for "High-Security" sites |
| byparr/flaresolverr | "Timed out while solving the challenge" even at 180s maxTimeout |

Nodriver is the successor to undetected-chromedriver and is purpose-built to
bypass Cloudflare, hCaptcha, and Turnstile via direct Chrome DevTools Protocol.

## Solution

Replace `requests.get()` in `freegog.py` with nodriver — an undetected Chrome
browser automation library. Each HTTP call starts a fresh browser via
`asyncio.run()`, waits for the Cloudflare challenge to resolve, gets the
rendered HTML, and closes. No persistent browser state.

## Design

### Dependencies

**`pyproject.toml`**: Add `nodriver` to `[project] dependencies`.

**`arch-gamarr/build/root/install.sh`**: Add `chromium` to the `pacman_packages`
list. Chromium gets baked into the Docker image at build time (~150MB).

### FreeGOG source changes (`src/gamarr/sources/freegog.py`)

**New async helper** at module level:

```python
def _nodriver_get(url: str, timeout: int = 30) -> str:
    """Fetch a URL through nodriver (undetected Chrome), returning HTML."""
    return asyncio.run(_nodriver_get_async(url, timeout))

async def _nodriver_get_async(url: str, timeout: int = 30) -> str:
    browser = await nodriver.start(headless=True)
    try:
        page = await browser.get(url)
        await page
        return await page.get_content()
    finally:
        browser.stop()
```

**Call site replacements:**

| Method | Before | After |
|--------|--------|-------|
| `_index_az_page` | `resp = requests.get(url, timeout=30, headers=...)` → `resp.text` | `html = _nodriver_get(url)` |
| `_fetch_and_store_game` | `game_resp = requests.get(url, timeout=30, headers=...)` → `game_resp.text` | `html = _nodriver_get(url)` |

`_USER_AGENT` constant removed — nodriver provides browser headers.

**Error handling:** Change `except requests.RequestException` to `except Exception`
in both methods since nodriver raises its own exception types (not `requests`
exceptions).

### What does NOT change

- `_parse_freegog_az_page()` — HTML string in, list out
- `_extract_magnet_from_freegog_page()` — HTML string in, magnet or None out
- `_clean_freegog_title()` — string processing
- Database, caching — unchanged
- FitGirl source — unchanged
- No new config keys, CLI flags, or env vars

### Timeout

Browser start ~2s, page load + Cloudflare challenge ~5-15s. Default 30s timeout
provides headroom. The FreeGOG source polls every 6 hours (cached between runs),
so ~20s per request is acceptable.

## Test plan

Unit tests mock `_nodriver_get` at the module level instead of `requests.get`.
The mock returns a raw HTML string directly (the parsers downstream are
unchanged). All existing test assertions for `_parse_freegog_az_page` and
`_extract_magnet_from_freegog_page` remain valid.

Integration test: manual verification with a live Docker build against
freegogpcgames.com.
