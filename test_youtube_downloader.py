import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_yt.services import youtube_downloader


class FakeYoutubeDL:
    info = {}
    last_url = ""
    last_options = {}

    def __init__(self, options=None):
        self.options = options or {}
        type(self).last_options = self.options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, url, download=False):
        type(self).last_url = url
        if download:
            output = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
            output.write_bytes(b"video")
            for hook in self.options.get("progress_hooks", []):
                hook({
                    "status": "downloading",
                    "downloaded_bytes": 50,
                    "total_bytes": 100,
                    "info_dict": {"vcodec": "avc1", "acodec": "mp4a"},
                })
        return dict(self.info)


class YouTubeDownloaderTests(unittest.TestCase):
    def test_rejects_non_youtube_url(self):
        with self.assertRaisesRegex(ValueError, "link video hoặc kênh YouTube"):
            youtube_downloader.validate_youtube_url("https://example.com/video")

    def test_folder_name_is_safe_and_keeps_video_id(self):
        folder_name = youtube_downloader.sanitize_folder_name(
            'CON: Tiêu đề / có "ký tự" * lỗi?',
            "abc-123",
        )

        self.assertNotRegex(folder_name, r'[<>:"/\\|?*]')
        self.assertTrue(folder_name.endswith("[abc-123]"))

    def test_channel_entries_are_listed_and_deduplicated(self):
        FakeYoutubeDL.info = {
            "entries": [
                {"id": "one", "title": "Video 1", "url": "one"},
                {"id": "two", "title": "Video 2", "url": "two"},
                {"id": "one", "title": "Duplicate", "url": "one"},
            ]
        }
        with patch.object(youtube_downloader, "YoutubeDL", FakeYoutubeDL):
            videos = youtube_downloader.list_youtube_videos(
                "https://www.youtube.com/@example"
            )

        self.assertEqual([video["id"] for video in videos], ["one", "two"])
        self.assertEqual(
            videos[0]["url"],
            "https://www.youtube.com/watch?v=one",
        )
        self.assertEqual(
            FakeYoutubeDL.last_url,
            "https://www.youtube.com/@example/videos",
        )

    def test_explicit_channel_tab_is_not_modified(self):
        url = "https://www.youtube.com/@example/videos"
        self.assertEqual(
            youtube_downloader.normalize_youtube_listing_url(url),
            url,
        )

    def test_single_video_url_is_not_modified(self):
        url = "https://www.youtube.com/watch?v=abc123"
        self.assertEqual(
            youtube_downloader.normalize_youtube_listing_url(url),
            url,
        )

    def test_channel_url_is_detected_for_folder_numbering(self):
        self.assertTrue(
            youtube_downloader.is_youtube_channel_url(
                "https://www.youtube.com/@example/videos"
            )
        )
        self.assertFalse(
            youtube_downloader.is_youtube_channel_url(
                "https://www.youtube.com/watch?v=abc123"
            )
        )

    def test_download_creates_video_description_and_transcript(self):
        FakeYoutubeDL.info = {"description": "Mô tả kiểm thử"}
        manager = youtube_downloader.DownloadJobManager()
        manager._jobs["job"] = {
            "items": [{"progress": 0.0}],
        }
        video = {
            "id": "video-id",
            "title": "Tiêu đề",
            "url": "https://www.youtube.com/watch?v=video-id",
        }

        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(youtube_downloader, "YoutubeDL", FakeYoutubeDL),
            patch.object(
                youtube_downloader,
                "_fetch_transcript",
                return_value="Nội dung transcript",
            ),
        ):
            folder = Path(temporary_directory) / "video-folder"
            folder.mkdir()
            manager._download_one("job", 0, video, folder)

            self.assertEqual((folder / "video.mp4").read_bytes(), b"video")
            self.assertEqual(
                (folder / "description.txt").read_text(encoding="utf-8"),
                "Mô tả kiểm thử",
            )
            self.assertEqual(
                (folder / "transcript.txt").read_text(encoding="utf-8"),
                "Nội dung transcript",
            )
            self.assertEqual(manager._jobs["job"]["items"][0]["progress"], 99.0)
            self.assertEqual(
                FakeYoutubeDL.last_options["format"],
                youtube_downloader.VIDEO_FORMAT,
            )
            ffmpeg_directory = Path(FakeYoutubeDL.last_options["ffmpeg_location"])
            self.assertTrue((ffmpeg_directory / "ffmpeg.exe").is_file())

    def test_separate_video_and_audio_streams_use_one_progress_timeline(self):
        video_progress, video_phase = youtube_downloader.calculate_media_progress({
            "downloaded_bytes": 50,
            "total_bytes": 100,
            "info_dict": {"vcodec": "avc1", "acodec": "none"},
        })
        audio_progress, audio_phase = youtube_downloader.calculate_media_progress({
            "downloaded_bytes": 50,
            "total_bytes": 100,
            "info_dict": {"vcodec": "none", "acodec": "opus"},
        })

        self.assertEqual(video_progress, 39.0)
        self.assertEqual(audio_progress, 85.0)
        self.assertEqual(video_phase, "Đang tải video")
        self.assertEqual(audio_phase, "Đang tải audio")
        self.assertGreater(audio_progress, video_progress)

    def test_job_progress_includes_completed_and_current_items(self):
        manager = youtube_downloader.DownloadJobManager()
        manager._jobs["job"] = {
            "id": "job",
            "status": "running",
            "total": 2,
            "completed": 1,
            "failed": 0,
            "items": [
                {"status": "completed", "progress": 100.0},
                {"status": "downloading", "progress": 50.0},
            ],
        }

        self.assertEqual(manager.get("job")["progress"], 75.0)

    def test_channel_download_folder_has_original_position_prefix(self):
        manager = youtube_downloader.DownloadJobManager()

        def create_result(_job_id, _index, _video, folder, _control):
            (folder / "video.mp4").write_bytes(b"video")
            (folder / "description.txt").write_text("description", encoding="utf-8")
            (folder / "transcript.txt").write_text("transcript", encoding="utf-8")

        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(manager, "_download_one", side_effect=create_result),
        ):
            job_id = manager.start(
                temporary_directory,
                [{
                    "id": "video-id",
                    "title": "Tiêu đề",
                    "url": "https://www.youtube.com/watch?v=video-id",
                    "position": 12,
                }],
                number_folders=True,
            )
            for _ in range(100):
                if manager.get(job_id)["status"] == "completed":
                    break
                time.sleep(0.01)

            folders = [path.name for path in Path(temporary_directory).iterdir()]
            self.assertEqual(folders, ["0012 - Tiêu đề [video-id]"])

    def test_job_can_pause_resume_and_stop(self):
        manager = youtube_downloader.DownloadJobManager()
        download_started = threading.Event()

        def wait_for_stop(_job_id, _index, _video, _folder, control):
            download_started.set()
            while True:
                manager._honor_control(control)
                time.sleep(0.01)

        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(manager, "_download_one", side_effect=wait_for_stop),
        ):
            job_id = manager.start(
                temporary_directory,
                [{
                    "id": "video-id",
                    "title": "Tiêu đề",
                    "url": "https://www.youtube.com/watch?v=video-id",
                }],
            )
            self.assertTrue(download_started.wait(1))
            self.assertEqual(manager.pause(job_id)["status"], "paused")
            self.assertEqual(manager.resume(job_id)["status"], "running")
            self.assertEqual(manager.stop(job_id)["status"], "stopping")
            for _ in range(100):
                if manager.get(job_id)["status"] == "stopped":
                    break
                time.sleep(0.01)
            self.assertEqual(manager.get(job_id)["status"], "stopped")
            self.assertEqual(manager.get(job_id)["items"][0]["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
