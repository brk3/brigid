import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".mov"}

# Upper bound on how long a single subliminal invocation may run before we give
# up and move on to the next file. subliminal fans out to several subtitle
# providers over the network, and any one of them can stall indefinitely; the
# watcher and the startup scan are both single-threaded, so without this a
# single stuck provider call blocks the entire queue forever with nothing logged.
DEFAULT_TIMEOUT_SECONDS = 300

# subliminal logs "download quota exceeded, quota reset on <ts> UTC" when
# OpenSubtitles.com answers 407. Exit code stays 0, so we must watch stderr.
QUOTA_EXCEEDED_RE = re.compile(r"download quota exceeded(?:, quota reset on (.+?) UTC)?")
DEFAULT_QUOTA_BACKOFF_SECONDS = 24 * 60 * 60  # fallback when no reset time is reported

_quota_lock = threading.Lock()
_quota_reset_at: float | None = None  # epoch seconds; fetchers sleep until it passes


def _wait_for_quota_reset() -> None:
    global _quota_reset_at
    with _quota_lock:
        reset_at = _quota_reset_at
    if reset_at is None:
        return
    delay = reset_at - time.time()
    if delay <= 0:
        with _quota_lock:
            if _quota_reset_at == reset_at:
                _quota_reset_at = None
        return
    logging.warning("Download quota exhausted; backing off for %.0f minutes", delay / 60)
    time.sleep(delay)


def _note_quota_exceeded(reset_time_utc: str | None) -> None:
    global _quota_reset_at
    reset_at = None
    if reset_time_utc:
        try:
            reset_at = datetime.fromisoformat(reset_time_utc.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    if reset_at is None:
        reset_at = time.time() + DEFAULT_QUOTA_BACKOFF_SECONDS
    with _quota_lock:
        _quota_reset_at = max(_quota_reset_at or 0, reset_at)
    logging.warning(
        "Pausing subtitle downloads until %s",
        datetime.fromtimestamp(reset_at, tz=timezone.utc).isoformat(),
    )


def _timeout() -> int:
    return int(os.environ.get("SUBLIMINAL_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))


def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def fetch_subtitles(path: Path, languages: list[str]) -> bool:
    # Skip refiners that can't work here: omdb's bundled key is dead (401) and
    # tvdb's legacy API 404s on search. Both only burn a request per file.
    # tmdb stays active but is a no-op: with no SUBLIMINAL_REFINER_TMDB_APIKEY
    # set it aborts locally without any network call.
    cmd = ["subliminal", "download", "-R", "omdb", "-R", "tvdb"]
    for language in languages:
        cmd += ["-l", language]
    cmd.append(str(path))

    _wait_for_quota_reset()

    timeout = _timeout()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        logging.warning("subliminal timed out for %s after %ss; skipping", path, timeout)
        return False
    quota = QUOTA_EXCEEDED_RE.search(result.stderr)
    if quota:
        _note_quota_exceeded(quota.group(1))
        return False
    if result.returncode != 0:
        logging.warning("subliminal failed for %s: %s", path, result.stderr.strip())
        return False
    if result.stdout.strip():
        logging.info(result.stdout.strip())
    return True
