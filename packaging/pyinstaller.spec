# PyInstaller build spec for YTDLManager.
#
# Produces a single-folder (onedir) build for the current platform. onedir is
# preferred over onefile: startup is faster and FFmpeg/yt-dlp errors are
# easier to diagnose since the extracted files persist between runs.
#
# Usage (with the dev extras installed; paths below are relative to this
# file, so it can be run from anywhere):
#   cd packaging && pyinstaller pyinstaller.spec --noconfirm
#
# Output lands in dist/YTDLManager/ (all platforms) plus dist/YTDLManager.app
# on macOS. The per-OS installers are then made from that output by
# packaging/macos/build_dmg.sh, packaging/windows/build_installer.ps1 and
# packaging/linux/build_appimage.sh (see README > "How packaging works").
#
# FFmpeg is NOT bundled: it is a large (~50-100MB) binary with its own
# licensing considerations (LGPL/GPL depending on build configuration), and
# bundling a specific build would prevent users from getting security
# updates independently. Instead the app detects FFmpeg on PATH at runtime
# (see ytdlmanager/ffmpeg_utils.py) and shows install instructions if it's
# missing. See README > "FFmpeg installation and detection".

import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

HERE = Path(SPECPATH)  # noqa: F821 - provided by PyInstaller
ROOT = HERE.parent
ICONS = HERE / "icons"
VERSION = re.search(
    r'__version__ = "([^"]+)"', (ROOT / "src" / "ytdlmanager" / "__init__.py").read_text()
).group(1)

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
    [str(HERE / "entry_point.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

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
    icon=str(ICONS / ("YTDLManager.ico" if sys.platform == "win32" else "YTDLManager.icns")),
)

coll = COLLECT(
    exe,
    a.binaries,
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
        icon=str(ICONS / "YTDLManager.icns"),
        bundle_identifier="com.local.ytdlmanager",
        version=VERSION,
        info_plist={
            "CFBundleName": "YTDL Manager",
            "CFBundleDisplayName": "YTDL Manager",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.utilities",
        },
    )
