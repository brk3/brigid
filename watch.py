#!/usr/bin/env python3
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from health import Health, start_health_server
from subtitles import fetch_subtitles, is_video

TV_ROOT = Path(os.environ.get("TV_ROOT", "/data/tv"))
MOVIES_ROOT = Path(os.environ.get("MOVIES_ROOT", "/data/movies"))
LANGUAGES = [lang.strip() for lang in os.environ.get("LANGUAGES", "en").split(",") if lang.strip()]
SETTLE_SECONDS = int(os.environ.get("SETTLE_SECONDS", "5"))
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))
BEAT_SECONDS = int(os.environ.get("BEAT_SECONDS", "15"))
LOG_FILE = os.environ.get("LOG_FILE", "")
SCAN_WORKERS = int(os.environ.get("SCAN_WORKERS", "4"))


RESET = "\033[0m"
DIM, RED, YELLOW, GREEN, CYAN = "\033[2m", "\033[31m", "\033[33m", "\033[32m", "\033[36m"

LEVEL_COLORS = {
    logging.DEBUG: CYAN,
    logging.INFO: GREEN,
    logging.WARNING: YELLOW,
    logging.ERROR: RED,
    logging.CRITICAL: "\033[1;31m",
}

# Outcome phrases, most specific first: "failed within" is a benign skip and
# must win over the plain "failed" red.
OUTCOME_COLORS = re.compile(
    r"(?P<dim>Skipping|failed within|already present)"
    r"|(?P<red>timed out|failed|died)"
    r"|(?P<yellow>No subtitles found|Could not|quota exhausted|backing off|Pausing|discarded providers)"
    r"|(?P<green>Downloaded|complete)"
    r"|(?P<cyan>New video detected)",
    re.IGNORECASE,
)
# shortcut: only /healthz and /readyz are exempt from path highlighting; grow
# the lookahead if more non-file paths show up in log messages.
PATH_PATTERN = re.compile(r"/(?!healthz\b|readyz\b)[^\s,:)]+")


def _colorize(message: str) -> str:
    def _replace(match):
        for name, color in (("dim", DIM), ("red", RED), ("yellow", YELLOW), ("green", GREEN), ("cyan", CYAN)):
            if match.group(name):
                return f"{color}{match.group()}{RESET}"
        return match.group()

    message = OUTCOME_COLORS.sub(_replace, message)
    return PATH_PATTERN.sub(lambda m: f"{CYAN}{m.group()}{RESET}", message)


class ColoredFormatter(logging.Formatter):
    def format(self, record):
        levelname = record.levelname
        record.levelname = f"{LEVEL_COLORS.get(record.levelno, '')}{levelname}{RESET}"
        try:
            return super().format(record)
        finally:
            record.levelname = levelname

    def formatMessage(self, record):
        return _colorize(super().formatMessage(record))


FMT = "%(asctime)s %(levelname)s %(message)s"


def setup_logging():
    handlers = []
    stream = logging.StreamHandler(sys.stdout)
    # Color only when a human is at a terminal; piped and file logs stay plain.
    stream.setFormatter(ColoredFormatter(FMT) if sys.stdout.isatty() else logging.Formatter(FMT))
    handlers.append(stream)
    if LOG_FILE:
        Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setFormatter(logging.Formatter(FMT))
        handlers.append(file_handler)
    logging.basicConfig(
        level=logging.INFO,
        handlers=handlers,
    )


class NewVideoHandler(FileSystemEventHandler):
    def on_created(self, event):
        self._handle(event.src_path)

    def on_moved(self, event):
        self._handle(event.dest_path)

    def _handle(self, raw_path: str):
        path = Path(raw_path)
        if not is_video(path):
            return
        logging.info("New video detected: %s", path)
        time.sleep(SETTLE_SECONDS)
        try:
            fetch_subtitles(path, LANGUAGES)
        except Exception:
            logging.exception("Failed to fetch subtitles for %s", path)


def _fetch(path: Path):
    try:
        fetch_subtitles(path, LANGUAGES)
    except Exception:
        logging.exception("Failed to fetch subtitles for %s", path)


def scan_existing(root: Path):
    logging.info("Scanning existing videos under %s", root)
    videos = [path for path in sorted(root.rglob("*")) if is_video(path)]
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS, thread_name_prefix="scan") as pool:
        # pool.map is lazy: consuming it submits every file and waits for all
        # workers, preserving the per-call SUBLIMINAL_TIMEOUT_SECONDS bound.
        list(pool.map(_fetch, videos))
    logging.info("Scan of %s complete", root)


def start_scan_threads(roots: list[Path]) -> list[threading.Thread]:
    """Start the startup scans without blocking the caller (or readiness)."""
    threads = []
    for root in roots:
        thread = threading.Thread(target=scan_existing, args=(root,), name=f"scan-{root}", daemon=True)
        thread.start()
        threads.append(thread)
    return threads


def main() -> int:
    setup_logging()
    health = Health()
    start_health_server(health, HEALTH_PORT)

    roots = [root for root in (TV_ROOT, MOVIES_ROOT) if root.exists()]
    if not roots:
        logging.error("Neither %s nor %s exist", TV_ROOT, MOVIES_ROOT)
        return 1

    observer = Observer()
    for root in roots:
        observer.schedule(NewVideoHandler(), str(root), recursive=True)
    observer.start()
    health.track(observer)
    logging.info("Watching %s for new videos", ", ".join(str(root) for root in roots))

    # Ready means "the watcher is up". The startup scan takes minutes on a big
    # library and must not sit on the critical path: the Kubernetes startup
    # probe (600 s) kills the pod before a single-threaded scan finishes.
    health.mark_ready()
    start_scan_threads(roots)

    try:
        while True:
            if not observer.is_alive():
                logging.error("Watcher thread died; exiting so Kubernetes restarts us")
                return 1
            health.beat()
            time.sleep(BEAT_SECONDS)
    except KeyboardInterrupt:
        logging.info("Shutting down")
        observer.stop()
        observer.join()
        return 0


if __name__ == "__main__":
    sys.exit(main())
