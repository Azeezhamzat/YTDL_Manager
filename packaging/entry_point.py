"""PyInstaller entry point.

PyInstaller executes its entry script as a bare top-level module (not as
part of a package), so `ytdlmanager/__main__.py`'s relative import
(`from .app import run`) fails when used directly as the Analysis script.
This tiny launcher imports the package normally instead.
"""

import sys

from ytdlmanager.app import run

if __name__ == "__main__":
    sys.exit(run())
