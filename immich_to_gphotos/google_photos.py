"""Google Photos automation via Playwright."""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from immich_to_gphotos import log
from immich_to_gphotos.config import ensure_private_dir, resolve_tmp_dir

PHOTOS_HOME = "https://photos.google.com/"
PHOTOS_APP = "https://photos.google.com/"
UPLOAD_BATCH_SIZE = 30
UPLOAD_SETTLE_SECONDS = 15
REFRESH_SESSION_WAIT_MS = 60_000


class GooglePhotosError(Exception):
    pass


class GooglePhotosSession:
    def __init__(self, auth_file: Path) -> None:
        self._auth_file = auth_file

    def run_auth(self, *, cdp_url: str | None = None) -> None:
        """Headed login; persist storage state."""
        ensure_private_dir(self._auth_file.parent)
        cdp = (cdp_url or os.environ.get("IMMICH_TO_GPHOTOS_CDP_URL", "")).strip() or None

        with sync_playwright() as p:
            if cdp:
                context, page, close_fn = _connect_cdp(p, cdp)
                log.info(f"connected to Chrome at {cdp}; sign in if needed")
            else:
                profile = self._auth_file.parent / "chrome-profile"
                context = _launch_persistent_auth(p, profile)
                context.set_default_timeout(600_000)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(PHOTOS_APP, wait_until="domcontentloaded")
                _open_sign_in(page)
                close_fn = context.close
                log.info(
                    "sign in with your Google account in the Chrome window "
                    "(waiting up to 10 minutes)"
                )

            log.info(f"current URL: {page.url}")
            _raise_if_google_blocked(page, cdp=cdp)

            try:
                _wait_for_photos_app(page, timeout_ms=600_000)
            except Exception as exc:
                close_fn()
                raise GooglePhotosError("timed out waiting for Google Photos sign-in") from exc

            _raise_if_google_blocked(page, cdp=cdp)
            if _needs_login(page):
                close_fn()
                raise GooglePhotosError("not signed in to Google Photos")

            _save_storage_state(context, self._auth_file)
            close_fn()
        log.info(f"saved Google session to {self._auth_file}")

    def refresh_session(self) -> None:
        """Headless visit to Google Photos; persist refreshed cookies or fail if expired."""
        if not self._auth_file.is_file():
            raise GooglePhotosError(
                f"Google session not found at {self._auth_file}; run: immich-to-gphotos auth"
            )

        with sync_playwright() as p:
            browser, context, page = self._launch(p, headless=True, wait_after_goto_ms=0)
            try:
                try:
                    _wait_for_photos_app(page, timeout_ms=REFRESH_SESSION_WAIT_MS)
                except Exception as exc:
                    raise GooglePhotosError(
                        "timed out waiting for Google Photos during session refresh"
                    ) from exc
                _require_signed_in(page)
                _save_storage_state(context, self._auth_file)
            finally:
                context.close()
                browser.close()
        log.info(f"refreshed Google session at {self._auth_file}")

    def upload_files(self, album_name: str, file_paths: list[Path]) -> None:
        """Upload files to an existing album."""
        if not file_paths:
            return
        if not self._auth_file.is_file():
            raise GooglePhotosError(
                f"Google session not found at {self._auth_file}; run: immich-to-gphotos auth"
            )

        with sync_playwright() as p:
            browser, context, page = self._launch(p, headless=True)
            try:
                _require_signed_in(page)
                _open_album(page, album_name)
                for batch in _chunks(file_paths, UPLOAD_BATCH_SIZE):
                    _upload_batch(page, batch)
                    if len(file_paths) > UPLOAD_BATCH_SIZE:
                        page.wait_for_timeout(2000)
                _save_storage_state(context, self._auth_file)
                log.info(f"refreshed Google session at {self._auth_file}")
            finally:
                context.close()
                browser.close()

    def _launch(
        self,
        p: Playwright,
        *,
        headless: bool,
        wait_after_goto_ms: int = 2000,
    ) -> tuple[Browser, object, Page]:
        browser = _launch_system_chrome(p, headless=headless)
        context = browser.new_context(storage_state=str(self._auth_file))
        page = context.new_page()
        page.goto(PHOTOS_HOME, wait_until="domcontentloaded", timeout=60_000)
        if wait_after_goto_ms:
            page.wait_for_timeout(wait_after_goto_ms)
        return browser, context, page


def _chrome_profile_dir(auth_file: Path) -> Path:
    return auth_file.parent / "chrome-profile"


