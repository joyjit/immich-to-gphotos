"""Download transforms: RAW→JPEG, HEIC skip, exiftool metadata."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import rawpy
from PIL import Image

from immich_to_gphotos import log

RAW_EXTENSIONS = {
    ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw",
}
HEIC_EXTENSIONS = {".heic", ".heif"}


def is_heic(path: Path) -> bool:
    return path.suffix.casefold() in HEIC_EXTENSIONS


def is_raw(path: Path) -> bool:
    return path.suffix.casefold() in RAW_EXTENSIONS


def raw_to_jpeg(source: Path, destination: Path) -> Path:
    """Convert RAW to JPEG; return destination path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with rawpy.imread(str(source)) as raw:
        rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=False)
    image = Image.fromarray(rgb.astype(np.uint8))
    image.save(destination, "JPEG", quality=92)
    return destination


def _exiftool_datetime(value: Any) -> str | None:
    """Convert Immich ISO timestamps to exiftool format (YYYY:MM:DD HH:MM:SS)."""
    if not value:
        return None
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def embed_metadata(path: Path, exif_info: dict[str, Any] | None) -> None:
    """Embed Immich metadata with exiftool; warn and continue on failure."""
    if not exif_info:
        return
    args = ["exiftool", "-overwrite_original", "-P"]
    dt = _exiftool_datetime(
        exif_info.get("dateTimeOriginal") or exif_info.get("modifyDate")
    )
    if dt:
        args.extend([f"-DateTimeOriginal={dt}", f"-CreateDate={dt}"])
    lat = exif_info.get("latitude")
    lon = exif_info.get("longitude")
    if lat is not None and lon is not None:
        args.append(f"-GPSLatitude={lat}")
        args.append(f"-GPSLongitude={lon}")
    desc = exif_info.get("description")
    if desc:
        args.append(f"-ImageDescription={desc}")
        args.append(f"-Description={desc}")
    if len(args) <= 3:
        return
    args.append(str(path))
    try:
        subprocess.run(args, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        log.warning("exiftool not found; uploading without embedded metadata")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        log.warning(
            f"exiftool failed for {path.name}"
            + (f": {detail}" if detail else "")
            + ", uploading without embedded metadata"
        )


def prepare_upload_path(
    downloaded: Path,
    *,
    exif_info: dict[str, Any] | None,
    work_dir: Path,
) -> Path | None:
    """
    Return local path ready for Google upload, or None if asset should be skipped (HEIC).
    """
    if is_heic(downloaded):
        log.warning(f"skipping HEIC/HEIF asset {downloaded.name}")
        return None

    upload_path = downloaded
    if is_raw(downloaded):
        jpeg_name = re.sub(r"\.[^.]+$", ".jpg", downloaded.name, flags=re.IGNORECASE)
        upload_path = work_dir / jpeg_name
        log.info(f"converting RAW {downloaded.name} to JPEG")
        raw_to_jpeg(downloaded, upload_path)

    embed_metadata(upload_path, exif_info)
    return upload_path
