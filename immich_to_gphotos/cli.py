"""CLI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from immich_to_gphotos import __version__, exit_codes, log
from immich_to_gphotos.config import DEFAULT_IMMICH_CONFIG_FILE, load_config
from immich_to_gphotos.google_photos import GooglePhotosError, GooglePhotosSession
from immich_to_gphotos.immich_client import ImmichError, immich_exit_code
from immich_to_gphotos.pipeline import run_upload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="immich-to-gphotos",
        description="Upload Immich album originals to Google Photos",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config-file",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"Immich config file (default: {DEFAULT_IMMICH_CONFIG_FILE})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    auth_parser = sub.add_parser("auth", help="Sign in to Google and save browser session")
    auth_parser.add_argument(
        "--cdp-url",
        default=None,
        help="Attach to Chrome started with --remote-debugging-port=9222 (bypasses Google bot block)",
    )

    upload_parser = sub.add_parser("upload", help="Download from Immich and upload to Google Photos")
    upload_parser.add_argument("--immich-album", required=True, help="Immich album name")
    upload_parser.add_argument("--google-album", required=True, help="Existing Google Photos album name")
    upload_parser.add_argument(
        "--immich-url",
        default=None,
        help="Immich server URL (overrides config file and IMMICH_URL)",
    )

    args = parser.parse_args(argv)

    try:
        config = load_config(
            immich_url=getattr(args, "immich_url", None),
            config_file=args.config_file,
        )
    except ValueError as exc:
        log.error(str(exc))
        return exit_codes.CONFIG

    try:
        if args.command == "auth":
            GooglePhotosSession(config.auth_file).run_auth(cdp_url=args.cdp_url)
            return exit_codes.SUCCESS
        if args.command == "upload":
            run_upload(
                config,
                immich_album=args.immich_album,
                google_album=args.google_album,
            )
            return exit_codes.SUCCESS
    except ImmichError as exc:
        log.error(str(exc))
        return immich_exit_code(exc)
    except GooglePhotosError as exc:
        log.error(str(exc))
        msg = str(exc).lower()
        if any(
            token in msg
            for token in (
                "session",
                "expired",
                "not found at",
                "sign-in",
                "timed out waiting",
            )
        ):
            return exit_codes.GOOGLE_AUTH
        if "not found" in msg:
            return exit_codes.GOOGLE_PHOTOS
        return exit_codes.GOOGLE_PHOTOS
    except Exception as exc:
        log.error(f"unexpected error: {exc}")
        return exit_codes.UNEXPECTED

    return exit_codes.UNEXPECTED


if __name__ == "__main__":
    sys.exit(main())