def _save_storage_state(context: BrowserContext, auth_file: Path) -> None:
    """Write Playwright storage state at mode 0o600 via a private temp file."""
    ensure_private_dir(auth_file.parent)
    fd, tmp_name = tempfile.mkstemp(
        dir=auth_file.parent,
        prefix=".google-storage-",
        suffix=".tmp",
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        context.storage_state(path=str(tmp_path))
        tmp_path.chmod(0o600)
        tmp_path.replace(auth_file)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _chrome_launch_kwargs(*, headless: bool) -> dict:
    return dict(
        headless=headless,
        ignore_default_args=["--enable-automation"],
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )


def _chrome_executable_candidates() -> list[str]:
    return [
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/opt/google/chrome/google-chrome",
    ]


def _launch_system_chrome(p: Playwright, *, headless: bool) -> Browser:
    """Use system Google Chrome or Edge (not Playwright-bundled Chromium)."""
    kwargs = _chrome_launch_kwargs(headless=headless)
    errors: list[str] = []
    for channel in ("chrome", "msedge"):
        try:
            return p.chromium.launch(channel=channel, **kwargs)
        except Exception as exc:
            errors.append(f"channel={channel}: {exc}")
    for exe in _chrome_executable_candidates():
        if not Path(exe).is_file():
            continue
        try:
            return p.chromium.launch(executable_path=exe, **kwargs)
        except Exception as exc:
            errors.append(f"executable_path={exe}: {exc}")
    detail = errors[-1] if errors else "no Chrome/Edge binary found"
    raise GooglePhotosError(
        "system Chrome/Edge not found; install google-chrome-stable "
        f"(e.g. sudo apt install google-chrome-stable) or use auth --cdp-url. "
        f"Last error: {detail}"
    )


def _launch_persistent_auth(p: Playwright, profile: Path) -> BrowserContext:
    """Dedicated system Chrome profile for sign-in."""
    ensure_private_dir(profile)
    common = dict(
        user_data_dir=str(profile),
        viewport=None,
        locale="en-US",
        **_chrome_launch_kwargs(headless=False),
    )
    errors: list[str] = []
    for channel in ("chrome", "msedge"):
        try:
            return p.chromium.launch_persistent_context(channel=channel, **common)
        except Exception as exc:
            errors.append(f"channel={channel}: {exc}")
    for exe in _chrome_executable_candidates():
        if not Path(exe).is_file():
            continue
        try:
            return p.chromium.launch_persistent_context(executable_path=exe, **common)
        except Exception as exc:
            errors.append(f"executable_path={exe}: {exc}")
    detail = errors[-1] if errors else "unknown"
    raise GooglePhotosError(
        "could not launch Chrome for sign-in. "
        "Run from a graphical session (not plain SSH), or use auth --cdp-url. "
        f"Last error: {detail}"
    )


def _connect_cdp(p: Playwright, cdp_url: str) -> tuple[BrowserContext, Page, object]:
    """Attach to Chrome the user started with --remote-debugging-port=9222."""
    browser = p.chromium.connect_over_cdp(cdp_url)
    if not browser.contexts:
        raise GooglePhotosError(f"no browser contexts at {cdp_url}")
    context = browser.contexts[0]
    context.set_default_timeout(600_000)
    page = context.pages[0] if context.pages else context.new_page()
    if not context.pages or "photos.google.com" not in page.url:
        page.goto(PHOTOS_APP, wait_until="domcontentloaded")
    return context, page, browser.close


def _raise_if_google_blocked(page: Page, *, cdp: str | None) -> None:
    body = ""
    try:
        body = page.inner_text("body", timeout=2000)
    except Exception:
        return
    if "Couldn't sign you in" not in body and "may not be secure" not in body:
        return
    hint = (
        "Google blocked automated sign-in. Use your real Chrome:\n"
        "  google-chrome --remote-debugging-port=9222 https://photos.google.com\n"
        "  immich-to-gphotos auth --cdp-url http://127.0.0.1:9222"
    )
    if cdp:
        hint = "Google still blocked sign-in in the connected browser; try a normal Chrome window."
    raise GooglePhotosError(hint)


def _open_sign_in(page: Page) -> None:
    """Leave marketing/about pages and open Google sign-in or Photos app."""
    if "photos.google.com" in page.url and "/about" not in page.url:
        return
    for label in ("Go to Google Photos", "Sign in", "Get started"):
        try:
            link = page.get_by_role("link", name=label)
            if link.count() > 0:
                link.first.click(timeout=5000)
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
                return
        except Exception:
            pass
    page.goto(
        "https://accounts.google.com/ServiceLogin?passive=1209600"
        "&continue=https://photos.google.com/&followup=https://photos.google.com/",
        wait_until="domcontentloaded",
    )


def _wait_for_photos_app(page: Page, *, timeout_ms: int) -> None:
    """Wait until the signed-in Google Photos web app is open."""
    page.wait_for_function(
        """() => {
            const host = location.hostname;
            if (host === 'photos.google.com') return true;
            if (host.endsWith('.google.com') && location.pathname.includes('/photos')) {
                return !location.pathname.includes('/about');
            }
            return false;
        }""",
        timeout=timeout_ms,
    )


def _needs_login(page: Page) -> bool:
    url = page.url
    if "accounts.google.com" in url or "ServiceLogin" in url:
        return True
    if "/photos/about" in url:
        return True
    return "photos.google.com" not in url and "/photos" not in url


def _require_signed_in(page: Page) -> None:
    if _needs_login(page):
        raise GooglePhotosError("Google session expired; run: immich-to-gphotos auth")


def _album_title_pattern(album_name: str, *, exact: bool = False) -> re.Pattern[str]:
    escaped = re.escape(album_name.strip())
    flags = re.IGNORECASE
    if exact:
        return re.compile(f"^{escaped}$", flags)
    return re.compile(escaped, flags)


def _album_title_first_line(label: str) -> str:
    """Google album tiles show 'Title' then 'N items · Shared' on the next line."""
    return label.strip().splitlines()[0].strip() if label else ""


def _label_matches_album(label: str, album_name: str) -> bool:
    """Case-insensitive match on the tile title (first line of label text)."""
    first = _album_title_first_line(label)
    name_cf = album_name.casefold().strip()
    if not first or not name_cf:
        return False
    first_cf = first.casefold()
    if first_cf == name_cf:
        return True
    if not first_cf.startswith(name_cf):
        return name_cf in first_cf
    rest = first_cf[len(name_cf) :].lstrip()
    if not rest:
        return True
    if rest[0] in "·|-":
        return True
    return rest.startswith("by ")


def _on_albums_list_page(page: Page) -> bool:
    return bool(re.search(r"photos\.google\.com/albums/?(\?|$)", page.url, re.I))


def _album_page_opened(page: Page, album_name: str) -> bool:
    """True when the album detail view is open (owned /album/ or shared /share/ URLs)."""
    if _on_albums_list_page(page):
        return False
    url = page.url.casefold()
    if "/album/" in url or "/share/" in url:
        return True
    try:
        heading = page.get_by_role("heading", name=album_name, exact=True)
        if heading.count() > 0 and heading.first.is_visible():
            return True
    except Exception:
        pass
    return False


def _activate_shared_with_me_filter(page: Page) -> None:
    """Click the 'Shared with me' chip on the albums page (not per-album 'Shared' badges)."""
    for role in ("tab", "button"):
        chip = page.get_by_role(role, name=re.compile(r"^Shared with me$", re.I))
        try:
            if chip.count() > 0:
                chip.first.click(timeout=5000)
                page.wait_for_timeout(2000)
                return
        except Exception:
            continue
    try:
        chip = page.get_by_text("Shared with me", exact=True)
        if chip.count() > 0:
            chip.first.click(timeout=5000)
            page.wait_for_timeout(2000)
    except Exception:
        pass


def _wait_for_albums_grid(page: Page, album_name: str) -> None:
    """Wait until the albums page has rendered (tile or filter chips)."""
    try:
        page.get_by_role("button", name=re.compile(r"^(All|My albums|Shared with me)$", re.I)).first.wait_for(
            state="visible",
            timeout=30_000,
        )
    except Exception:
        pass
    try:
        page.get_by_text(album_name, exact=True).first.wait_for(state="visible", timeout=15_000)
    except Exception:
        page.wait_for_timeout(3000)


def _scroll_album_list(page: Page) -> bool:
    """Scroll the album grid; return False when nothing more loads."""
    before = page.evaluate("() => window.scrollY")
    page.evaluate(
        """() => {
            const main = document.querySelector('main');
            if (main) main.scrollTop += 900;
            window.scrollBy(0, 900);
        }"""
    )
    page.wait_for_timeout(500)
    after = page.evaluate("() => window.scrollY")
    return after > before


def _click_album_tile_via_js(page: Page, album_name: str) -> bool:
    """Click an album card by matching the title line (tiles are often not <a> links)."""
    name_cf = album_name.casefold().strip()
    clicked = page.evaluate(
        """({ nameCf }) => {
            const titleMatches = (text) => {
                const first = (text || '').trim().split(/\\n/)[0].trim().toLowerCase();
                return first === nameCf;
            };
            const root = document.querySelector('main') || document.body;
            const candidates = root.querySelectorAll('a, [role="link"], [role="button"], [jsaction]');
            for (const el of candidates) {
                const label = (el.getAttribute('aria-label') || el.innerText || '').trim();
                if (!label || !titleMatches(label)) continue;
                const lines = label.split(/\\n/).map((s) => s.trim()).filter(Boolean);
                if (lines.length > 4) continue;
                el.click();
                return true;
            }
            for (const el of root.querySelectorAll('div, span, a, h2, h3')) {
                const text = (el.innerText || '').trim();
                if (!text || !titleMatches(text)) continue;
                const lines = text.split(/\\n/).map((s) => s.trim()).filter(Boolean);
                if (!lines.length || lines[0].toLowerCase() !== nameCf) continue;
                if (lines.length > 4) continue;
                let node = el;
                for (let depth = 0; depth < 12 && node; depth++) {
                    if (node.tagName === 'A' && node.href && (node.href.includes('/album/') || node.href.includes('/share/'))) {
                        node.click();
                        return true;
                    }
                    const role = node.getAttribute && node.getAttribute('role');
                    if (role === 'link' || role === 'button' || node.hasAttribute?.('jsaction')) {
                        node.click();
                        return true;
                    }
                    node = node.parentElement;
                }
            }
            return false;
        }""",
        {"nameCf": name_cf},
    )
    if not clicked:
        return False
    page.wait_for_timeout(4000)
    return _album_page_opened(page, album_name)


def _click_album_by_visible_title(page: Page, album_name: str) -> bool:
    """Click the album title text and walk up to a clickable tile."""
    try:
        titles = page.get_by_text(album_name, exact=True)
        count = min(titles.count(), 20)
    except Exception:
        return False
    for i in range(count):
        try:
            title = titles.nth(i)
            if not title.is_visible():
                continue
            parent_text = title.evaluate(
                """(el) => (el.closest('a,[role="link"],[role="button"],[jsaction]') || el.parentElement)?.innerText || ''"""
            )
            if parent_text and not _label_matches_album(parent_text, album_name):
                continue
            clicked = title.evaluate(
                """(el) => {
                    let node = el;
                    for (let depth = 0; depth < 12 && node; depth++) {
                        if (node.tagName === 'A' && node.href && (node.href.includes('/album/') || node.href.includes('/share/'))) {
                            node.click();
                            return true;
                        }
                        const role = node.getAttribute?.('role');
                        if (role === 'link' || role === 'button' || node.hasAttribute?.('jsaction')) {
                            node.click();
                            return true;
                        }
                        node = node.parentElement;
                    }
                    el.click();
                    return true;
                }"""
            )
            if not clicked:
                continue
            page.wait_for_timeout(4000)
            if _album_page_opened(page, album_name):
                return True
            page.go_back(wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1500)
        except Exception:
            continue
    return False


def _click_album_match(page: Page, album_name: str) -> bool:
    patterns = (
        _album_title_pattern(album_name, exact=True),
        _album_title_pattern(album_name, exact=False),
    )
    for pattern in patterns:
        locators = [
            page.get_by_role("link", name=pattern),
            page.locator("[aria-label]").filter(has_text=pattern),
            page.get_by_text(pattern),
        ]
        for locator in locators:
            try:
                count = min(locator.count(), 30)
            except Exception:
                continue
            for i in range(count):
                try:
                    el = locator.nth(i)
                    if not el.is_visible():
                        continue
                    text = (el.inner_text(timeout=1000) or "").strip()
                    aria = el.get_attribute("aria-label") or ""
                    if not _label_matches_album(text or aria, album_name):
                        continue
                    el.click(timeout=10_000)
                    page.wait_for_timeout(4000)
                    if _album_page_opened(page, album_name):
                        return True
                    page.go_back(wait_until="domcontentloaded", timeout=30_000)
                    page.wait_for_timeout(1500)
                except Exception:
                    continue
    return _click_album_tile_via_js(page, album_name)


def _try_open_album_on_current_page(page: Page, album_name: str) -> bool:
    strategies = (
        _click_album_by_visible_title,
        _click_album_tile_via_js,
        _click_album_match,
    )
    for _ in range(25):
        for strategy in strategies:
            if strategy(page, album_name):
                return True
        if not _scroll_album_list(page):
            break
    return False


def _open_album(page: Page, album_name: str) -> None:
    """Navigate to an existing album by name (owned or shared with you)."""
    album_name = album_name.strip()
    if not album_name:
        raise GooglePhotosError("Google album name is empty")

    page.goto("https://photos.google.com/albums", wait_until="domcontentloaded", timeout=60_000)
    _wait_for_albums_grid(page, album_name)

    if _try_open_album_on_current_page(page, album_name):
        return

    log.info(f'filtering albums to "Shared with me" for "{album_name}"')
    _activate_shared_with_me_filter(page)
    if _try_open_album_on_current_page(page, album_name):
        return

    page.goto("https://photos.google.com/sharing", wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(3000)
    if _try_open_album_on_current_page(page, album_name):
        return

    if _album_page_opened(page, album_name):
        log.info(f'opened Google album "{album_name}"')
        return

    save_failure_screenshot(page)
    raise GooglePhotosError(
        f'Google album "{album_name}" not found '
        "(checked All albums, Shared with me, and sharing; name match is case-insensitive)"
    )


def _upload_batch(page: Page, paths: list[Path]) -> None:
    """Upload a batch via Add photos → Select from computer."""
    strings = [str(p.resolve()) for p in paths]

    file_input = page.locator('input[type="file"]')
    if file_input.count() == 0:
        _open_add_photos_menu(page)
        if _pick_files_via_menu(page, strings):
            log.info(f"uploading batch of {len(paths)} file(s) to Google Photos")
            page.wait_for_timeout(UPLOAD_SETTLE_SECONDS * 1000)
            return
        file_input = page.locator('input[type="file"]')

    if file_input.count() == 0:
        save_failure_screenshot(page)
        raise GooglePhotosError("could not find file upload input on Google Photos")

    file_input.first.set_input_files(strings)
    log.info(f"uploading batch of {len(paths)} file(s) to Google Photos")
    page.wait_for_timeout(UPLOAD_SETTLE_SECONDS * 1000)


def _open_add_photos_menu(page: Page) -> None:
    for label in ("Add photos", "Add to album"):
        btn = page.get_by_role("button", name=label)
        if btn.count() > 0:
            btn.first.click(timeout=5000)
            page.wait_for_timeout(1500)
            return
    raise GooglePhotosError('could not find "Add photos" on album page')


def _pick_files_via_menu(page: Page, paths: list[str]) -> bool:
    """Click upload menu item; return True if files were attached."""
    menu_labels = (
        "Select from computer",
        "From computer",
        "Computer",
        "Upload from computer",
    )
    for label in menu_labels:
        item = page.get_by_text(label, exact=True)
        if item.count() == 0:
            item = page.get_by_role("menuitem", name=label)
        if item.count() == 0:
            continue
        try:
            with page.expect_file_chooser(timeout=10_000) as chooser_info:
                item.first.click(timeout=5000)
            chooser_info.value.set_files(paths)
            return True
        except Exception:
            try:
                item.first.click(timeout=5000)
                page.wait_for_timeout(1000)
                if page.locator('input[type="file"]').count() > 0:
                    page.locator('input[type="file"]').first.set_input_files(paths)
                    return True
            except Exception:
                continue
    return False


def _chunks(items: list[Path], size: int) -> list[list[Path]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def save_failure_screenshot(page: Page, directory: Path | None = None) -> None:
    directory = directory or resolve_tmp_dir()
    ensure_private_dir(directory)
    path = directory / f"google-failure-{int(time.time())}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        log.warning(f"saved failure screenshot to {path}")
    except Exception:
        pass


def clear_failure_screenshots(directory: Path | None = None) -> None:
    directory = directory or resolve_tmp_dir()
    """Remove saved failure screenshots after a successful upload run."""
    if not directory.is_dir():
        return
    for path in directory.glob("google-failure-*.png"):
        try:
            path.unlink()
            log.info(f"removed failure screenshot {path.name}")
        except OSError as exc:
            log.warning(f"could not remove failure screenshot {path.name}: {exc}")
