# YTDLManager — Local Social Media Download Manager

A local desktop application for downloading videos and playlists from
**YouTube, TikTok, Twitter/X, Instagram, Facebook, and Vimeo** that you own
or are otherwise legally authorised to save. Built with Python,
[PySide6](https://doc.qt.io/qtforpython-6/) (Qt 6) for the interface,
[yt-dlp](https://github.com/yt-dlp/yt-dlp) for media extraction, and
[FFmpeg](https://ffmpeg.org/) for format merging/conversion. All processing
and storage happen entirely on your machine — no analytics, no telemetry, no
credential collection, no ads.

**By default this app only accesses public content and never logs in to any
platform.** The one exception is opt-in: if YouTube starts blocking your
network ("Sign in to confirm you're not a bot"), you can let YouTube requests
use your own browser's signed-in YouTube session — see
[When YouTube says "confirm you're not a bot"](#when-youtube-says-confirm-youre-not-a-bot).
Nothing else ever uses a login (see [Known limitations](#known-limitations) —
some content, especially on Instagram and Vimeo, requires a login this app
deliberately doesn't support).

## Legal notice

Only download content you own, that is in the public domain, that carries a
licence permitting download, or for which you otherwise have the rights
holder's explicit permission. This application does not implement DRM
circumvention, CAPTCHA bypassing, paywall bypassing, or any other
access-control evasion. It uses no logins or cookies unless you turn on
YouTube sign-in in Settings, in which case YouTube requests (and only those)
use your browser's signed-in YouTube session. It cannot guarantee that every
item on any supported platform is legally or technically downloadable. You are responsible for complying with applicable
law, each platform's Terms of Service, and any content licence. This notice
is also shown inside the application at all times.

## Contents

- [Supported platforms](#supported-platforms)
- **Get started** — pick the guide for your computer, each one complete on
  its own, start to finish:
  - [macOS](#get-started-on-macos)
  - [Windows](#get-started-on-windows)
  - [Linux](#get-started-on-linux)
- [Using the app](#using-the-app) (pause/resume, subtitles, updates)
- [Architecture & why](#architecture--why)
- [Development commands (all platforms)](#development-commands-all-platforms)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [How packaging works](#how-packaging-works)
- [Dependency licences](#dependency-licences)
- [Known limitations](#known-limitations)

## Supported platforms

The app recognises URLs from six platforms. Video-vs-playlist classification
is done locally by [`url_utils.py`](src/ytdlmanager/url_utils.py) (no
network call needed just to validate a pasted URL); the actual extraction
and download always goes through yt-dlp, which does its own, much more
thorough site handling.

| Platform | Single item | Collection ("Playlist" tab) | Real-world notes (verified live against public content) |
|---|---|---|---|
| YouTube | video | playlist | Fully supported; the platform this app was originally built around. |
| TikTok | `/@user/video/123...`, shortened share links | `/@user` (profile) | Verified working end-to-end for public videos. |
| Twitter/X | `/user/status/123...` | `/user` (timeline) | Verified working end-to-end for public posts. Very old tweets using legacy embed formats can fail — that's a yt-dlp/Twitter-side limitation, not this app's. |
| Instagram | `/p/`, `/reel/`, `/reels/`, `/tv/` | `/username` (profile) | Some public posts work as-is; many others (and most profile/collection fetches) return "login required" — Instagram increasingly gates access even to public content. Handled gracefully with a clear error, not a crash or a silent retry loop. |
| Facebook | `/watch/?v=`, `/user/videos/id`, `fb.watch/...` | *(not supported — see below)* | Facebook's page structure changes often and yt-dlp's extractor is one of its more fragile ones; expect some videos to fail with a parsing error unrelated to permissions. No playlist/bulk equivalent is exposed for Facebook (no reliable "download this page's videos" URL shape to build on). |
| Vimeo | numeric `/123456789` | `/album/`, `/showcase/`, `/channels/` | Classification and routing work correctly, but **every Vimeo video tested while building this** returned "The web client only works when logged-in" — as of now, Vimeo support is effectively login-only in practice, even though this app doesn't add any Vimeo-specific restriction. Kept in because that could change (Vimeo's own policy, or a yt-dlp update), but don't expect it to work today. |

This app never prompts for credentials, and the only browser-cookie use is
the opt-in YouTube sign-in (YouTube URLs only) — see
[Legal notice](#legal-notice). If a URL from a platform above still fails,
upgrading yt-dlp (`pip install -U "yt-dlp[default,deno]"`, inside the
virtual environment from whichever guide below you used) is the first thing
to try before assuming it's unsupported.

---

# Get started on macOS

This section alone is everything you need — install, run, and (optionally)
build a double-clickable app — without looking at any other section.

### 1. Open Terminal

Press `Cmd + Space`, type `Terminal`, press Enter. Every command below gets
typed into this window, one at a time, followed by Enter.

### 2. Check whether you have Python

```bash
python3 --version
```

If that prints something like `Python 3.11.4`, you're set — skip to step 3.
If it says "command not found" or shows a version below 3.10, install
Python first. The easiest way on macOS is with [Homebrew](https://brew.sh/)
(a package manager for macOS — if you don't have it, that site has a
one-line install command):

```bash
brew install python@3.12
```

### 3. Install FFmpeg

The app uses FFmpeg (a free, open-source tool) to combine video and audio
and to convert formats. Install it the same way:

```bash
brew install ffmpeg
```

### 4. Get the project onto your machine

If you already have the project folder (e.g. you downloaded/cloned it),
`cd` into it:

```bash
cd path/to/Downloader
```

(Replace `path/to/Downloader` with wherever the folder actually is — you can
type `cd ` (with a trailing space) and then drag the folder from Finder
into the Terminal window to auto-fill the path.)

### 5. Create a virtual environment and install the app

A "virtual environment" is an isolated folder for this project's Python
packages, so they don't clash with anything else on your system. Create and
activate one, then install:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

You'll know the virtual environment is active because your prompt now
starts with `(.venv)`. You need to run `source .venv/bin/activate` again
every time you open a **new** Terminal window for this project.

### 6. Run the app

```bash
python -m ytdlmanager
```

The app window should open. Paste a video/playlist URL, pick a format, and
download — see the [Supported platforms](#supported-platforms) table above
for what's recognised.

### 7. (Optional) Build a double-clickable app, so you don't need Terminal again

```bash
cd packaging
pyinstaller pyinstaller.spec --noconfirm
```

This produces `packaging/dist/YTDLManager.app`. Copy it into your
Applications folder to make it launchable from Spotlight, Launchpad, or
Finder like any other Mac app:

```bash
cp -R dist/YTDLManager.app /Applications/
```

Then open it from Launchpad or Spotlight (`Cmd + Space`, type
"YTDLManager"). **First launch only:** macOS will likely warn that the app
is from an "unidentified developer" (it isn't notarised/signed by an Apple
developer account). Right-click the app → **Open** → confirm in the dialog
that appears. You only need to do this once.

If you ever change the source code and want the double-clickable app to
reflect it, redo step 7 (rebuild) and re-copy it over the old one in
Applications.

**That's the whole macOS setup.** For running the test suite or other
development commands, see [Development commands](#development-commands-all-platforms).
If something goes wrong, check [Troubleshooting](#troubleshooting).

---

# Get started on Windows

This section alone is everything you need — install, run, and (optionally)
build a double-clickable app — without looking at any other section.
Commands go into **PowerShell** (search "PowerShell" in the Start menu and
open it — not "Windows PowerShell (x86)", the regular one is fine).

### 1. Check whether you have Python

```powershell
python --version
```

If that prints something like `Python 3.11.4`, skip to step 3. If it says
it's not recognised, or shows a version below 3.10, install Python first.

### 2. Install Python (only if step 1 said you need to)

```powershell
winget install Python.Python.3.12
```

If `winget` itself isn't recognised (older Windows versions don't have it
built in), instead download the installer from
[python.org/downloads](https://www.python.org/downloads/) and run it —
**important:** on the first setup screen, tick **"Add python.exe to PATH"**
before clicking Install, or the commands below won't find Python.

After installing, close and reopen PowerShell, then re-run
`python --version` to confirm it now works.

### 3. Install FFmpeg

The app uses FFmpeg (a free, open-source tool) to combine video and audio
and to convert formats.

```powershell
winget install Gyan.FFmpeg
```

Close and reopen PowerShell afterward so it picks up the change, then
confirm it worked:

```powershell
ffmpeg -version
```

If `winget` isn't available, use [Chocolatey](https://chocolatey.org/install)
instead (`choco install ffmpeg`), or download a build from
[ffmpeg.org](https://ffmpeg.org/download.html) and add its `bin` folder to
your PATH manually.

### 4. Get the project onto your machine

If you already have the project folder, move into it:

```powershell
cd path\to\Downloader
```

(Replace `path\to\Downloader` with wherever the folder actually is.)

### 5. Create a virtual environment and install the app

A "virtual environment" is an isolated folder for this project's Python
packages, so they don't clash with anything else on your system. Create and
activate one, then install:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

You'll know the virtual environment is active because your prompt now
starts with `(.venv)`. You need to run `.venv\Scripts\activate` again every
time you open a **new** PowerShell window for this project.

If activation is blocked with a message about "running scripts is
disabled", run this once (it only relaxes the restriction for your own
scripts, not system-wide) and try activating again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 6. Run the app

```powershell
python -m ytdlmanager
```

The app window should open. Paste a video/playlist URL, pick a format, and
download — see the [Supported platforms](#supported-platforms) table above
for what's recognised.

### 7. (Optional) Build a double-clickable app, so you don't need PowerShell again

```powershell
cd packaging
pyinstaller pyinstaller.spec --noconfirm
```

This produces `packaging\dist\YTDLManager\YTDLManager.exe` — it's already
directly double-clickable from File Explorer, no extra install step
required. For a Desktop shortcut instead of navigating into that folder
every time:

```powershell
powershell -ExecutionPolicy Bypass -File install_windows.ps1
```

(Run that from inside the `packaging` folder, same as above.) Then
right-click the new Desktop shortcut and choose **Pin to Start** or **Pin to
taskbar** for one-click access.

If you ever change the source code and want the app to reflect it, redo
step 7 (rebuild) — the shortcut keeps pointing at the same file, so you
don't need to recreate it.

**That's the whole Windows setup.** For running the test suite or other
development commands, see [Development commands](#development-commands-all-platforms).
If something goes wrong, check [Troubleshooting](#troubleshooting).

---

# Get started on Linux

This section alone is everything you need — install, run, and (optionally)
build a double-clickable app — without looking at any other section.
Commands go into a terminal (exact app name depends on your desktop
environment — GNOME Terminal, Konsole, xterm, etc.). Examples below are
given for Debian/Ubuntu (`apt`), Fedora (`dnf`), and Arch (`pacman`) — use
whichever matches your distribution.

### 1. Check whether you have Python

```bash
python3 --version
```

If that prints something like `Python 3.11.4` (3.10 or newer), skip to step
3. Most modern distributions already ship a recent enough Python.

### 2. Install Python (only if step 1 said you need to)

```bash
# Debian/Ubuntu
sudo apt update && sudo apt install python3 python3-venv python3-pip

# Fedora
sudo dnf install python3 python3-pip

# Arch
sudo pacman -S python python-pip
```

### 3. Install FFmpeg

The app uses FFmpeg (a free, open-source tool) to combine video and audio
and to convert formats.

```bash
# Debian/Ubuntu
sudo apt install ffmpeg

# Fedora
sudo dnf install ffmpeg

# Arch
sudo pacman -S ffmpeg
```

### 4. Get the project onto your machine

If you already have the project folder, move into it:

```bash
cd path/to/Downloader
```

(Replace `path/to/Downloader` with wherever the folder actually is.)

### 5. Create a virtual environment and install the app

A "virtual environment" is an isolated folder for this project's Python
packages, so they don't clash with anything else on your system. Create and
activate one, then install:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

You'll know the virtual environment is active because your prompt now
starts with `(.venv)`. You need to run `source .venv/bin/activate` again
every time you open a **new** terminal window for this project.

If you're on Debian/Ubuntu and step 2 was skipped but this step fails with
an error mentioning `ensurepip` or `venv`, you're missing the
`python3-venv` package — install it with `sudo apt install python3-venv`
and try again.

### 6. Run the app

```bash
python -m ytdlmanager
```

The app window should open. Paste a video/playlist URL, pick a format, and
download — see the [Supported platforms](#supported-platforms) table above
for what's recognised.

### 7. (Optional) Build a double-clickable app, so you don't need a terminal again

```bash
cd packaging
pyinstaller pyinstaller.spec --noconfirm
```

This produces `packaging/dist/YTDLManager/YTDLManager` (a plain program
file, not yet in your application menu). Install it into your application
menu with the included script — this only touches your own home folder
(the program goes in `~/.local/opt/ytdlmanager`, the menu entry in
`~/.local/share/applications`), no `sudo` needed:

```bash
./install_linux.sh
```

YTDLManager should now show up in your application menu (GNOME Activities,
KDE's app launcher, etc.) like any other installed app — search for it and
click to launch. If it doesn't appear immediately, log out and back in
once.

If you ever change the source code and want the app to reflect it, redo
step 7 (rebuild, then re-run `./install_linux.sh` — it overwrites the
previous install, and never touches your settings, history, or queue, which
live separately in `~/.local/share/ytdlmanager`).

**That's the whole Linux setup.** For running the test suite or other
development commands, see [Development commands](#development-commands-all-platforms).
If something goes wrong, check [Troubleshooting](#troubleshooting).

---

## Using the app

### Pausing and resuming downloads

Select one or more items in the **Queue** tab and use **Pause Selected** /
**Resume Selected**:

- **Pause** stops the transfer but keeps the partial file on disk. Resuming
  continues from where it left off via HTTP range requests (yt-dlp's normal
  resume mechanism) rather than starting over.
- **Cancel** (the other button) stops the transfer *and* deletes the partial
  file — use it when you don't want that item at all.

Closing the app while downloads are active behaves the same way: confirming
the close prompt marks active items **Paused** (not cancelled), and they
pick up again next launch once you start them — nothing resumes
automatically on its own, by design, so a launch never surprises you with a
burst of network activity.

### Downloading subtitles

The **Video** and **Playlist** tabs have a **Subtitles** option:

- **None** (default) — nothing changes from before.
- **Save alongside the download** — saves `.srt` subtitle files next to the
  video or audio, named like `My Video.en.srt`.
- **Subtitles only** — fetches just the subtitle file(s), no video or audio
  (the Mode/Format options are greyed out since they don't apply).

Type the languages you want in the box beside it — codes separated by
commas (`en, fr`) or `all`. If a video has no human-made track in a
language, auto-generated captions are used when the platform offers them
(not with `all`, which would otherwise pull dozens of machine-translated
tracks). If a video has no subtitles in the languages you asked for, a
subtitles-only item fails right away with a clear message instead of
retrying. Subtitle conversion to `.srt` uses FFmpeg, which you already
need for the rest of the app. Availability depends on the platform:
YouTube is the most reliable; many TikTok/Instagram/X videos simply have no
separate subtitle track.

### Checking for yt-dlp updates

Every supported platform changes its site often in ways that break
extraction, and yt-dlp ships fixes frequently (sometimes daily). The app
checks PyPI for a newer yt-dlp on startup by default (a plain, anonymous
version lookup — no personal data leaves your machine) and, if one is
available, mentions it quietly in the status bar. Click **Check for
Updates** any time for a full report and, if you're running from source
(not a packaged build), the option to update in place with a single click.
Packaged builds are a frozen snapshot and can't self-update — the dialog
tells you to rebuild or run from source instead when that's the case.
Turn the startup check off in **Settings** if you'd rather not check
automatically.

### When YouTube says "confirm you're not a bot"

YouTube blocks a network address with *"Sign in to confirm you're not a
bot"* when it sees a lot of downloading from it in a short time — a big
playlist is the usual trigger, and a shared or VPN address makes it more
likely. It's tied to your network, not to the video, so updating yt-dlp
doesn't help and retrying makes it worse. When it happens the app:

- doesn't retry that download (each retry is another request from the
  blocked address), and
- pauses the remaining queued YouTube items and tells you, instead of
  letting every one of them hit the block in turn. Other platforms keep
  going.

You then have two choices:

1. **Wait** — the block usually lifts after a few hours, sometimes a day.
   Then select the paused items and use **Resume Selected** (or **Start
   All**), and **Retry Selected** for the one that failed.
2. **Turn on YouTube sign-in** — in **Settings → YouTube sign-in from**, pick
   a browser you're signed in to YouTube with. YouTube requests then use
   that browser's signed-in session, which YouTube doesn't block the same
   way. It's off by default, and the cookies are only ever sent to YouTube,
   never to the other platforms. On macOS the first download asks for
   Keychain access (choose **Always Allow**); Safari additionally needs the
   app to have Full Disk Access (System Settings → Privacy & Security).
   YouTube can rate-limit or temporarily restrict an account used this way,
   so a secondary Google account is the safer choice.

Either way, lowering **Concurrent downloads** in Settings to 1–2 for large
playlists makes it less likely to happen again.

## Architecture & why

| Concern | Choice | Why |
|---|---|---|
| UI toolkit | **PySide6** (official Qt for Python) | Native look on Windows/macOS/Linux, mature accessibility support (screen-reader labels, keyboard navigation, focus rings), LGPL-licensed so it can be used in this MIT-licensed project without relicensing, and it ships prebuilt wheels for all three target OSes. |
| Media extraction/download | **yt-dlp** (library, not shelled out to) | Actively maintained, handles each supported platform's frequently-changing extraction logic, format negotiation, and post-processing so we don't reimplement any of that ourselves — this is also *why* adding TikTok/Twitter/Instagram/Facebook/Vimeo on top of YouTube was mostly a matter of generalising URL classification, not touching the download engine at all. |
| Format merge / audio extraction | **FFmpeg** (external binary, detected at runtime) | Industry-standard, already what yt-dlp expects; not bundled (see [How packaging works](#how-packaging-works) for why). |
| Concurrency | `QThreadPool` / `QRunnable` per download, signals for progress | Keeps all download and metadata-fetch work off the GUI thread while integrating naturally with Qt's event loop; bounded by a configurable `setMaxThreadCount`. |
| Persistence | **SQLite** via the stdlib `sqlite3` | Single-user local app with modest write volume — a file-based DB avoids the operational overhead of a server, and needs no extra dependency. |
| App data location | Per-OS convention, no extra dependency | `%APPDATA%\YTDLManager` (Windows), `~/Library/Application Support/YTDLManager` (macOS), `$XDG_DATA_HOME/ytdlmanager` or `~/.local/share/ytdlmanager` (Linux). |

The smallest complete version was built first (URL validation → metadata
fetch → single download → queue → history) and then extended with playlist
selection, retry/backoff, duplicate handling, and settings persistence.

## Development commands (all platforms)

These assume you've already done the "Get started" setup for your OS above
(virtual environment created and activated). The only difference between
platforms is how you activate the virtual environment in a new terminal —
everything else below is identical:

- macOS/Linux: `source .venv/bin/activate`
- Windows (PowerShell): `.venv\Scripts\activate`

```bash
# Full mocked/unit test suite (no network access, safe to run repeatedly)
pytest

# With coverage
pytest --cov=ytdlmanager --cov-report=term-missing

# Lint
ruff check src tests

# Type check
mypy src/ytdlmanager
```

All tests above run entirely against mocks and fixtures — none of them
download real media, per the project's testing requirements.

### Optional real-network integration test

One additional test exercises the full yt-dlp + FFmpeg pipeline against a
real, tiny, Creative-Commons-licensed video ("Big Buck Bunny", Blender
Foundation, CC BY 3.0 — also yt-dlp's own extractor test fixture). It is
skipped by default and only runs when explicitly requested, so normal test
runs never repeatedly download media:

```bash
# macOS/Linux
YTDLMANAGER_RUN_INTEGRATION_TESTS=1 pytest tests/test_integration_real_download.py -v
```

```powershell
# Windows (PowerShell)
$env:YTDLMANAGER_RUN_INTEGRATION_TESTS = "1"
pytest tests/test_integration_real_download.py -v
```

### Keeping yt-dlp up to date

Every supported platform changes its site frequently in ways that break
extraction, and yt-dlp ships fixes often (sometimes daily). The running app
already checks for this itself — see
[Checking for yt-dlp updates](#checking-for-yt-dlp-updates) — but you can
always do it directly from the command line too (with the virtual
environment activated):

```bash
pip install -U "yt-dlp[default,deno]"
```

The two extras matter for YouTube: `default` brings yt-dlp's challenge
solver (`yt-dlp-ejs`) and `deno` the JavaScript runtime that runs it.
Without a JavaScript runtime yt-dlp falls back to a single limited YouTube
client, which YouTube increasingly refuses. The app finds Deno on its own
(from the virtual environment, a packaged build's bundled copy, or a
Homebrew/`~/.deno` install) and logs which one it's using at startup.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "FFmpeg not found" warning on startup | Install FFmpeg (see the "Get started" guide for your OS above). On macOS specifically, apps launched by double-click/Spotlight/Dock don't inherit your shell's PATH the way Terminal does, so a Homebrew-installed FFmpeg that works fine in Terminal can still be reported missing this way — the app also checks common install locations directly (`/opt/homebrew/bin`, `/usr/local/bin`, etc.) as a fallback for exactly this case, so this should only come up if FFmpeg genuinely isn't installed anywhere expected. |
| "That doesn't look like a valid video or playlist URL for a supported platform" | Only YouTube/TikTok/Twitter-X/Instagram/Facebook/Vimeo `http(s)` URLs in a recognised shape are accepted; check for typos, or see [Supported platforms](#supported-platforms) for the exact URL shapes each site needs. |
| "This content requires a logged-in account..." | Expected and by design — see [Legal notice](#legal-notice). Common on Instagram and (currently) on almost all of Vimeo; the app will never prompt for or store credentials to work around it. |
| "YouTube is blocking requests from this network" / "Sign in to confirm you're not a bot" | YouTube has throttled your network address, usually after heavy downloading. See [When YouTube says "confirm you're not a bot"](#when-youtube-says-confirm-youre-not-a-bot). |
| "Couldn't read YouTube sign-in cookies from …" | YouTube sign-in is on but the app can't read that browser's cookies. On macOS, choose **Always Allow** when asked for Keychain access; for Safari, give the app Full Disk Access. Or pick a different browser in Settings. |
| A video fails immediately and never retries | It was classified as a **permanent** failure (private, removed, age-restricted, region-blocked, copyright claim, login-required, etc.) — these are not retried since no amount of retrying fixes them. Check the error message in the Queue or History tab. |
| A video fails after a few retries with a timeout-like message | Likely a **transient** network issue — check your connection and use "Retry Selected" in the Queue tab. |
| Downloads didn't resume after restarting the app | This is intentional: interrupted downloads are restored as **Paused**, not auto-resumed, so nothing starts without your explicit action. Use "Start All" or select items and start them. |
| "Insufficient storage" / download fails partway | The app does a best-effort free-space check before starting; if a download still fails mid-transfer, free up space and retry. |
| App won't close / asks to confirm | Downloads are still active. Confirming exits anyway (active items become Paused and can be resumed next launch); cancelling the dialog lets you wait or cancel downloads first. |
| Logs | Written to the same per-OS app data directory as the database — see the log path printed at startup, e.g. `~/Library/Application Support/YTDLManager/ytdlmanager.log` on macOS. |
| yt-dlp errors mentioning signature extraction, "unable to extract", or "cannot parse data" | The platform changed something and your yt-dlp is stale — upgrade it: `pip install -U "yt-dlp[default,deno]"` (see [Keeping yt-dlp up to date](#keeping-yt-dlp-up-to-date)). Facebook in particular breaks this way often. |
| yt-dlp warns "No supported JavaScript runtime could be found" | Deno is missing: `pip install -U "yt-dlp[default,deno]"` in the virtual environment (packaged builds bundle it). |

## Project layout

```
Downloader/
├── README.md
├── LICENSE
├── pyproject.toml            # package metadata, dependencies, tool config
├── requirements.txt          # runtime deps (mirrors pyproject)
├── requirements-dev.txt      # + test/lint/type-check/packaging tools
├── .gitignore
├── src/
│   └── ytdlmanager/
│       ├── __init__.py
│       ├── __main__.py           # `python -m ytdlmanager` entry point
│       ├── app.py                # bootstraps logging, DB, settings, Qt event loop
│       ├── config.py             # per-OS app-data paths, constants, legal notice text
│       ├── models.py             # typed dataclasses/enums (VideoInfo, QueueItem, ...)
│       ├── db.py                 # SQLite persistence: settings, history, queue
│       ├── url_utils.py          # per-platform URL validation & video/playlist classification
│       ├── sanitize.py           # cross-platform filename sanitisation
│       ├── ffmpeg_utils.py       # FFmpeg detection + install instructions
│       ├── js_runtime.py         # Deno (JavaScript runtime for YouTube) detection
│       ├── ytdlp_backend.py      # yt-dlp wrapper: metadata extraction, download, error classification
│       ├── retry.py              # bounded exponential backoff
│       ├── download_manager.py   # queue orchestration, QThreadPool workers, duplicate handling
│       └── ui/
│           ├── main_window.py    # main window: URL input, Video/Playlist/Queue/History tabs
│           ├── models_qt.py      # Qt table models (playlist selection, queue)
│           ├── metadata_worker.py# background metadata fetch (QRunnable)
│           ├── settings_dialog.py
│           └── theme.py          # light/dark stylesheets
├── tests/
│   ├── conftest.py
│   ├── fixtures/                 # sample yt-dlp metadata JSON (mocked, no network)
│   ├── test_url_utils.py
│   ├── test_sanitize.py
│   ├── test_db_settings.py
│   ├── test_db_history.py
│   ├── test_queue_state.py
│   ├── test_playlist_selection.py
│   ├── test_duplicate_handling.py
│   ├── test_retry_backoff.py
│   ├── test_error_classification.py
│   ├── test_metadata_parsing.py
│   ├── test_cancellation.py
│   ├── test_ffmpeg_utils.py
│   ├── test_app_bootstrap.py
│   ├── test_download_manager_state.py
│   ├── test_enum_combo_roundtrip.py
│   ├── test_worker_lifecycle.py           # one worker per item; pause/cancel/remove reach waiting workers
│   ├── test_youtube_block_and_cookies.py  # bot-check handling, opt-in YouTube sign-in, Deno
│   └── test_integration_real_download.py   # opt-in, real network (see above)
└── packaging/
    ├── pyinstaller.spec
    ├── entry_point.py
    ├── install_linux.sh          # Linux: installs a clickable app-menu entry
    ├── install_windows.ps1       # Windows: creates a Desktop shortcut
    └── ytdlmanager.desktop.template
```

## How packaging works

The three "Get started" guides above already give you the exact commands to
build and install a clickable app for your own OS. This section is
reference material on how that packaging works under the hood, for anyone
who wants to understand or modify it.

Packaging uses [PyInstaller](https://pyinstaller.org/) to build a
standalone application per OS, driven by `packaging/pyinstaller.spec`. You
must build **on** the target OS — PyInstaller does not cross-compile, so a
Windows `.exe` can only be produced by running PyInstaller on Windows, and
likewise for macOS and Linux.

Raw output per OS (before the per-OS "make it clickable" steps each guide
above walks through):

- **macOS:** `packaging/dist/YTDLManager.app` (also a plain `packaging/dist/YTDLManager/` folder build)
- **Windows:** `packaging/dist/YTDLManager/YTDLManager.exe`
- **Linux:** `packaging/dist/YTDLManager/YTDLManager`

**Verification status, precisely:** built, launched, and used on macOS
(arm64) as part of this project's development. Windows and Linux were
**not** built or run in the environment this project was developed in (no
such machine was available there); what was done for them instead was a
full source review for portability (path handling, subprocess/console
behavior, encoding, per-OS FFmpeg detection) plus fixes for two real issues
that review turned up:
- a Windows-only console-flash bug (a windowed build's own FFmpeg version
  check would briefly flash a console window without an explicit
  `CREATE_NO_WINDOW` flag — fixed in `ffmpeg_utils.py`),
- a Windows-only logging crash risk (`sys.stderr` can be `None` in a
  windowed PyInstaller build, which a plain `logging.StreamHandler()`
  doesn't handle — fixed in `app.py` to skip that handler when there's no
  real stream, keeping the file log as the source of truth either way).

The Linux install script's file-copying and `.desktop`-generation logic was
exercised locally (with `$HOME` sandboxed) to confirm it behaves correctly;
running the resulting Linux binary itself still needs a real Linux machine
to fully confirm.

**FFmpeg is deliberately not bundled** into the package: it's a large
(tens of MB) binary with its own licensing considerations depending on build
configuration, and bundling one specific build would stop users getting
independent security updates. The packaged app detects FFmpeg on `PATH` at
runtime exactly as the source version does (with fallback checks at common
install locations — see [Troubleshooting](#troubleshooting)), and shows
install instructions if it's missing.

**Deno is bundled** (unlike FFmpeg): yt-dlp needs it for YouTube, it's
MIT-licensed, and it's a single self-contained binary (~80MB, most of the
size difference in a packaged build). The spec copies it from the build
environment's `deno` package (installed by `yt-dlp[deno]`) into
`deno_bin/`, where `js_runtime.py` finds it at runtime; if it's missing at
build time PyInstaller prints a warning and the build falls back to any Deno
installed on the machine.

To create a distributable archive/installer beyond the raw folder (e.g. a
`.dmg` on macOS, an NSIS/Inno Setup installer on Windows, an `.AppImage` or
`.deb` on Linux), wrap the `packaging/dist/` output with the platform's
usual tool (`hdiutil`/`create-dmg` on macOS, `iscc`/NSIS on Windows,
`appimagetool`/`dpkg-deb` on Linux) — these are standard OS-level packaging
steps outside PyInstaller's scope and are not included here to keep the
build reproducible with just `pip` + `pyinstaller`.

## Dependency licences

Direct runtime dependencies:

| Package | Licence |
|---|---|
| yt-dlp | Unlicense (public domain equivalent) |
| yt-dlp-ejs (via `yt-dlp[default]`) | Unlicense AND MIT AND ISC |
| deno (via `yt-dlp[deno]`) | MIT |
| PySide6 (+ Essentials/Addons, shiboken6) | LGPL-3.0 (also available under GPL-2.0/GPL-3.0) |

PySide6 is used as an unmodified, dynamically-linked dependency (installed
from PyPI wheels, imported normally), which satisfies LGPL-3.0's linking
terms for use in this MIT-licensed project.

To regenerate a full transitive dependency licence report at any time
(including dev/test tooling):

```bash
pip install pip-licenses
pip-licenses --format=markdown --with-urls --order=license
```

## Known limitations

- **Per-video overrides in playlist mode:** the batch options (mode and
  "Preferred format" — e.g. mp4/webm/mkv/3gp for video, mp3/opus/m4a/etc.
  for audio) apply to every selected item, since playlist metadata is
  fetched lazily (`extract_flat`) and doesn't include each video's exact
  format list up front — an *extension preference* is the option that
  actually works across a whole playlist without a slow per-item fetch.
  If a given video doesn't offer the requested extension, that one item
  falls back to its best available format automatically rather than
  failing the batch. Per-video *exact format id* overrides aren't exposed
  in the playlist UI; download an item individually from the Video tab for
  that level of control.
- **Not every item is downloadable.** Private, region-restricted,
  age-restricted (without sign-in), members-only, login-gated, or removed
  content will fail with a permanent (non-retried) error and a
  human-readable reason.
- **No authentication, by design** — apart from the opt-in YouTube sign-in,
  which exists only to get past YouTube's network-level bot check. This app
  never uses browser cookies or credentials on any other platform. In
  practice this means: Instagram profile/collection fetches and a
  meaningful share of individual Instagram posts return "login required";
  as of writing, **every Vimeo video tested during development** hit the
  same wall ("The web client only works when logged-in") regardless of the
  video's own privacy setting — Vimeo support is implemented and correctly
  routes to yt-dlp's Vimeo extractor, but is not expected to work for most
  content today. See [Supported platforms](#supported-platforms) for the
  full picture per site.
- **Facebook's extractor is fragile.** Facebook changes its page structure
  often, and some public videos will fail with a generic parsing error
  (not a permissions issue) until yt-dlp ships a fix — upgrade yt-dlp first
  if this happens.
- **No bulk/playlist support for Facebook.** There's no reliable "all of
  this page's videos" URL shape to build a collection view on, so Facebook
  only supports single videos, not the Playlist tab.
- **yt-dlp needs to stay current.** Because every supported platform changes
  frequently, an old yt-dlp version can start failing extraction; see
  [Keeping yt-dlp up to date](#keeping-yt-dlp-up-to-date).
- **Windows/Linux packaging** was written, source-reviewed for portability,
  and had two real Windows-specific issues fixed as a result (console-flash
  on FFmpeg detection, a logging handler that assumed a console exists) —
  but only actually built and launch-verified on macOS in the environment
  this project was developed in. See [How packaging works](#how-packaging-works)
  for the precise verification status, and the "Get started" guide for your
  OS above for the exact commands to build and verify it yourself.
