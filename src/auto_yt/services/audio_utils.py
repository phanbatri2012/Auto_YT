from __future__ import annotations

import os
import tempfile
import time
import urllib.request
from pathlib import Path


DOWNLOAD_TIMEOUT_SECONDS = 90
MAX_SPEECH_WORDS_PER_SECOND = 6.0
FILE_REPLACE_RETRY_ATTEMPTS = 20
FILE_REPLACE_RETRY_DELAY_SECONDS = 0.05

_MPEG1_BITRATES = {
    1: (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    2: (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
}
_MPEG2_BITRATES = {
    1: (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    3: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_SAMPLE_RATES = (44100, 48000, 32000)


class AudioContentError(RuntimeError):
    """Raised when a returned audio file is invalid or materially incomplete."""


def _decode_synchsafe(value: bytes) -> int:
    return (
        (value[0] << 21)
        | (value[1] << 14)
        | (value[2] << 7)
        | value[3]
    )


def _id3v2_end(data: bytes) -> int:
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    footer_size = 10 if data[5] & 0x10 else 0
    return min(len(data), 10 + _decode_synchsafe(data[6:10]) + footer_size)


def _parse_mp3_header(data: bytes, offset: int) -> dict | None:
    if offset + 4 > len(data):
        return None
    header = int.from_bytes(data[offset:offset + 4], "big")
    if header & 0xFFE00000 != 0xFFE00000:
        return None

    version_bits = (header >> 19) & 0b11
    layer_bits = (header >> 17) & 0b11
    bitrate_index = (header >> 12) & 0b1111
    sample_rate_index = (header >> 10) & 0b11
    padding = (header >> 9) & 1
    if (
        version_bits == 0b01
        or layer_bits == 0
        or bitrate_index in {0, 15}
        or sample_rate_index == 3
    ):
        return None

    version = {0b11: 1.0, 0b10: 2.0, 0b00: 2.5}[version_bits]
    layer = {0b11: 1, 0b10: 2, 0b01: 3}[layer_bits]
    bitrates = _MPEG1_BITRATES if version == 1.0 else _MPEG2_BITRATES
    bitrate_kbps = bitrates[layer][bitrate_index]
    sample_rate = _SAMPLE_RATES[sample_rate_index]
    if version == 2.0:
        sample_rate //= 2
    elif version == 2.5:
        sample_rate //= 4

    if layer == 1:
        frame_length = ((12 * bitrate_kbps * 1000) // sample_rate + padding) * 4
        samples_per_frame = 384
    elif layer == 3 and version != 1.0:
        frame_length = (72 * bitrate_kbps * 1000) // sample_rate + padding
        samples_per_frame = 576
    else:
        frame_length = (144 * bitrate_kbps * 1000) // sample_rate + padding
        samples_per_frame = 1152

    if frame_length <= 4 or offset + frame_length > len(data):
        return None
    return {
        "frame_length": frame_length,
        "sample_rate": sample_rate,
        "samples_per_frame": samples_per_frame,
        "encoding": (version, layer, sample_rate),
    }


def _find_first_frame(data: bytes) -> tuple[int, dict]:
    start = _id3v2_end(data)
    scan_end = min(len(data) - 4, start + 16_384)
    for offset in range(start, scan_end + 1):
        frame = _parse_mp3_header(data, offset)
        if frame:
            return offset, frame
    raise AudioContentError("The Genmax response is not a valid MP3 file.")


def extract_mp3_audio_frames(data: bytes) -> tuple[bytes, float, tuple]:
    """Return playable MP3 frames without container metadata and their duration."""
    offset, first_frame = _find_first_frame(data)
    output = bytearray()
    duration_seconds = 0.0
    encoding = first_frame["encoding"]
    frame_index = 0

    while offset + 4 <= len(data):
        frame = _parse_mp3_header(data, offset)
        if not frame:
            break
        if frame["encoding"] != encoding:
            raise AudioContentError(
                "Genmax returned MP3 segments with incompatible encodings."
            )

        frame_end = offset + frame["frame_length"]
        frame_data = data[offset:frame_end]
        is_metadata_frame = frame_index == 0 and any(
            marker in frame_data for marker in (b"Xing", b"Info", b"VBRI")
        )
        if not is_metadata_frame:
            output.extend(frame_data)
            duration_seconds += frame["samples_per_frame"] / frame["sample_rate"]
        offset = frame_end
        frame_index += 1

    if not output or duration_seconds <= 0:
        raise AudioContentError(
            "The Genmax MP3 file contains no playable audio frames."
        )
    return bytes(output), duration_seconds, encoding


def download_audio(audio_url: str) -> bytes:
    request = urllib.request.Request(
        audio_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        return response.read()


def get_remote_mp3_duration(audio_url: str) -> float:
    _, duration_seconds, _ = extract_mp3_audio_frames(download_audio(audio_url))
    return duration_seconds


def validate_spoken_duration(text: str, duration_seconds: float) -> None:
    word_count = len(text.split())
    minimum_duration = word_count / MAX_SPEECH_WORDS_PER_SECOND
    if duration_seconds < minimum_duration:
        raise AudioContentError(
            "Genmax returned incomplete audio: "
            f"{duration_seconds / 60:.1f} minutes for {word_count:,} words "
            f"(minimum expected {minimum_duration / 60:.1f} minutes)."
        )


def _replace_file_with_retry(source_path: Path, output_path: Path) -> None:
    for attempt in range(FILE_REPLACE_RETRY_ATTEMPTS):
        try:
            os.replace(source_path, output_path)
            return
        except PermissionError:
            if attempt == FILE_REPLACE_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(FILE_REPLACE_RETRY_DELAY_SECONDS)


def merge_remote_mp3_files(
    audio_urls: list[str],
    output_path: Path,
    expected_texts: list[str] | None = None,
) -> float:
    if not audio_urls:
        raise ValueError("At least one audio URL is required.")
    if expected_texts is not None and len(expected_texts) != len(audio_urls):
        raise ValueError("Each audio URL must have matching expected text.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    expected_encoding = None
    total_duration = 0.0

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output_path.parent,
            prefix=f"{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output_file:
            temporary_path = Path(output_file.name)
            for index, audio_url in enumerate(audio_urls):
                frames, duration_seconds, encoding = extract_mp3_audio_frames(
                    download_audio(audio_url)
                )
                if expected_texts is not None:
                    validate_spoken_duration(
                        expected_texts[index],
                        duration_seconds,
                    )
                if expected_encoding is None:
                    expected_encoding = encoding
                elif encoding != expected_encoding:
                    raise AudioContentError(
                        "Genmax returned MP3 segments with incompatible encodings."
                    )
                output_file.write(frames)
                total_duration += duration_seconds
        _replace_file_with_retry(temporary_path, output_path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise

    return total_duration
