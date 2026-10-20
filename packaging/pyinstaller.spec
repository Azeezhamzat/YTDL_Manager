# PyInstaller build spec for YTDLManager.
#
# Produces a single-folder (onedir) build for the current platform. onedir is
# preferred over onefile: startup is faster and FFmpeg/yt-dlp errors are
# easier to diagnose since the extracted files persist between runs.
#
# Usage (run from the repository root, with the dev extras installed):
#   pyinstaller packaging/pyinstaller.spec --noconfirm
#
# Output lands in dist/YTDLManager/ (Windows/Linux) or dist/YTDLManager.app
# (macOS, when BUNDLE is used below).
#
# FFmpeg is NOT bundled: it is a large (~50-100MB) binary with its own
# licensing considerations (LGPL/GPL depending on build configuration), and
# bundling a specific build would prevent users from getting security
# updates independently. Instead the app detects FFmpeg on PATH at runtime
# (see ytdlmanager/ffmpeg_utils.py) and shows install instructions if it's
# missing. See README > "FFmpeg installation and detection".

import sys

from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = []
binaries = []
hiddenimports = ["yt_dlp"]

# yt-dlp ships plugin/extractor data that PyInstaller's default import
# analysis can miss; collect_all pulls in its submodules and data files.
ytdlp_datas, ytdlp_binaries, ytdlp_hiddenimports = collect_all("yt_dlp")
datas += ytdlp_datas
binaries += ytdlp_binaries
hiddenimports += ytdlp_hiddenimports

# yt-dlp needs a JavaScript runtime (Deno) to solve YouTube's player
# challenges; yt-dlp's own PyInstaller hook already bundles the solver
# scripts (yt_dlp_ejs). Unlike FFmpeg, Deno is MIT-licensed and a single
# self-contained binary (~80MB), so the build carries its own copy -- found
# at runtime by ytdlmanager/js_runtime.py under BUNDLED_DENO_DIR. Install it
# into the build environment with: pip install "yt-dlp[default,deno]"
try:
    import deno

    binaries.append((deno.find_deno_bin(), "deno_bin"))
except (ImportError, FileNotFoundError):
    print(
        "WARNING: Deno not found; this build's YouTube support will be degraded. "
        'Run: pip install "yt-dlp[default,deno]"',
        file=sys.stderr,
    )

a = Analysis(
    ["entry_point.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YTDLManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="YTDLManager",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="YTDLManager.app",
        icon=None,
        bundle_identifier="com.local.ytdlmanager",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "0.1.0",
        },
    )
