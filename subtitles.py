import logging
import os
import subprocess
from pathlib import Path

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".mov"}

# Upper bound on how long a single subliminal invocation may run before we give
# up and move on to the next file. subliminal fans out to several subtitle
# providers over the network, and any one of them can stall indefinitely; the
# watcher and the startup scan are both single-threaded, so without this a
# single stuck provider call blocks the entire queue forever with nothing logged.
DEFAULT_TIMEOUT_SECONDS = 300


def _timeout() -> int:
    return int(os.environ.get("SUBLIMINAL_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))


def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def fetch_subtitles(path: Path, languages: list[str]) -> bool:
    cmd = ["subliminal", "download"]
    for language in languages:
        cmd += ["-l", language]
    cmd.append(str(path))

    timeout = _timeout()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        logging.warning("subliminal timed out for %s after %ss; skipping", path, timeout)
        return False
    if result.returncode != 0:
        logging.warning("subliminal failed for %s: %s", path, result.stderr.strip())
        return False
    if result.stdout.strip():
        logging.info(result.stdout.strip())
    return True
