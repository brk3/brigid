import watch
from watch import NewVideoHandler, scan_existing


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

        assert seen == ["a.mkv", "b.mkv", "c.mkv"]


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
