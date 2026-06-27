"""Tests for GooglePhotosSession.refresh_session."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_to_gphotos.google_photos import (
    REFRESH_SESSION_WAIT_MS,
    GooglePhotosError,
    GooglePhotosSession,
    photos_ui_cookie_days_remaining,
    photos_ui_session_needs_refresh,
)


def _write_storage_state(path: Path, *, compass_expires: float | None) -> None:
    cookies: list[dict[str, object]] = []
    if compass_expires is not None:
        cookies.append(
            {
                "name": "COMPASS",
                "value": "photos-ui=test",
                "domain": "photos.google.com",
                "path": "/",
                "expires": compass_expires,
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }
        )
    path.write_text(json.dumps({"cookies": cookies, "origins": []}))


class PhotosUiSessionNeedsRefreshTests(unittest.TestCase):
    def test_true_when_compass_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "google-storage.json"
            _write_storage_state(auth_file, compass_expires=None)
            self.assertTrue(photos_ui_session_needs_refresh(auth_file, now=1_000_000.0))

    def test_false_when_compass_expires_beyond_lead_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "google-storage.json"
            now = 1_000_000.0
            _write_storage_state(auth_file, compass_expires=now + 4 * 86400)
            self.assertFalse(photos_ui_session_needs_refresh(auth_file, now=now))

    def test_true_when_compass_expires_within_lead_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "google-storage.json"
            now = 1_000_000.0
            _write_storage_state(auth_file, compass_expires=now + 2 * 86400)
            self.assertTrue(photos_ui_session_needs_refresh(auth_file, now=now))

    def test_true_when_compass_already_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "google-storage.json"
            now = 1_000_000.0
            _write_storage_state(auth_file, compass_expires=now - 60)
            self.assertTrue(photos_ui_session_needs_refresh(auth_file, now=now))

    def test_days_remaining_from_compass_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "google-storage.json"
            now = 1_000_000.0
            _write_storage_state(auth_file, compass_expires=now + 10 * 86400)
            self.assertAlmostEqual(
                photos_ui_cookie_days_remaining(auth_file, now=now),
                10.0,
            )


class RefreshSessionTests(unittest.TestCase):
    @patch("immich_to_gphotos.google_photos.photos_ui_session_needs_refresh", return_value=True)
    @patch("immich_to_gphotos.google_photos.save_failure_screenshot")
    @patch("immich_to_gphotos.google_photos._save_storage_state")
    @patch("immich_to_gphotos.google_photos._require_signed_in")
    @patch("immich_to_gphotos.google_photos._wait_for_photos_app")
    @patch("immich_to_gphotos.google_photos.sync_playwright")
    def test_waits_for_photos_app_before_sign_in_check(
        self,
        mock_sync_playwright: MagicMock,
        mock_wait_for_photos_app: MagicMock,
        mock_require_signed_in: MagicMock,
        mock_save_storage_state: MagicMock,
        mock_save_failure_screenshot: MagicMock,
        mock_needs_refresh: MagicMock,
    ) -> None:
        auth_file = Path("/tmp/fake-google-storage.json")
        session = GooglePhotosSession(auth_file)
        call_order: list[str] = []
        mock_wait_for_photos_app.side_effect = lambda *args, **kwargs: call_order.append("wait")
        mock_require_signed_in.side_effect = lambda *args, **kwargs: call_order.append("require")

        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        playwright = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = playwright

        with patch.object(Path, "is_file", return_value=True):
            with patch.object(
                GooglePhotosSession,
                "_launch",
                return_value=(browser, context, page),
            ) as mock_launch:
                session.refresh_session()

        mock_launch.assert_called_once_with(playwright, headless=True, wait_after_goto_ms=0)
        mock_wait_for_photos_app.assert_called_once_with(page, timeout_ms=REFRESH_SESSION_WAIT_MS)
        mock_require_signed_in.assert_called_once_with(page)
        mock_save_storage_state.assert_called_once_with(context, auth_file)
        self.assertEqual(call_order, ["wait", "require"])
        mock_save_failure_screenshot.assert_not_called()

    @patch("immich_to_gphotos.google_photos.sync_playwright")
    @patch("immich_to_gphotos.google_photos.time.time")
    def test_skips_headless_refresh_when_compass_still_fresh(
        self,
        mock_time: MagicMock,
        mock_sync_playwright: MagicMock,
    ) -> None:
        now = 1_000_000.0
        mock_time.return_value = now
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "google-storage.json"
            _write_storage_state(auth_file, compass_expires=now + 10 * 86400)
            session = GooglePhotosSession(auth_file)

            with patch.object(sys, "stderr", new_callable=StringIO) as mock_stderr:
                session.refresh_session()

        mock_sync_playwright.assert_not_called()
        self.assertIn("valid for 10.0 more days", mock_stderr.getvalue())

    @patch("immich_to_gphotos.google_photos.photos_ui_session_needs_refresh", return_value=True)
    @patch("immich_to_gphotos.google_photos.save_failure_screenshot")
    @patch("immich_to_gphotos.google_photos._save_storage_state")
    @patch("immich_to_gphotos.google_photos._require_signed_in")
    @patch("immich_to_gphotos.google_photos._wait_for_photos_app")
    @patch("immich_to_gphotos.google_photos.sync_playwright")
    def test_wait_timeout_raises_google_photos_error(
        self,
        mock_sync_playwright: MagicMock,
        mock_wait_for_photos_app: MagicMock,
        mock_require_signed_in: MagicMock,
        mock_save_storage_state: MagicMock,
        mock_save_failure_screenshot: MagicMock,
        mock_needs_refresh: MagicMock,
    ) -> None:
        auth_file = Path("/tmp/fake-google-storage.json")
        session = GooglePhotosSession(auth_file)
        mock_wait_for_photos_app.side_effect = TimeoutError("still loading")

        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        playwright = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = playwright

        with patch.object(Path, "is_file", return_value=True):
            with patch.object(
                GooglePhotosSession,
                "_launch",
                return_value=(browser, context, page),
            ):
                with self.assertRaises(GooglePhotosError) as ctx:
                    session.refresh_session()

        self.assertIn("timed out waiting for Google Photos", str(ctx.exception))
        mock_save_failure_screenshot.assert_called_once_with(page)
        mock_require_signed_in.assert_not_called()
        mock_save_storage_state.assert_not_called()
        context.close.assert_called_once()
        browser.close.assert_called_once()

    @patch("immich_to_gphotos.google_photos.photos_ui_session_needs_refresh", return_value=True)
    @patch("immich_to_gphotos.google_photos.save_failure_screenshot")
    @patch("immich_to_gphotos.google_photos._save_storage_state")
    @patch("immich_to_gphotos.google_photos._require_signed_in")
    @patch("immich_to_gphotos.google_photos._wait_for_photos_app")
    @patch("immich_to_gphotos.google_photos.sync_playwright")
    def test_sign_in_failure_saves_screenshot(
        self,
        mock_sync_playwright: MagicMock,
        mock_wait_for_photos_app: MagicMock,
        mock_require_signed_in: MagicMock,
        mock_save_storage_state: MagicMock,
        mock_save_failure_screenshot: MagicMock,
        mock_needs_refresh: MagicMock,
    ) -> None:
        auth_file = Path("/tmp/fake-google-storage.json")
        session = GooglePhotosSession(auth_file)
        mock_require_signed_in.side_effect = GooglePhotosError(
            "Google session expired; run: immich-to-gphotos auth"
        )

        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        playwright = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = playwright

        with patch.object(Path, "is_file", return_value=True):
            with patch.object(
                GooglePhotosSession,
                "_launch",
                return_value=(browser, context, page),
            ):
                with self.assertRaises(GooglePhotosError) as ctx:
                    session.refresh_session()

        self.assertIn("Google session expired", str(ctx.exception))
        mock_save_failure_screenshot.assert_called_once_with(page)
        mock_save_storage_state.assert_not_called()
        context.close.assert_called_once()
        browser.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
