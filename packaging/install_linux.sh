#!/usr/bin/env bash
# Installs the PyInstaller onedir build produced by `pyinstaller
# pyinstaller.spec --noconfirm` into a per-user application menu entry, so
# YTDLManager shows up in GNOME/KDE/XFCE's application launcher and can be
# opened with a click instead of a terminal.
#
# Installs entirely under $HOME (~/.local/opt/ytdlmanager and
# ~/.local/share/applications) -- no sudo, no system-wide changes.
#
# The program must NOT go in ~/.local/share/ytdlmanager: that's where the
# app keeps its own database (settings, history, queue) and log (see
# ytdlmanager/config.py), and this script wipes INSTALL_DIR on every run.
#
# Usage (run from the packaging/ directory, after building):
#   ./install_linux.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/dist/YTDLManager"
INSTALL_DIR="$HOME/.local/opt/ytdlmanager"
# Where earlier versions of this script installed the program -- the app's
# data folder. Only the program files are removed from it, never the data.
LEGACY_INSTALL_DIR="$HOME/.local/share/ytdlmanager"
APPLICATIONS_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APPLICATIONS_DIR/ytdlmanager.desktop"

if [ ! -x "$BUILD_DIR/YTDLManager" ]; then
    echo "error: $BUILD_DIR/YTDLManager not found." >&2
    echo "Build it first:" >&2
    echo "  cd $SCRIPT_DIR && pyinstaller pyinstaller.spec --noconfirm" >&2
    exit 1
fi

echo "Installing to $INSTALL_DIR ..."
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -R "$BUILD_DIR/." "$INSTALL_DIR/"

if [ -x "$LEGACY_INSTALL_DIR/YTDLManager" ]; then
    echo "Removing the old program files from $LEGACY_INSTALL_DIR (your data stays) ..."
    rm -rf "$LEGACY_INSTALL_DIR/YTDLManager" "$LEGACY_INSTALL_DIR/_internal"
fi

mkdir -p "$APPLICATIONS_DIR"
sed "s|__INSTALL_DIR__|$INSTALL_DIR|" "$SCRIPT_DIR/ytdlmanager.desktop.template" > "$DESKTOP_FILE"
chmod +x "$DESKTOP_FILE"

# Best-effort: refresh the desktop database so the entry appears without
# logging out, if the tool is available (most distros ship it; harmless if
# not -- the .desktop file is already valid without this).
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR" 2>/dev/null || true
fi

echo "Done. YTDLManager should now appear in your application launcher."
echo "(If it doesn't show up immediately, log out and back in once.)"
echo
echo "To uninstall: rm -rf '$INSTALL_DIR' '$DESKTOP_FILE'"
echo "(Your settings and history live in '$LEGACY_INSTALL_DIR'; delete it too for a clean slate.)"
