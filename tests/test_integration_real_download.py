"""Optional, opt-in integration test that hits the real network.

Every other test in this suite runs against mocks/fixtures and never touches
the network, per the project's testing requirements. This single test is the
exception: it exercises the real yt-dlp + FFmpeg pipeline end to end against
"Big Buck Bunny" (youtube.com/watch?v=YE7VzlLtp-4), the Blender Foundation's
open movie released under Creative Commons Attribution 3.0 -- explicit
permission to download and redistribute is part of the film's licence. It is
also yt-dlp's own primary extractor test fixture. We download audio-only to
keep the transfer small while still exercising the FFmpeg post-processing
path (FFmpegExtractAudio).

It is skipped unless YTDLMANAGER_RUN_INTEGRATION_TESTS=1 is set, so normal
`pytest` runs (and CI) never repeatedly download media.

    YTDLMANAGER_RUN_INTEGRATION_TESTS=1 pytest tests/test_integration_real_download.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ytdlmanager.ffmpeg_utils import find_ffmpeg
from ytdlmanager.models import DownloadMode, DownloadRequest, QueueStatus
from ytdlmanager.ytdlp_backend import download_one, guess_output_path

pytestmark = pytest.mark.skipif(
    os.environ.get("YTDLMANAGER_RUN_INTEGRATION_TESTS") != "1",
    reason="Set YTDLMANAGER_RUN_INTEGRATION_TESTS=1 to run the real-network integration test.",
)

TEST_VIDEO_URL = "https://www.youtube.com/watch?v=YE7VzlLtp-4"  # Big Buck Bunny, CC BY 3.0


def test_real_download_produces_a_playable_file(tmp_path):
    ffmpeg_status = find_ffmpeg()
    assert ffmpeg_status.found, "FFmpeg must be installed to run this integration test"

    request = DownloadRequest(
        url=TEST_VIDEO_URL,
        title="integration-test-video",
        destination_dir=str(tmp_path),
        mode=DownloadMode.AUDIO_ONLY,
    )
    output_template = guess_output_path(tmp_path, "integration-test-video")

    saved_path = download_one(
        request,
        output_template=output_template,
        ffmpeg_location=ffmpeg_status.path,
    )

    assert saved_path
    saved_file = tmp_path / os.path.basename(saved_path)
    assert saved_file.exists()
    assert saved_file.stat().st_size > 1000


def test_real_pause_then_resume_produces_a_complete_playable_file(qapp, tmp_path, tmp_db_path):
    """The one integration test that matters most for the pause/resume
    feature: pause a real, in-flight download partway through, confirm the
    partial file survives, resume it, and confirm the final file is
    complete and valid -- not just "some file exists". Format 232 (720p)
    is picked deliberately: large enough on typical bandwidth to still be
    mid-transfer a couple of seconds in, so the pause lands on a genuinely
    partial file rather than a race against an already-finished download.
    """
    from PySide6.QtCore import QEventLoop, QTimer

    from ytdlmanager.db import Database
    from ytdlmanager.download_manager import DownloadManager

    ffmpeg_status = find_ffmpeg()
    assert ffmpeg_status.found, "FFmpeg must be installed to run this integration test"

    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        request = DownloadRequest(
            url=TEST_VIDEO_URL,
            title="pause-resume-test-video",
            destination_dir=str(tmp_path),
            mode=DownloadMode.SPECIFIC_FORMAT,
            format_id="232",
        )
        item = manager.enqueue(request)
        manager.start_item(item.id)

        loop = QEventLoop()
        QTimer.singleShot(2500, loop.quit)  # let it transfer for ~2.5s
        loop.exec()

        partial_files_before_pause = list(tmp_path.glob("*.part"))
        assert partial_files_before_pause, (
            "expected an in-progress .part file 2.5s in -- format 232 finished "
            "too fast for this test to be meaningful; pick a larger format"
        )
        size_before_pause = partial_files_before_pause[0].stat().st_size
        assert size_before_pause > 0

        manager.pause_item(item.id)

        # Wait for the worker to actually acknowledge the pause (status
        # change is delivered async via a queued cross-thread signal).
        paused_loop = QEventLoop()

        def on_status_changed(changed_id, status_value):
            if changed_id == item.id and status_value == QueueStatus.PAUSED.value:
                paused_loop.quit()

        manager.item_status_changed.connect(on_status_changed)
        QTimer.singleShot(10000, paused_loop.quit)  # safety timeout
        paused_loop.exec()

        assert manager.get_item(item.id).status == QueueStatus.PAUSED
        partial_files_after_pause = list(tmp_path.glob("*.part"))
        assert partial_files_after_pause, "pausing must not delete the partial file"
        assert manager.get_item(item.id).working_path is not None

        # Resume: must continue the SAME file, not restart from zero.
        manager.start_item(item.id)

        finished_loop = QEventLoop()
        result = {}

        def on_finished(finished_id, saved_path):
            if finished_id == item.id:
                result["saved_path"] = saved_path
                finished_loop.quit()

        def on_failed(failed_id, message, is_permanent):
            if failed_id == item.id:
                result["error"] = message
                finished_loop.quit()

        manager.item_finished.connect(on_finished)
        manager.item_failed.connect(on_failed)
        QTimer.singleShot(120000, finished_loop.quit)  # safety timeout
        finished_loop.exec()

        assert "error" not in result, f"resumed download failed: {result.get('error')}"
        assert "saved_path" in result, "resumed download neither finished nor failed in time"

        saved_file = Path(result["saved_path"])
        assert saved_file.exists()
        assert saved_file.stat().st_size > size_before_pause  # actually continued, not truncated
        assert not list(tmp_path.glob("*.part"))  # no leftover partial files

        # Confirm the finished file is actually valid, not just present --
        # ffprobe must be able to read a sensible duration out of it (Big
        # Buck Bunny is 597s), which a corrupt/truncated resume would fail.
        import json
        import subprocess

        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(saved_file),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert probe.returncode == 0, f"ffprobe failed: {probe.stderr}"
        duration = float(json.loads(probe.stdout)["format"]["duration"])
        assert 590 < duration < 605  # ~597s, generous tolerance

        manager.shutdown()
    finally:
        db.close()


def test_real_subtitles_only_download_writes_a_valid_srt(tmp_path):
    ffmpeg_status = find_ffmpeg()
    assert ffmpeg_status.found, "FFmpeg must be installed to run this integration test"

    request = DownloadRequest(
        url=TEST_VIDEO_URL,
        title="subs-test",
        destination_dir=str(tmp_path),
        mode=DownloadMode.VIDEO_WITH_AUDIO,
        subtitle_langs="en",
        subtitles_only=True,
    )
    saved_path = download_one(
        request,
        output_template=guess_output_path(tmp_path, "subs-test"),
        ffmpeg_location=ffmpeg_status.path,
    )

    assert saved_path.endswith(".en.srt")
    text = Path(saved_path).read_text(encoding="utf-8")
    assert "-->" in text  # SRT cue timing lines
    # Subtitles-only must not have downloaded any media.
    assert [p.name for p in tmp_path.iterdir()] == [Path(saved_path).name]
