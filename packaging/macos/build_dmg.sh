#!/usr/bin/env bash
# Wraps the PyInstaller .app in a drag-to-install disk image:
#   packaging/release/YTDLManager-<version>-macos-<arch>.dmg
#
# Usage (after `cd packaging && pyinstaller pyinstaller.spec --noconfirm`):
#   bash packaging/macos/build_dmg.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGING="$(dirname "$HERE")"
ROOT="$(dirname "$PACKAGING")"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/src/ytdlmanager/__init__.py")"
ARCH="$(uname -m)"  # arm64 (Apple Silicon) or x86_64 (Intel)
APP="$PACKAGING/dist/YTDLManager.app"
OUT="$PACKAGING/release"
DMG="$OUT/YTDLManager-$VERSION-macos-$ARCH.dmg"

if [ ! -d "$APP" ]; then
    echo "error: $APP not found -- run pyinstaller first." >&2
    exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/YTDLManager.app"
ln -s /Applications "$STAGE/Applications"

mkdir -p "$OUT"
rm -f "$DMG"
hdiutil create -volname "YTDL Manager $VERSION" -srcfolder "$STAGE" \
    -fs HFS+ -format UDZO -ov "$DMG" >/dev/null
echo "Created $DMG ($(du -h "$DMG" | cut -f1))"
