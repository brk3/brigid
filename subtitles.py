import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".mov"}

# Upper bound on how long a single subliminal invocation may run before we give
# up and move on to the next file. subliminal fans out to several subtitle
# providers over the network, and any one of them can stall indefinitely; the
# watcher handler is synchronous, so without this a single stuck provider call
# blocks its queue forever with nothing logged.
DEFAULT_TIMEOUT_SECONDS = 300

# Failed files are remembered so a restart doesn't re-hammer the rate-limited
# providers with the same library sweep. The log survives restarts because it
# lives on the media mount (TV_ROOT is where brigid writes its subtitles).
ATTEMPT_LOG = os.environ.get("ATTEMPT_LOG", os.path.join(os.environ.get("TV_ROOT", "/data/tv"), ".brigid-attempts.json"))
SKIP_RECENT_SECONDS = int(os.environ.get("SKIP_RECENT_SECONDS", str(24 * 60 * 60)))

_attempt_lock = threading.Lock()
_attempt_times: dict[str, float] = {}  # path -> epoch seconds of last failed attempt
_attempts_loaded = False
_attempt_log_failed = False

# subliminal logs "download quota exceeded, quota reset on <ts> UTC" when
# OpenSubtitles.com answers 407 and raises DownloadLimitReached (in the
# stderr traceback) when the legacy OpenSubtitles.org API does. Exit code
# stays 0 either way, so we must watch stderr.
QUOTA_EXCEEDED_RE = re.compile(
    r"(?:download quota exceeded|DownloadLimitReached)(?:, quota reset on (.+?) UTC)?"
)
DEFAULT_QUOTA_BACKOFF_SECONDS = 24 * 60 * 60  # fallback when no reset time is reported

# subliminal's per-file stdout is progressbar noise plus these two lines; we
# parse them into one readable log line per file instead of dumping them raw.
SUMMARY_RE = re.compile(r"(\d+) video collected / (\d+) video ignored / (\d+) error")
DOWNLOADED_RE = re.compile(r"Downloaded (\d+) subtitles?")
DISCARDED_RE = re.compile(r"Some providers have been discarded due to unexpected errors: (.+)")


def _parse_summary(stdout: str) -> tuple[int, int, int] | None:
    match = SUMMARY_RE.search(stdout)
    if not match:
        return None
    collected, ignored, errors = match.groups()
    return int(collected), int(ignored), int(errors)


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


def _load_attempt_times() -> None:
    global _attempts_loaded
    if _attempts_loaded:
        return
    _attempts_loaded = True
    if not ATTEMPT_LOG:
        return
    try:
        with open(ATTEMPT_LOG) as f:
            saved = json.load(f)
        _attempt_times.update(
            {path: datetime.fromisoformat(timestamp).timestamp() for path, timestamp in saved.items()}
        )
    except FileNotFoundError:
        pass  # first run: nothing to load
    except (OSError, ValueError):
        logging.warning("Could not read attempt log %s; starting fresh", ATTEMPT_LOG)


def _recently_attempted(path: Path) -> bool:
    _load_attempt_times()
    with _attempt_lock:
        attempted = _attempt_times.get(str(path))
    return attempted is not None and time.time() - attempted < SKIP_RECENT_SECONDS


def _record_attempt(path: Path) -> None:
    global _attempt_log_failed
    with _attempt_lock:
        _attempt_times[str(path)] = time.time()
        saved = {
            p: datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
            for p, timestamp in _attempt_times.items()
        }
    if not ATTEMPT_LOG:
        return
    try:
        with open(ATTEMPT_LOG, "w") as f:
            json.dump(saved, f)
    except OSError:
        if not _attempt_log_failed:
            _attempt_log_failed = True
            logging.warning("Could not write attempt log %s; skip list will not survive restarts", ATTEMPT_LOG)


def fetch_subtitles(path: Path, languages: list[str]) -> bool:
    if _recently_attempted(path):
        logging.debug("Skipping %s: failed within the last %ss", path, SKIP_RECENT_SECONDS)
        return False

    # Skip refiners that can't work here: omdb's bundled key is dead (401) and
    # tvdb's legacy API 404s on search. Both only burn a request per file.
    # tmdb stays active but is a no-op: with no SUBLIMINAL_REFINER_TMDB_APIKEY
    # set it aborts locally without any network call. -F pins .srt so weak
    # providers can't leave MicroDVD (.sub) files behind. There is no
    # opensubtitles.com account (the credentials are for the legacy .org
    # XML-RPC provider), so ignore the .com REST provider entirely instead of
    # letting it fail 401 and get discarded on every file.
    cmd = ["subliminal", "download", "-R", "omdb", "-R", "tvdb", "-F", "srt", "-P", "opensubtitlescom"]
    for language in languages:
        cmd += ["-l", language]
    cmd.append(str(path))

    _wait_for_quota_reset()

    timeout = _timeout()
    # Concurrent subliminal processes must not share a cache file: its dbm
    # backend crashes on the creation race and its MutexLock is thread-only,
    # so give every invocation a throwaway cache dir.
    env = {**os.environ, "SUBLIMINAL_CACHE_DIR": tempfile.mkdtemp(prefix="subliminal-cache-")}
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        _record_attempt(path)
        logging.warning("subliminal timed out for %s after %ss; skipping", path, timeout)
        return False
    quota = QUOTA_EXCEEDED_RE.search(result.stderr)
    if quota:
        _record_attempt(path)
        _note_quota_exceeded(quota.group(1))
        return False
    if result.returncode != 0:
        _record_attempt(path)
        logging.warning("subliminal failed for %s: %s", path, result.stderr.strip())
        return False
    downloaded = DOWNLOADED_RE.search(result.stdout)
    if downloaded:
        count = int(downloaded.group(1))
        if count == 0:
            # Nothing was found: remember it so a restart doesn't waste quota
            # on the same file. Successful fetches are not recorded so a
            # replaced file is still picked up by the watcher.
            _record_attempt(path)
            discarded = DISCARDED_RE.search(result.stdout)
            if discarded:
                # "Nothing found" usually means provider failures, not an
                # empty search result: say which providers broke.
                logging.warning(
                    "No subtitles found for %s: subliminal discarded providers: %s",
                    path, discarded.group(1),
                )
            else:
                logging.info("No subtitles found for %s", path)
            return False
        logging.info(
            "Downloaded %d %s for %s",
            count, "subtitle" if count == 1 else "subtitles", path,
        )
        return True
    summary = _parse_summary(result.stdout)
    if summary:
        collected, ignored, errors = summary
        if errors:
            logging.warning(
                "Could not scan %s for subtitles (%d error%s)",
                path, errors, "s" if errors > 1 else "",
            )
            return True
        if collected == 0:
            # "N video ignored" means the file already has subtitles in every
            # requested language: the common case on a library rescan, so it
            # stays out of the INFO log.
            logging.debug("Subtitles already present for %s", path)
            return True
    if result.stdout.strip():
        logging.info(result.stdout.strip())
    return True
