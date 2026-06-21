"""Tests for GooglePhotosSession.refresh_session."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_to_gphotos.google_photos import (
    REFRESH_SESSION_WAIT_MS,
    GooglePhotosError,
    GooglePhotosSession,
)


class RefreshSessionTests(unittest.TestCase):
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
