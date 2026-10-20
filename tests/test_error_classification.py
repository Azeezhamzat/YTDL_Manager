from __future__ import annotations

import pytest

from ytdlmanager.ytdlp_backend import classify_error


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: Private video. Sign in if you've been granted access.",
        "This video is unavailable",
        "ERROR: [youtube] abc123: Video unavailable. This video has been removed by the uploader",
        "This video is not available in your country",
        "Sign in to confirm your age",
        "ERROR: This video is age-restricted",
        "This video is no longer available due to a copyright claim",
    ],
)
def test_permanent_errors_are_not_retried(message):
    assert classify_error(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "urlopen error timed out",
        "HTTP Error 503: Service Unavailable",
        "Connection reset by peer",
        "Unable to download webpage: <urlopen error [Errno 8]>",
        "Read timed out",
    ],
)
def test_transient_errors_are_retried(message):
    assert classify_error(message) is False


def test_unknown_error_defaults_to_transient():
    assert classify_error("Some completely novel error message we've never seen") is False


@pytest.mark.parametrize(
    "message",
    [
        "OSError: [Errno 28] No space left on device",
        "ERROR: unable to open for writing: disk quota exceeded",
        "PermissionError: [Errno 13] Permission denied: '/root/video.mp4'",
        "OSError: [Errno 30] Read-only file system",
    ],
)
def test_filesystem_errors_are_permanent(message):
    # No amount of retrying frees disk space or fixes permissions -- these
    # must fail fast instead of exhausting the retry budget with backoff
    # delays on a problem that can't self-resolve.
    assert classify_error(message) is True


@pytest.mark.parametrize(
    "message",
    [
        # Real message from yt-dlp's Vimeo extractor, captured live.
        "ERROR: [vimeo] 148751763: The web client only works when logged-in. "
        "Use --cookies, --cookies-from-browser",
        "ERROR: [Instagram] Requested content is not available, "
        "rate-limit reached or login required",
        "ERROR: [twitter] This account's tweets are protected",
        "ERROR: [facebook] You must log in to continue",
    ],
)
def test_login_required_errors_are_permanent(message):
    # This app deliberately doesn't support authentication (public content
    # only), so anything that needs a login can never succeed here no matter
    # how many times it's retried.
    assert classify_error(message) is True
