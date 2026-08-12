import logging
import subprocess
from pathlib import Path

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".mov"}


def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def fetch_subtitles(path: Path, languages: list[str]) -> bool:
    cmd = ["subliminal", "download"]
    for language in languages:
        cmd += ["-l", language]
    cmd.append(str(path))

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logging.warning("subliminal failed for %s: %s", path, result.stderr.strip())
        return False
    if result.stdout.strip():
        logging.info(result.stdout.strip())
    return True
