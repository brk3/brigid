import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import subtitles
from subtitles import fetch_subtitles, is_video


@pytest.fixture(autouse=True)
def reset_module_state(monkeypatch, tmp_path):
    monkeypatch.setattr(subtitles, "_quota_reset_at", None)
    monkeypatch.setattr(subtitles, "ATTEMPT_LOG", str(tmp_path / "attempts.json"))
    monkeypatch.setattr(subtitles, "_attempt_times", {})
    monkeypatch.setattr(subtitles, "_attempts_loaded", False)
    monkeypatch.setattr(subtitles, "_attempt_log_failed", False)


class TestIsVideo:
    def test_recognizes_video_extension(self, tmp_path):
        f = tmp_path / "movie.mkv"
        f.write_bytes(b"0")
        assert is_video(f)

    def test_rejects_non_video_extension(self, tmp_path):
        f = tmp_path / "movie.nfo"
        f.write_bytes(b"0")
        assert not is_video(f)

    def test_rejects_directory(self, tmp_path):
        d = tmp_path / "movie.mkv"
        d.mkdir()
        assert not is_video(d)


class TestFetchSubtitles:
    @patch("subtitles.subprocess.run")
    def test_builds_command_with_languages(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        fetch_subtitles(Path("/data/movies/Movie.mkv"), ["en", "fr"])

        assert mock_run.call_args.args[0] == [
            "subliminal", "download", "-R", "omdb", "-R", "tvdb", "-F", "srt",
            "-l", "en", "-l", "fr", "/data/movies/Movie.mkv"
        ]

    @patch("subtitles.subprocess.run")
    def test_returns_false_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        assert fetch_subtitles(Path("/data/movies/Movie.mkv"), ["en"]) is False

    @patch("subtitles.subprocess.run")
    def test_returns_true_on_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        assert fetch_subtitles(Path("/data/movies/Movie.mkv"), ["en"]) is True

    @patch("subtitles.subprocess.run")
    def test_passes_timeout_to_subprocess(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        fetch_subtitles(Path("/data/movies/Movie.mkv"), ["en"])

        assert mock_run.call_args.kwargs["timeout"] == 300

    @patch("subtitles.subprocess.run")
    def test_returns_false_on_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(["subliminal", "download"], 300)
        assert fetch_subtitles(Path("/data/movies/Movie.mkv"), ["en"]) is False


class TestQuotaBackoff:
    @patch("subtitles.subprocess.run")
    def test_quota_exceeded_sets_backoff_until_reported_reset(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Downloaded 0 subtitle",
            stderr="download quota exceeded, quota reset on 2099-01-01T00:00:00Z UTC",
        )

        assert fetch_subtitles(Path("/data/movies/Movie.mkv"), ["en"]) is False
        assert subtitles._quota_reset_at is not None
        assert subtitles._quota_reset_at > time.time()

    @patch("subtitles.subprocess.run")
    def test_fetch_sleeps_until_quota_reset(self, mock_run, monkeypatch):
        monkeypatch.setattr(subtitles, "_quota_reset_at", time.time() + 3600)
        slept = []
        monkeypatch.setattr(subtitles.time, "sleep", lambda seconds: slept.append(seconds))
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        fetch_subtitles(Path("/data/movies/Movie.mkv"), ["en"])

        assert slept and slept[0] > 3500

    @patch("subtitles.subprocess.run")
    def test_unreported_reset_time_defaults_to_a_day(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Downloaded 0 subtitle",
            stderr="download quota exceeded",
        )

        fetch_subtitles(Path("/data/movies/Movie.mkv"), ["en"])

        assert subtitles._quota_reset_at >= time.time() + 23 * 60 * 60


class TestRecentAttemptSkip:
    @patch("subtitles.subprocess.run")
    def test_skips_a_file_that_recently_failed(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Downloaded 0 subtitle", stderr=""
        )
        path = Path("/data/movies/Movie.mkv")

        assert fetch_subtitles(path, ["en"]) is False
        mock_run.reset_mock()
        assert fetch_subtitles(path, ["en"]) is False
        mock_run.assert_not_called()

    @patch("subtitles.subprocess.run")
    def test_successful_fetch_is_not_recorded(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Downloaded 1 subtitle", stderr=""
        )
        path = Path("/data/movies/Movie.mkv")

        assert fetch_subtitles(path, ["en"]) is True
        assert fetch_subtitles(path, ["en"]) is True
        assert mock_run.call_count == 2

    @patch("subtitles.subprocess.run")
    def test_failed_attempts_survive_a_restart(self, mock_run, monkeypatch, tmp_path):
        log = tmp_path / "attempts.json"
        monkeypatch.setattr(subtitles, "ATTEMPT_LOG", str(log))
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Downloaded 0 subtitle", stderr=""
        )
        path = Path("/data/movies/Movie.mkv")
        assert fetch_subtitles(path, ["en"]) is False

        # Simulate a restart: fresh in-memory state, same attempt log on disk.
        monkeypatch.setattr(subtitles, "_attempt_times", {})
        monkeypatch.setattr(subtitles, "_attempts_loaded", False)
        assert fetch_subtitles(path, ["en"]) is False

        assert mock_run.call_count == 1
