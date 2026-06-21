# immich-to-gphotos

Upload originals from an Immich album into an **existing** Google Photos album. Google has no public upload API; this tool uses Playwright with a saved browser session.

## Disclaimer

This is an **unofficial** personal automation tool. It is not affiliated with Google or Immich. Google’s web UI changes can break uploads at any time. The saved session file (`google-storage.json`) is equivalent to staying logged in—protect it like a password (`chmod 600`, never commit it, never paste it in issues).

## Requirements

- Python 3.11+
- [exiftool](https://exiftool.org/) on `PATH` (metadata embedding)
- **Google Chrome** or **Microsoft Edge** — Playwright drives the system browser, not a bundled Chromium download
- Immich API key with access to the source album

On Ubuntu:

```bash
sudo apt install google-chrome-stable
```

## Install

### pipx (recommended)

```bash
pipx install immich-to-gphotos
# or from GitHub:
# pipx install git+https://github.com/joyjit/immich-to-gphotos.git
# pipx install git+https://github.com/joyjit/immich-to-gphotos.git@v0.1.0
```

### From a clone (development)

```bash
git clone https://github.com/joyjit/immich-to-gphotos.git
cd immich-to-gphotos
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### First-time setup (every user)

```bash
mkdir -p ~/.local/share/immich-to-gphotos
cp immich.conf-example ~/.local/share/immich-to-gphotos/immich.conf
chmod 600 ~/.local/share/immich-to-gphotos/immich.conf
# Edit immich.conf: set IMMICH_API_KEY and IMMICH_URL
```

Config precedence: **CLI flags → environment → `immich.conf` → built-in defaults**.

## Usage

### One-time Google sign-in

```bash
immich-to-gphotos auth
```

A **system Chrome** window opens (profile under `~/.local/share/immich-to-gphotos/chrome-profile`). Sign in to the Google account that owns the target albums. The session is saved to `~/.local/share/immich-to-gphotos/google-storage.json` (override with `IMMICH_TO_GPHOTOS_AUTH_FILE`).

If Google shows **“This browser or app may not be secure”**, use your normal Chrome via remote debugging:

```bash
google-chrome --remote-debugging-port=9222 https://photos.google.com
# Sign in in that window, then:
immich-to-gphotos auth --cdp-url http://127.0.0.1:9222
```

`auth` needs a graphical session (local desktop or switch-user). Plain SSH without a display cannot open Chrome; use `--cdp-url` instead (see above).

### Upload

```bash
immich-to-gphotos upload \
  --immich-album "Trip 2024" \
  --google-album "Trip 2024"
```

Optional Immich URL override:

```bash
immich-to-gphotos upload \
  --immich-album "Trip 2024" \
  --google-album "Trip 2024" \
  --immich-url https://immich.example.com
```

Every `upload` run validates the Google session, even when Immich has nothing new:

- **New files** — upload to Google, then save refreshed cookies to `google-storage.json`.
- **Nothing new** — headless visit to Google Photos only; rewrite `google-storage.json` if still signed in.

If the session is missing or expired, `upload` exits **4** on every run (including idle cron runs). Previously, a run with no new files could exit **0** while the Google session was already dead.

## Behavior

| Topic | Behavior |
|-------|----------|
| Immich album | Case-insensitive name match; error if none or ambiguous |
| Asset limit | Fails if album has more than 500 assets |
| Dedup | By filename per Google album (`state/<slug>.json`) |
| RAW | Converted to JPEG before upload |
| HEIC/HEIF | Skipped with warning |
| Live Photo | Uploads image + motion video when Immich links them |
| exiftool errors | Warning only; file still uploaded |
| Google album | Must already exist (yours or shared with you); not created by this tool |
| Google session | Checked on every `upload`; cookies refreshed when signed in |

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Unexpected failure |
| 2 | Missing/invalid configuration |
| 3 | Immich album not found or ambiguous |
| 4 | Google auth / session expired |
| 5 | Immich API error |
| 6 | Google Photos / browser failure |

Logs go to stderr with journal-style prefixes, e.g. `<6>immich-to-gphotos: …`.

## Cron

Run `upload` on a schedule once `auth` has been done and `immich.conf` is in place. Re-run `auth` when uploads fail with exit code 4 (typically every few weeks, not on a fixed schedule).

Cron does not use your login `PATH`. Use the full path to the CLI, e.g. `/home/you/.local/bin/immich-to-gphotos`.

Each scheduled `upload` launches headless Chrome to validate or refresh the Google session. A frequent schedule (e.g. every 5 minutes) is fine on a small server but does add overhead when there is nothing to upload.

```cron
0 3 * * * /home/you/.local/bin/immich-to-gphotos upload --immich-album "Backup" --google-album "Backup" 2>&1 | logger -t immich-to-gphotos
```

## Limitations

- Filename dedup: two different Immich assets with the same basename in one Google album — only the first is uploaded.
- Google web UI changes may break automation.
- Shared albums: you must be allowed to add photos (collaboration enabled by the owner).
- HEIC/HEIF assets are never uploaded.
- Live Photo pairing on Google is best-effort.
