import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from subtitles import fetch_subtitles, is_video


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
            "subliminal", "download", "-l", "en", "-l", "fr", "/data/movies/Movie.mkv"
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
