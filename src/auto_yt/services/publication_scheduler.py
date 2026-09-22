"""Timezone-aware publication slot selection."""

from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class PublicationScheduleError(RuntimeError):
    pass


def validate_publication_slots(slots: object) -> list[dict]:
    if not isinstance(slots, list):
        raise ValueError("Lịch đăng phải là một danh sách khung giờ.")
    normalized = []
    seen = set()
    for slot in slots:
        if not isinstance(slot, dict):
            raise ValueError("Khung giờ đăng không hợp lệ.")
        try:
            day = int(slot.get("day"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Ngày trong lịch đăng không hợp lệ.") from exc
        time_text = str(slot.get("time") or "").strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_text):
            raise ValueError("Giờ đăng phải có định dạng HH:MM.")
        try:
            parsed_time = dt.time.fromisoformat(time_text)
        except ValueError as exc:
            raise ValueError("Giờ đăng phải có định dạng HH:MM.") from exc
        if day < 0 or day > 6 or parsed_time.second or parsed_time.microsecond:
            raise ValueError("Khung giờ đăng không hợp lệ.")
        key = (day, parsed_time.strftime("%H:%M"))
        if key in seen:
            raise ValueError("Lịch đăng không được chứa khung giờ trùng nhau.")
        seen.add(key)
        normalized.append({"day": day, "time": key[1]})
    normalized.sort(key=lambda item: (item["day"], item["time"]))
    return normalized


def validate_timezone(value: str) -> str:
    timezone_name = str(value or "").strip()
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Múi giờ đăng video không hợp lệ.") from exc
    return timezone_name


def _parse_utc(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def find_next_publication_slot(
    *,
    timezone_name: str,
    slots: list[dict],
    daily_limit: int,
    lead_minutes: int,
    occupied_utc: list[str],
    now_utc: dt.datetime | None = None,
    search_days: int = 366,
) -> str:
    timezone_name = validate_timezone(timezone_name)
    normalized_slots = validate_publication_slots(slots)
    if not normalized_slots:
        raise PublicationScheduleError("Kênh chưa có khung giờ đăng video.")
    timezone = ZoneInfo(timezone_name)
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    earliest = now.astimezone(dt.timezone.utc) + dt.timedelta(
        minutes=max(1, int(lead_minutes))
    )
    occupied = [parsed for value in occupied_utc if (parsed := _parse_utc(value))]
    occupied_minutes = {item.replace(second=0, microsecond=0) for item in occupied}
    local_counts: dict[dt.date, int] = {}
    for item in occupied:
        local_day = item.astimezone(timezone).date()
        local_counts[local_day] = local_counts.get(local_day, 0) + 1
    start_day = earliest.astimezone(timezone).date()
    candidates = []
    for day_offset in range(max(1, int(search_days))):
        local_day = start_day + dt.timedelta(days=day_offset)
        if local_counts.get(local_day, 0) >= max(1, int(daily_limit)):
            continue
        for slot in normalized_slots:
            if slot["day"] != local_day.weekday():
                continue
            hour, minute = (int(value) for value in slot["time"].split(":"))
            local_candidate = dt.datetime.combine(
                local_day, dt.time(hour=hour, minute=minute), tzinfo=timezone
            )
            candidate = local_candidate.astimezone(dt.timezone.utc)
            # Reject nonexistent local times around DST jumps.
            if candidate.astimezone(timezone).replace(tzinfo=None) != local_candidate.replace(
                tzinfo=None
            ):
                continue
            if candidate < earliest:
                continue
            rounded = candidate.replace(second=0, microsecond=0)
            if rounded in occupied_minutes:
                continue
            candidates.append(candidate)
    if not candidates:
        raise PublicationScheduleError(
            "Không tìm thấy khung giờ trống trong 366 ngày tới."
        )
    return min(candidates).replace(microsecond=0).isoformat()
