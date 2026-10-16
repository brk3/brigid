import logging
import threading
from pathlib import Path

import watch
from watch import NewVideoHandler, scan_existing, start_scan_threads


class TestScanExisting:
    def test_fetches_subtitles_for_each_video(self, tmp_path, monkeypatch):
        (tmp_path / "Show" / "Season 01").mkdir(parents=True)
        video = tmp_path / "Show" / "Season 01" / "Show.S01E01.mkv"
        video.write_bytes(b"0")
        (tmp_path / "Show" / "Season 01" / "not-a-video.nfo").write_bytes(b"0")
        seen = []
        monkeypatch.setattr(watch, "fetch_subtitles", lambda path, langs: seen.append(path))

        scan_existing(tmp_path)

        assert seen == [video]

    def test_continues_past_a_failing_video(self, tmp_path, monkeypatch):
        for name in ("a.mkv", "b.mkv", "c.mkv"):
            (tmp_path / name).write_bytes(b"0")
        seen = []

        def flaky(path, langs):
            seen.append(path.name)
            if path.name == "b.mkv":
                raise RuntimeError("provider exploded")

        monkeypatch.setattr(watch, "fetch_subtitles", flaky)
        scan_existing(tmp_path)

        # Workers run in parallel, so completion order is nondeterministic.
        assert sorted(seen) == ["a.mkv", "b.mkv", "c.mkv"]


class TestStartScanThreads:
    def test_scan_runs_in_background_without_blocking(self, monkeypatch):
        started = threading.Event()
        release = threading.Event()

        def fake_scan(root):
            started.set()
            release.wait()

        monkeypatch.setattr(watch, "scan_existing", fake_scan)

        threads = start_scan_threads([Path("/data/tv")])

        # The scan is running in a background thread, not blocking the caller.
        assert started.wait(timeout=1)
        assert all(thread.is_alive() for thread in threads)
        release.set()
        for thread in threads:
            thread.join(timeout=1)
        assert not any(thread.is_alive() for thread in threads)


class TestNewVideoHandler:
    def test_handler_swallows_errors(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watch, "SETTLE_SECONDS", 0)

        def boom(path, langs):
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(watch, "fetch_subtitles", boom)
        video = tmp_path / "movie.mkv"
        video.write_bytes(b"0")

        NewVideoHandler()._handle(str(video))

    def test_handler_ignores_non_video_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watch, "SETTLE_SECONDS", 0)
        called = []
        monkeypatch.setattr(watch, "fetch_subtitles", lambda path, langs: called.append(path))

        NewVideoHandler()._handle(str(tmp_path / "movie.nfo"))

        assert called == []


class TestColoredFormatter:
    @staticmethod
    def _record(message, level=logging.INFO):
        return logging.LogRecord("test", level, __file__, 1, message, None, None)

    def test_colors_outcome_words_and_paths(self):
        out = watch.ColoredFormatter("%(levelname)s %(message)s").format(
            self._record("No subtitles found for /data/tv/a.mkv")
        )
        assert "\033[33mNo subtitles found\033[0m" in out
        assert "\033[36m/data/tv/a.mkv\033[0m" in out

    def test_benign_skip_is_not_colored_red(self):
        out = watch.ColoredFormatter("%(message)s").format(
            self._record("Skipping /data/tv/a.mkv: failed within the last 86400s", logging.WARNING)
        )
        assert "\033[31m" not in out
        assert "\033[2mSkipping\033[0m" in out
        assert "\033[2mfailed within\033[0m" in out

    def test_levelname_is_colored_and_restored(self):
        formatter = watch.ColoredFormatter("%(levelname)s %(message)s")
        record = self._record("Watching /data/tv for new videos")
        assert "\033[32mINFO\033[0m" in formatter.format(record)
        assert record.levelname == "INFO"  # not leaked into other handlers
