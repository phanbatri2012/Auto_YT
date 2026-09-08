"""List and download YouTube videos into self-contained folders."""

from __future__ import annotations

import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from imageio_ffmpeg import get_ffmpeg_exe
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from auto_yt.paths import DATA_DIR


YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
VIDEO_FORMAT = (
    "bestvideo*[protocol^=http]+bestaudio[protocol^=http]/"
    "best[protocol^=http]/bestvideo*+bestaudio/best"
)
FOLDER_SEQUENCE_WIDTH = 4
FFMPEG_SETUP_LOCK = threading.Lock()
VIDEO_STREAM_PROGRESS_END = 78.0
AUDIO_STREAM_PROGRESS_START = VIDEO_STREAM_PROGRESS_END
MEDIA_PROGRESS_END = 92.0
POSTPROCESS_PROGRESS = 95.0
DESCRIPTION_PROGRESS = 97.0
TRANSCRIPT_PROGRESS = 99.0


class YouTubeDownloaderError(RuntimeError):
    pass


class DownloadStopped(RuntimeError):
    pass


def ensure_ffmpeg_directory() -> str:
    source = Path(get_ffmpeg_exe())
    target_directory = DATA_DIR / "tools" / "ffmpeg"
    target = target_directory / "ffmpeg.exe"
    with FFMPEG_SETUP_LOCK:
        if not target.is_file() or target.stat().st_size != source.stat().st_size:
            target_directory.mkdir(parents=True, exist_ok=True)
            temporary_target = target.with_suffix(".tmp")
            shutil.copy2(source, temporary_target)
            temporary_target.replace(target)
    return str(target_directory)


def validate_youtube_url(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in YOUTUBE_HOSTS:
        raise ValueError("Hãy nhập đúng link video hoặc kênh YouTube.")
    return url


def normalize_youtube_listing_url(url: str) -> str:
    """Point a channel home URL at its Videos tab before extracting entries."""
    parsed = urlparse(url)
    if parsed.netloc.lower() not in YOUTUBE_HOSTS or parsed.netloc.lower() == "youtu.be":
        return url

    parts = [part for part in parsed.path.split("/") if part]
    if not parts or parts[0] in {"watch", "playlist", "shorts", "live"}:
        return url

    is_channel_root = (
        (parts[0].startswith("@") and len(parts) == 1)
        or (parts[0] in {"channel", "c", "user"} and len(parts) == 2)
    )
    if not is_channel_root:
        return url

    return parsed._replace(path=f"{parsed.path.rstrip('/')}/videos").geturl()


def is_youtube_channel_url(url: str) -> bool:
    parsed = urlparse(validate_youtube_url(url))
    parts = [part for part in parsed.path.split("/") if part]
    return bool(
        parts
        and (parts[0].startswith("@") or parts[0] in {"channel", "c", "user"})
    )


def sanitize_folder_name(title: str, video_id: str) -> str:
    cleaned_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(title or ""))
    cleaned_title = re.sub(r"\s+", " ", cleaned_title).strip(" .")
    if not cleaned_title:
        cleaned_title = "YouTube Video"
    if cleaned_title.upper() in WINDOWS_RESERVED_NAMES:
        cleaned_title = f"Video {cleaned_title}"
    cleaned_title = cleaned_title[:120].rstrip(" .")
    safe_video_id = re.sub(r"[^A-Za-z0-9_-]", "", str(video_id or ""))
    return f"{cleaned_title} [{safe_video_id or 'unknown'}]"


def _video_url(video_id: str, fallback_url: str = "") -> str:
    if fallback_url and urlparse(fallback_url).netloc.lower() in YOUTUBE_HOSTS:
        return fallback_url
    return f"https://www.youtube.com/watch?v={video_id}"


