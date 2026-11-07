#!/usr/bin/env bash
# Makes the Linux downloads from the PyInstaller output:
#   packaging/release/YTDLManager-<version>-linux-<arch>.AppImage  (one file, runs anywhere)
#   packaging/release/YTDLManager-<version>-linux-<arch>.tar.gz    (folder + install_linux.sh)
#
# Usage (after `cd packaging && pyinstaller pyinstaller.spec --noconfirm`):
#   bash packaging/linux/build_appimage.sh
#
# Downloads appimagetool from the AppImage project's GitHub releases.
# Build on the oldest distro you want to support (CI uses Ubuntu 22.04):
# the bundled libraries require at least the build machine's glibc.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGING="$(dirname "$HERE")"
ROOT="$(dirname "$PACKAGING")"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/src/ytdlmanager/__init__.py")"
ARCH="$(uname -m)"
BUILD="$PACKAGING/dist/YTDLManager"
OUT="$PACKAGING/release"
NAME="YTDLManager-$VERSION-linux-$ARCH"

if [ ! -x "$BUILD/YTDLManager" ]; then
    echo "error: $BUILD/YTDLManager not found -- run pyinstaller first." >&2
    exit 1
fi
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"

# -- 1. tar.gz: the program folder plus the per-user menu installer ----------
mkdir -p "$WORK/$NAME"
cp -R "$BUILD" "$WORK/$NAME/YTDLManager"
cp "$PACKAGING/install_linux.sh" "$PACKAGING/ytdlmanager.desktop.template" \
    "$PACKAGING/icons/ytdlmanager.png" "$WORK/$NAME/"
tar -C "$WORK" -czf "$OUT/$NAME.tar.gz" "$NAME"
echo "Created $OUT/$NAME.tar.gz"

# -- 2. AppImage ------------------------------------------------------------
APPDIR="$WORK/AppDir"
mkdir -p "$APPDIR/usr/lib"
cp -R "$BUILD" "$APPDIR/usr/lib/ytdlmanager"
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/ytdlmanager/YTDLManager" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"
cat > "$APPDIR/ytdlmanager.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=YTDL Manager
Comment=Video and audio downloader built on yt-dlp
Exec=ytdlmanager
Icon=ytdlmanager
Terminal=false
Categories=AudioVideo;Network;
DESKTOP
cp "$PACKAGING/icons/ytdlmanager.png" "$APPDIR/ytdlmanager.png"
ln -s ytdlmanager.png "$APPDIR/.DirIcon"

TOOL="$WORK/appimagetool"
curl -fsSL -o "$TOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
chmod +x "$TOOL"
# Extract-and-run: CI containers usually have no FUSE to mount the tool.
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$OUT/$NAME.AppImage"
echo "Created $OUT/$NAME.AppImage"
