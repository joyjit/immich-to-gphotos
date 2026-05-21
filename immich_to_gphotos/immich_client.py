"""Immich REST API client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from immich_to_gphotos import exit_codes, log

MAX_ASSETS = 500


@dataclass(frozen=True)
class Album:
    id: str
    name: str


@dataclass(frozen=True)
class Asset:
    id: str
    original_file_name: str
    live_photo_video_id: str | None
    exif_info: dict[str, Any] | None


class ImmichError(Exception):
    """Immich API failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ImmichClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "Accept": "application/json",
            "x-api-key": api_key,
        }

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base,
            headers=self._headers,
            timeout=httpx.Timeout(120.0, connect=30.0),
            follow_redirects=True,
        )

    def list_albums(self) -> list[Album]:
        with self._client() as client:
            response = client.get("/api/albums")
            self._raise_for_status(response, "list albums")
            data = response.json()
        albums: list[Album] = []
        for item in data:
            name = item.get("albumName") or item.get("name") or ""
            album_id = item.get("id")
            if album_id and name:
                albums.append(Album(id=album_id, name=name))
        return albums

    def resolve_album(self, query: str) -> Album:
        matches = [a for a in self.list_albums() if a.name.casefold() == query.casefold()]
        if len(matches) == 0:
            raise ImmichError(f'Immich album "{query}" not found')
        if len(matches) > 1:
            names = ", ".join(f'"{m.name}"' for m in matches)
            raise ImmichError(f'Immich album "{query}" is ambiguous ({names})')
        return matches[0]

    def list_album_assets(self, album_id: str) -> list[Asset]:
        with self._client() as client:
            response = client.get(f"/api/albums/{album_id}")
            self._raise_for_status(response, "get album")
            data = response.json()
        raw_assets = data.get("assets") or []
        assets = [_parse_asset(item) for item in raw_assets]
        if len(assets) > MAX_ASSETS:
            raise ImmichError(
                f"album has {len(assets)} assets (limit {MAX_ASSETS}); "
                "split the album or raise the limit in code"
            )
        return assets

    def get_asset(self, asset_id: str) -> Asset:
        with self._client() as client:
            response = client.get(f"/api/assets/{asset_id}")
            self._raise_for_status(response, "get asset")
            return _parse_asset(response.json())

    def download_original(self, asset_id: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        headers = {**self._headers, "Accept": "application/octet-stream"}
        with self._client() as client:
            with client.stream(
                "GET",
                f"/api/assets/{asset_id}/original",
                headers=headers,
            ) as response:
                self._raise_for_status(response, f"download asset {asset_id}")
                with destination.open("wb") as handle:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        handle.write(chunk)

    @staticmethod
    def _raise_for_status(response: httpx.Response, action: str) -> None:
        if response.is_success:
            return
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = str(body.get("message") or body)
            else:
                detail = str(body)
        except Exception:
            detail = response.text[:200]
        raise ImmichError(
            f"Immich {action} failed ({response.status_code}): {detail}".strip(),
            status_code=response.status_code,
        )


def _parse_asset(item: dict[str, Any]) -> Asset:
    return Asset(
        id=item["id"],
        original_file_name=item.get("originalFileName") or f"{item['id']}.bin",
        live_photo_video_id=item.get("livePhotoVideoId"),
        exif_info=item.get("exifInfo"),
    )


def immich_exit_code(exc: ImmichError) -> int:
    msg = str(exc).lower()
    if "not found" in msg or "ambiguous" in msg:
        return exit_codes.IMMICH_ALBUM
    return exit_codes.IMMICH_API