def _serialize_video(entry: dict, position: int) -> dict | None:
    video_id = str(entry.get("id") or "").strip()
    if not video_id:
        return None
    title = str(entry.get("title") or f"Video {video_id}").strip()
    webpage_url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
    if webpage_url and not webpage_url.startswith(("http://", "https://")):
        webpage_url = ""
    return {
        "id": video_id,
        "title": title,
        "url": _video_url(video_id, webpage_url),
        "duration": entry.get("duration"),
        "channel": str(entry.get("channel") or entry.get("uploader") or "").strip(),
        "upload_date": str(entry.get("upload_date") or "").strip(),
        "thumbnail": str(entry.get("thumbnail") or "").strip(),
        "position": position,
    }


def list_youtube_videos(url: str) -> list[dict]:
    validated_url = normalize_youtube_listing_url(validate_youtube_url(url))
    options = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "nocheckcertificate": True,
    }
    try:
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(validated_url, download=False)
    except DownloadError as exc:
        raise YouTubeDownloaderError(f"Không thể đọc link YouTube: {exc}") from exc

    if not info:
        raise YouTubeDownloaderError("YouTube không trả về video nào.")

    entries = info.get("entries") if isinstance(info, dict) else None
    raw_videos = list(entries or [info])
    videos = []
    seen_ids = set()
    for position, entry in enumerate(raw_videos, start=1):
        if not isinstance(entry, dict):
            continue
        video = _serialize_video(entry, position)
        if not video or video["id"] in seen_ids:
            continue
        seen_ids.add(video["id"])
        videos.append(video)

    if not videos:
        raise YouTubeDownloaderError("Không tìm thấy video trong link đã nhập.")
    return videos


def select_download_directory() -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title="Chọn thư mục lưu video YouTube",
            mustexist=True,
        )
    finally:
        root.destroy()
    return str(selected or "")


def _fetch_transcript(video_id: str) -> str:
    api = YouTubeTranscriptApi()
    transcript_list = api.list(video_id)
    try:
        transcript = transcript_list.find_transcript(["vi", "en"])
    except Exception:
        available = list(transcript_list)
        if not available:
            raise YouTubeDownloaderError("Video không có transcript.")
        transcript = available[0]
    return TextFormatter().format_transcript(transcript.fetch()).strip()


def calculate_media_progress(payload: dict) -> tuple[float, str]:
    """Map yt-dlp stream progress onto one monotonic per-video timeline."""
    total = payload.get("total_bytes") or payload.get("total_bytes_estimate") or 0
    downloaded = payload.get("downloaded_bytes") or 0
    fraction = min(1.0, max(0.0, downloaded / total)) if total else 0.0
    info = payload.get("info_dict") or {}
    has_video = str(info.get("vcodec") or "none").lower() != "none"
    has_audio = str(info.get("acodec") or "none").lower() != "none"

    if has_video and not has_audio:
        progress = fraction * VIDEO_STREAM_PROGRESS_END
        phase = "Đang tải video"
    elif has_audio and not has_video:
        audio_range = MEDIA_PROGRESS_END - AUDIO_STREAM_PROGRESS_START
        progress = AUDIO_STREAM_PROGRESS_START + fraction * audio_range
        phase = "Đang tải audio"
    else:
        progress = fraction * MEDIA_PROGRESS_END
        phase = "Đang tải video"

    return round(progress, 1), phase


class DownloadJobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._controls: dict[str, dict[str, threading.Event]] = {}
        self._lock = threading.Lock()

    def start(
        self,
        destination: str,
        videos: list[dict],
        number_folders: bool = False,
    ) -> str:
        destination_path = Path(destination).expanduser()
        if not destination_path.is_absolute() or not destination_path.is_dir():
            raise ValueError("Thư mục lưu không hợp lệ.")
        if not videos:
            raise ValueError("Hãy chọn ít nhất một video để tải.")

        normalized_videos = []
        for fallback_position, video in enumerate(videos, start=1):
            video_id = str(video.get("id") or "").strip()
            title = str(video.get("title") or "").strip()
            url = validate_youtube_url(video.get("url", ""))
            if not video_id:
                raise ValueError("Danh sách có video không hợp lệ.")
            try:
                position = max(1, int(video.get("position") or fallback_position))
            except (TypeError, ValueError):
                position = fallback_position
            normalized_videos.append({
                "id": video_id,
                "title": title,
                "url": url,
                "position": position,
            })

        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "status": "running",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "destination": str(destination_path),
            "total": len(normalized_videos),
            "completed": 0,
            "failed": 0,
            "current_video_id": "",
            "number_folders": bool(number_folders),
            "items": [
                {
                    **video,
                    "status": "pending",
                    "progress": 0.0,
                    "phase": "Đang chờ",
                    "folder": "",
                    "error": "",
                }
                for video in normalized_videos
            ],
        }
        with self._lock:
            self._jobs[job_id] = job
            self._controls[job_id] = {
                "pause": threading.Event(),
                "stop": threading.Event(),
            }
        threading.Thread(
            target=self._run,
            args=(job_id, destination_path),
            daemon=True,
        ).start()
        return job_id

    def list_jobs(self, limit: int = 100) -> list[dict]:
        with self._lock:
            job_ids = list(self._jobs.keys())[-max(1, min(int(limit), 500)):]
        return [job for job_id in reversed(job_ids) if (job := self.get(job_id))]

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            items = [dict(item) for item in job["items"]]
            total = len(items)
            overall_progress = 0.0
            if total:
                processed_progress = sum(
                    100.0
                    if item["status"] in {"completed", "failed"}
                    else float(item.get("progress") or 0.0)
                    for item in items
                )
                overall_progress = round(processed_progress / total, 1)
            return {
                **job,
                "progress": overall_progress,
                "items": items,
            }

    def pause(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            control = self._controls.get(job_id)
            if not job or not control:
                raise ValueError("Không tìm thấy job tải.")
            if job["status"] != "running":
                raise ValueError("Chỉ có thể tạm dừng job đang tải.")
            control["pause"].set()
            job["status"] = "paused"
        return self.get(job_id)

    def resume(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            control = self._controls.get(job_id)
            if not job or not control:
                raise ValueError("Không tìm thấy job tải.")
            if job["status"] != "paused":
                raise ValueError("Chỉ có thể tiếp tục job đang tạm dừng.")
            job["status"] = "running"
            control["pause"].clear()
        return self.get(job_id)

    def stop(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            control = self._controls.get(job_id)
            if not job or not control:
                raise ValueError("Không tìm thấy job tải.")
            if job["status"] not in {"running", "paused"}:
                raise ValueError("Job này không còn chạy.")
            job["status"] = "stopping"
            control["stop"].set()
            control["pause"].clear()
        return self.get(job_id)

    def _update_item(self, job_id: str, index: int, **changes) -> None:
        with self._lock:
            self._jobs[job_id]["items"][index].update(changes)

    def _update_item_progress(
        self,
        job_id: str,
        index: int,
        progress: float,
        phase: str,
    ) -> None:
        with self._lock:
            item = self._jobs[job_id]["items"][index]
            item["progress"] = max(float(item.get("progress") or 0.0), progress)
            item["phase"] = phase

    def _run(self, job_id: str, destination: Path) -> None:
        job = self.get(job_id)
        if not job:
            return
        control = self._controls[job_id]
        try:
            for index, video in enumerate(job["items"]):
                self._honor_control(control)
                with self._lock:
                    self._jobs[job_id]["current_video_id"] = video["id"]
                folder_name = sanitize_folder_name(video["title"], video["id"])
                if job["number_folders"]:
                    sequence = str(video["position"]).zfill(FOLDER_SEQUENCE_WIDTH)
                    folder_name = f"{sequence} - {folder_name}"
                try:
                    folder = destination / folder_name
                    folder.mkdir(parents=True, exist_ok=True)
                    self._update_item(
                        job_id,
                        index,
                        status="downloading",
                        phase="Đang chuẩn bị tải",
                        folder=str(folder),
                    )
                    self._download_one(job_id, index, video, folder, control)
                    self._update_item(
                        job_id,
                        index,
                        status="completed",
                        progress=100.0,
                        phase="Hoàn thành",
                    )
                    with self._lock:
                        self._jobs[job_id]["completed"] += 1
                except DownloadStopped:
                    self._mark_stopped(job_id, index)
                    return
                except Exception as exc:
                    self._update_item(
                        job_id,
                        index,
                        status="failed",
                        phase="Tải thất bại",
                        error=str(exc),
                    )
                    with self._lock:
                        self._jobs[job_id]["failed"] += 1
        except DownloadStopped:
            self._mark_stopped(job_id, 0)
            return
        finally:
            with self._lock:
                current_job = self._jobs[job_id]
                if current_job["status"] == "stopping":
                    for item in current_job["items"]:
                        if item["status"] in {"pending", "downloading"}:
                            item["status"] = "stopped"
                            item["phase"] = "Đã dừng"
                    current_job["current_video_id"] = ""
                    current_job["status"] = "stopped"
                elif current_job["status"] != "stopped":
                    current_job["current_video_id"] = ""
                    current_job["status"] = (
                        "completed"
                        if current_job["failed"] == 0
                        else "completed_with_errors"
                    )

    @staticmethod
    def _honor_control(
        control: dict[str, threading.Event],
        during_download: bool = False,
    ) -> None:
        while control["pause"].is_set():
            if control["stop"].is_set():
                if during_download:
                    raise KeyboardInterrupt()
                raise DownloadStopped()
            time.sleep(0.2)
        if control["stop"].is_set():
            if during_download:
                raise KeyboardInterrupt()
            raise DownloadStopped()

    def _mark_stopped(self, job_id: str, current_index: int) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for index, item in enumerate(job["items"]):
                if index >= current_index and item["status"] in {"pending", "downloading"}:
                    item["status"] = "stopped"
                    item["phase"] = "Đã dừng"
            job["current_video_id"] = ""
            job["status"] = "stopped"

    def _download_one(
        self,
        job_id: str,
        index: int,
        video: dict,
        folder: Path,
        control: dict[str, threading.Event] | None = None,
    ) -> None:
        def progress_hook(payload: dict) -> None:
            if control:
                self._honor_control(control, during_download=True)
            if payload.get("status") not in {"downloading", "finished"}:
                return
            if payload.get("status") == "finished" and not payload.get("downloaded_bytes"):
                payload = {
                    **payload,
                    "downloaded_bytes": payload.get("total_bytes") or 0,
                }
            progress, phase = calculate_media_progress(payload)
            self._update_item_progress(job_id, index, progress, phase)

        def postprocessor_hook(payload: dict) -> None:
            if control:
                self._honor_control(control, during_download=True)
            if payload.get("status") in {"started", "processing", "finished"}:
                self._update_item_progress(
                    job_id,
                    index,
                    POSTPROCESS_PROGRESS,
                    "Đang ghép và hoàn thiện video",
                )

        options = {
            "format": VIDEO_FORMAT,
            "ffmpeg_location": ensure_ffmpeg_directory(),
            "merge_output_format": "mp4",
            "outtmpl": str(folder / "video.%(ext)s"),
            "noplaylist": True,
            "continuedl": True,
            "overwrites": False,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
        }
        try:
            with YoutubeDL(options) as downloader:
                info = downloader.extract_info(video["url"], download=True)
        except KeyboardInterrupt as exc:
            raise DownloadStopped() from exc
        except DownloadError as exc:
            raise YouTubeDownloaderError(f"Tải video thất bại: {exc}") from exc
        if not info:
            raise YouTubeDownloaderError("YouTube không trả về dữ liệu video.")

        self._update_item_progress(
            job_id,
            index,
            POSTPROCESS_PROGRESS,
            "Đang lưu mô tả",
        )
        description = str(info.get("description") or "").strip()
        (folder / "description.txt").write_text(description, encoding="utf-8")
        self._update_item_progress(
            job_id,
            index,
            DESCRIPTION_PROGRESS,
            "Đang lấy transcript",
        )
        if control:
            self._honor_control(control)
        try:
            transcript = _fetch_transcript(video["id"])
        except Exception as exc:
            transcript = f"Không thể lấy transcript: {exc}"
        (folder / "transcript.txt").write_text(transcript, encoding="utf-8")
        self._update_item_progress(
            job_id,
            index,
            TRANSCRIPT_PROGRESS,
            "Đang hoàn tất",
        )


download_jobs = DownloadJobManager()
