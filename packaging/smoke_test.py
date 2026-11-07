"""Launch a built app headless and check that it really starts.

Runs the PyInstaller output with Qt's offscreen platform and a throwaway
home/app-data folder (so a real user's settings and history are never
touched), waits for the startup log line, and checks that:
  - the process is still running (no crash on start),
  - it found the Deno binary bundled into the build.

Usage (after `pyinstaller pyinstaller.spec` in packaging/):
    python packaging/smoke_test.py [macos|windows|linux]
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PACKAGING = Path(__file__).resolve().parent
EXECUTABLES = {
    "macos": "dist/YTDLManager.app/Contents/MacOS/YTDLManager",
    "windows": "dist/YTDLManager/YTDLManager.exe",
    "linux": "dist/YTDLManager/YTDLManager",
}
STARTUP_LINE = "JavaScript runtime for YouTube"


def _default_target() -> str:
    return {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else _default_target()
    executable = PACKAGING / EXECUTABLES[target]
    if not executable.exists():
        print(f"Built app not found: {executable}")
        return 1

    home = Path(tempfile.mkdtemp(prefix="ytdl-smoke-"))
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(home / "AppData"),
        "XDG_DATA_HOME": str(home / "data"),
        "QT_QPA_PLATFORM": "offscreen",
    }
    log = {
        "macos": home / "Library" / "Application Support" / "YTDLManager",
        "windows": home / "AppData" / "YTDLManager",
        "linux": home / "data" / "ytdlmanager",
    }[target] / "ytdlmanager.log"

    process = subprocess.Popen(
        [str(executable)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    started = False
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and process.poll() is None:
        if log.exists() and STARTUP_LINE in log.read_text(errors="replace"):
            time.sleep(3)  # let the window finish building
            started = True
            break
        time.sleep(0.5)
    still_running = process.poll() is None

    process.terminate()
    try:
        output, _ = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
    log_text = log.read_text(errors="replace") if log.exists() else "(no log written)"
    console = output.decode(errors="replace") if output else ""

    print("---- app log ----", log_text, "---- console ----", console[-4000:], sep="\n")
    checks = {
        "wrote its startup log line": started,
        "still running after startup": still_running,
        "uses the bundled Deno": "deno_bin" in log_text,
        "no Python traceback": "Traceback" not in console + log_text,
    }
    for name, ok in checks.items():
        print(f"[{'ok' if ok else 'FAIL'}] {name}")
    passed = all(checks.values())
    print("SMOKE TEST", "PASSED" if passed else "FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
