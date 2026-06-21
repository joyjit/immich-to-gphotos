"""End-to-end upload pipeline."""

from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

from immich_to_gphotos import log
from immich_to_gphotos.config import Config, ensure_private_dir, resolve_tmp_dir
from immich_to_gphotos.google_photos import (
    GooglePhotosError,
    GooglePhotosSession,
    clear_failure_screenshots,
)
from immich_to_gphotos.immich_client import Asset, ImmichClient, ImmichError
from immich_to_gphotos.state import UploadState, album_slug
from immich_to_gphotos.transform import prepare_upload_path


def run_upload(
    config: Config,
    *,
    immich_album: str,
    google_album: str,
) -> None:
    client = ImmichClient(config.immich_url, config.immich_api_key)
    album = client.resolve_album(immich_album)
    log.info(f'using Immich album "{album.name}" ({album.id})')

    assets = client.list_album_assets(album.id)
    log.info(f"found {len(assets)} asset(s) in album")

    state_path = config.state_dir / f"{album_slug(google_album)}.json"
    state = UploadState(state_path)

    tmp_base = resolve_tmp_dir()
    ensure_private_dir(tmp_base)
    tmp_root = tmp_base / str(uuid.uuid4())
    ensure_private_dir(tmp_root)
    log.info(f"downloading to {tmp_root}")

    try:
        paths_to_upload = _prepare_files(client, assets, state, tmp_root)
        google = GooglePhotosSession(config.auth_file)

        if paths_to_upload:
            log.info(f"uploading {len(paths_to_upload)} file(s) to Google album \"{google_album}\"")
            google.upload_files(google_album, paths_to_upload)
            for path in paths_to_upload:
                state.mark_uploaded(path.name)
                log.info(f'uploaded {path.name} to album "{google_album}"')
        else:
            log.info("no new files to upload")
            google.refresh_session()

        clear_failure_screenshots()
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
        if sys.exc_info()[0] is KeyboardInterrupt:
            clear_failure_screenshots()


def _prepare_files(
    client: ImmichClient,
    assets: list[Asset],
    state: UploadState,
    work_dir: Path,
) -> list[Path]:
    paths: list[Path] = []
    asset_by_id = {a.id: a for a in assets}

    for asset in assets:
        basename = Path(asset.original_file_name).name
        if state.is_uploaded(basename):
            log.info(f"skipping {basename} (already uploaded)")
            continue

        local = work_dir / basename
        log.info(f"downloading {basename}")
        client.download_original(asset.id, local)

        ready = prepare_upload_path(
            local,
            exif_info=asset.exif_info,
            work_dir=work_dir,
        )
        if ready is not None:
            paths.append(ready)

        if asset.live_photo_video_id:
            video_asset = asset_by_id.get(asset.live_photo_video_id)
            if video_asset is None:
                video_asset = _fetch_video_asset(client, asset.live_photo_video_id)
            if video_asset:
                vname = Path(video_asset.original_file_name).name
                if not state.is_uploaded(vname):
                    vlocal = work_dir / vname
                    log.info(f"downloading live photo video {vname}")
                    client.download_original(video_asset.id, vlocal)
                    vready = prepare_upload_path(
                        vlocal,
                        exif_info=video_asset.exif_info,
                        work_dir=work_dir,
                    )
                    if vready is not None:
                        paths.append(vready)

    return paths


def _fetch_video_asset(client: ImmichClient, asset_id: str) -> Asset | None:
    """Best-effort fetch for live-photo companion not in album list."""
    try:
        return client.get_asset(asset_id)
    except ImmichError:
        log.warning(f"could not fetch live photo video asset {asset_id}")
        return None
