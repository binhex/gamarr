# SeleniumBase UC Mode for FreeGOG Cloudflare Bypass

**Date:** 2026-07-25
**Status:** approved

## Problem

`freegogpcgames.com` is behind Cloudflare Turnstile managed challenge. Four
approaches were evaluated and failed:

| Approach | Result |
|----------|--------|
| cloudscraper (PyPI) | No Turnstile support |
| cloudscraper (GitHub) | Requires Playwright for "High-Security" sites |
| byparr (flaresolverr) | "Timed out while solving the challenge" |
| nodriver | Challenge scripts fail to load in headless Chrome |

SeleniumBase UC Mode is the #1 recommended open-source approach from the
[cloudflare-bypass-2026](https://github.com/1837620622/cloudflare-bypass-2026)
repository (403 stars, updated 2026-07-25). It uses a headed Chrome with a
virtual display (Xvfb) to bypass Turnstile managed challenges.

## Solution

Replace `requests.get()` in `freegog.py` with SeleniumBase UC Mode. The SB
manager handles virtual display automatically on headless Linux environments
(Docker). It bypasses Cloudflare Turnstile via `uc_open_with_reconnect()` and
auto-clicks any CAPTCHA checkbox with `uc_gui_click_captcha()`.

## Design

### Dependency

`pyproject.toml`: Add `seleniumbase` to `[project] dependencies`.
SeleniumBase includes Chromedriver and Xvfb handling — no separate
Chromium or Xvfb packages needed.

### FreeGOG source changes (`src/gamarr/sources/freegog.py`)

**New sync helper:**

```python
from seleniumbase import SB

def _sb_uc_get(url: str) -> str:
    """Fetch a URL through SeleniumBase UC Mode, returning HTML."""
    with SB(uc=True, headless2=True) as sb:
        sb.uc_open_with_reconnect(url, 4)
        sb.uc_gui_click_captcha()
        return sb.get_page_source()
```

`headless2=True`: SeleniumBase's special headless mode that uses a virtual
display (Xvfb) — works in Docker without a physical display.
`uc_open_with_reconnect(url, 4)`: Opens URL with automatic reconnection to
bypass Turnstile challenge.
`uc_gui_click_captcha()`: Auto-clicks Cloudflare checkbox if one appears.

**Call site replacements:**

| Method | Before | After |
|--------|--------|-------|
| `_index_az_page` | `resp = requests.get(url, timeout=30, headers=...)` → `resp.text` | `html = _sb_uc_get(url)` |
| `_fetch_and_store_game` | `game_resp = requests.get(url, timeout=30, headers=...)` → `game_resp.text` | `html = _sb_uc_get(url)` |

`_USER_AGENT` constant removed.

### What does NOT change

- `_parse_freegog_az_page()` — HTML string in, list out
- `_extract_magnet_from_freegog_page()` — HTML string in, magnet or None out
- `_clean_freegog_title()` — string processing
- Database, caching, all other files — unchanged

### Error handling

SeleniumBase exceptions are caught by `except Exception` in the existing
try/except blocks. Timeout/reconnection failures log as warnings.

### Docker

No `arch-gamarr/install.sh` changes needed. SeleniumBase pulls in
Chromedriver via pip. Existing `chromium` pacman package can be removed
if it was added during the nodriver attempt.

## Test plan

Unit tests mock `_sb_uc_get` at the module level instead of `requests.get`.
The mock returns a plain HTML string. Existing parsers receive the same
input format — test assertions remain valid.
