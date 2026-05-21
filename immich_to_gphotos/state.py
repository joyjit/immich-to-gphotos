"""Per-Google-album upload dedup state."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from immich_to_gphotos.config import ensure_private_dir


def album_slug(google_album: str) -> str:
    """Filesystem-safe slug from album name."""
    slug = google_album.casefold().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug or "album"


class UploadState:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._uploaded: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        names = data.get("uploaded") or data.get("filenames") or []
        if isinstance(names, list):
            self._uploaded = {str(n) for n in names}

    def is_uploaded(self, filename: str) -> bool:
        return filename in self._uploaded

    def mark_uploaded(self, filename: str) -> None:
        self._uploaded.add(filename)
        self._save()

    def _save(self) -> None:
        ensure_private_dir(self._path.parent)
        payload = {"uploaded": sorted(self._uploaded)}
        fd, tmp = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=f".{self._path.stem}-",
            suffix=".tmp",
        )
        try:
            with open(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
            Path(tmp).replace(self._path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
