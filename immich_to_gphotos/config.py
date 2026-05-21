"""Configuration: CLI flags → environment → config file → defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IMMICH_URL = "https://immich.example.com"
DEFAULT_DATA_DIR = Path.home() / ".local/share/immich-to-gphotos"
DEFAULT_STATE_DIR = DEFAULT_DATA_DIR / "state"
DEFAULT_TMP_DIR = DEFAULT_DATA_DIR / "tmp"
DEFAULT_AUTH_FILE = DEFAULT_DATA_DIR / "google-storage.json"
DEFAULT_IMMICH_CONFIG_FILE = DEFAULT_DATA_DIR / "immich.conf"
# Legacy single-line key file (read if immich.conf has no key)
LEGACY_IMMICH_API_KEY_FILE = DEFAULT_DATA_DIR / "immich-api-key"

PRIVATE_DIR_MODE = 0o700


def ensure_private_dir(path: Path) -> None:
    """Create a directory tree readable only by the owning user."""
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)


@dataclass(frozen=True)
class Config:
    immich_api_key: str
    immich_url: str
    state_dir: Path
    auth_file: Path


def _expand_path(value: str) -> Path:
    return Path(os.path.expanduser(value)).resolve()


def resolve_tmp_dir() -> Path:
    """Per-user temp base (downloads and failure screenshots)."""
    return _expand_path(
        os.environ.get("IMMICH_TO_GPHOTOS_TMP_DIR", str(DEFAULT_TMP_DIR))
    )


def _parse_config_file(path: Path) -> dict[str, str]:
    """Parse KEY=value lines; # comments and blank lines ignored."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            # Legacy: bare API key on one line
            if "IMMICH_API_KEY" not in values:
                values["IMMICH_API_KEY"] = line
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _read_legacy_api_key_file(path: Path) -> str:
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def load_config(
    *,
    immich_url: str | None = None,
    config_file: Path | str | None = None,
) -> Config:
    """Load config from environment, config file, and optional CLI overrides."""
    cfg_path = _expand_path(str(config_file or DEFAULT_IMMICH_CONFIG_FILE))
    file_values = _parse_config_file(cfg_path)

    api_key = os.environ.get("IMMICH_API_KEY", "").strip()
    if not api_key:
        api_key = file_values.get("IMMICH_API_KEY", "").strip()
    if not api_key:
        api_key = _read_legacy_api_key_file(LEGACY_IMMICH_API_KEY_FILE)
    if not api_key:
        raise ValueError(
            f"Immich API key not set (use {cfg_path}, IMMICH_API_KEY, or --config-file)"
        )

    url = (
        immich_url
        or os.environ.get("IMMICH_URL", "").strip()
        or file_values.get("IMMICH_URL", "").strip()
        or DEFAULT_IMMICH_URL
    ).rstrip("/")

    state_dir = _expand_path(
        os.environ.get("IMMICH_TO_GPHOTOS_STATE_DIR", str(DEFAULT_STATE_DIR))
    )
    auth_file = _expand_path(
        os.environ.get("IMMICH_TO_GPHOTOS_AUTH_FILE", str(DEFAULT_AUTH_FILE))
    )

    return Config(
        immich_api_key=api_key,
        immich_url=url,
        state_dir=state_dir,
        auth_file=auth_file,
    )
