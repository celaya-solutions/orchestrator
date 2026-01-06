from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional, Tuple

from lxml import etree

# Preferred absolute dataset locations; fall back to repo-local copies if present.
DEFAULT_RAW_ROOT = Path("/data/datasets/raw/healthkit")
DEFAULT_DERIVED_ROOT = Path("/data/datasets/derived")
FALLBACK_RAW_ROOT = Path("data/datasets/raw/healthkit")
FALLBACK_DERIVED_ROOT = Path("data/datasets/derived")

EXPORT_FILENAME = "export.xml"
TIMESERIES_FILENAME = "healthkit_timeseries.jsonl"
METADATA_FILENAME = "healthkit_metadata.json"

MOTION_CONTEXT_KEY = "HKMetadataKeyHeartRateMotionContext"
MOTION_CLASS = {"0": "rest", "1": "active"}


@dataclass
class ParseStats:
    heart_rate_samples: int = 0
    skipped_records: int = 0


def _parse_datetime(value: str) -> Optional[datetime]:
    """Parse HealthKit timestamps into timezone-aware datetimes."""
    if not value:
        return None
    value = value.strip()
    formats = (
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _floor_window(ts: datetime, minutes: int) -> Tuple[datetime, datetime]:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    window_seconds = minutes * 60
    epoch = int(ts.timestamp())
    start_epoch = (epoch // window_seconds) * window_seconds
    start = datetime.fromtimestamp(start_epoch, tz=ts.tzinfo)
    end = start + timedelta(seconds=window_seconds)
    return start, end


def _cleanup_element(elem: etree._Element) -> None:
    """Aggressively clear parsed XML nodes to hold memory flat."""
    elem.clear()
    parent = elem.getparent()
    if parent is not None:
        while elem.getprevious() is not None:
            del parent[0]


def _iter_heart_rate(
    export_path: Path, stats: ParseStats
) -> Iterator[Tuple[datetime, float, Optional[str]]]:
    """
    Stream heart-rate samples from export.xml using lxml.iterparse.

    Yields: (timestamp, bpm, motion_class) where motion_class is 'rest', 'active', or None.
    """
    parser = etree.iterparse(
        str(export_path),
        events=("end",),
        recover=True,
        huge_tree=True,
    )
    for _, elem in parser:
        if elem.tag != "Record":
            continue

        try:
            record_type = elem.attrib.get("type")
            if record_type != "HKQuantityTypeIdentifierHeartRate":
                continue

            timestamp = _parse_datetime(elem.attrib.get("startDate", ""))
            if timestamp is None:
                stats.skipped_records += 1
                continue

            try:
                bpm = float(elem.attrib["value"])
            except (KeyError, TypeError, ValueError):
                stats.skipped_records += 1
                continue

            motion_class: Optional[str] = None
            for child in elem:
                if child.tag != "MetadataEntry":
                    continue
                if child.attrib.get("key") != MOTION_CONTEXT_KEY:
                    continue
                raw_motion = child.attrib.get("value")
                if raw_motion in MOTION_CLASS:
                    motion_class = MOTION_CLASS[raw_motion]
                    break

            stats.heart_rate_samples += 1
            yield timestamp, bpm, motion_class
        finally:
            _cleanup_element(elem)


def _flush_window(
    out_file,
    window_start: datetime,
    window_end: datetime,
    count: int,
    sum_bpm: float,
    sumsq_bpm: float,
    rest_count: int,
    active_count: int,
) -> None:
    avg_bpm = (sum_bpm / count) if count else None
    variance = None
    if count and avg_bpm is not None:
        mean_square = sumsq_bpm / count
        variance = max(mean_square - (avg_bpm * avg_bpm), 0.0)
    volatility = math.sqrt(variance) if variance is not None else None

    context_total = rest_count + active_count
    rest_ratio = (rest_count / context_total) if context_total else None
    active_ratio = (active_count / context_total) if context_total else None

    record = {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "avg_bpm": avg_bpm,
        "volatility": volatility,
        "rest_ratio": rest_ratio,
        "active_ratio": active_ratio,
        "sample_count": count,
    }
    out_file.write(json.dumps(record, separators=(",", ":")) + "\n")


def _write_windows(
    samples: Iterator[Tuple[datetime, float, Optional[str]]],
    out_file,
    window_minutes: int,
) -> int:
    """
    Aggregate samples into fixed 5-minute windows while streaming.

    Returns:
        Number of windows written.
    """
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    count = 0
    sum_bpm = 0.0
    sumsq_bpm = 0.0
    rest_count = 0
    active_count = 0
    windows_written = 0
    delta = timedelta(minutes=window_minutes)

    for timestamp, bpm, motion in samples:
        if window_start is None or window_end is None:
            window_start, window_end = _floor_window(timestamp, window_minutes)

        # If the sample lands beyond the current window, flush forward until it fits.
        while window_end is not None and timestamp >= window_end:
            _flush_window(
                out_file,
                window_start,
                window_end,
                count,
                sum_bpm,
                sumsq_bpm,
                rest_count,
                active_count,
            )
            windows_written += 1
            window_start += delta
            window_end += delta
            count = 0
            sum_bpm = 0.0
            sumsq_bpm = 0.0
            rest_count = 0
            active_count = 0

        count += 1
        sum_bpm += bpm
        sumsq_bpm += bpm * bpm
        if motion == "rest":
            rest_count += 1
        elif motion == "active":
            active_count += 1

    if window_start is not None and window_end is not None:
        _flush_window(
            out_file,
            window_start,
            window_end,
            count,
            sum_bpm,
            sumsq_bpm,
            rest_count,
            active_count,
        )
        windows_written += 1

    return windows_written


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preprocess_healthkit(
    raw_root: Path = DEFAULT_RAW_ROOT,
    derived_root: Path = DEFAULT_DERIVED_ROOT,
    window_minutes: int = 5,
) -> Path:
    """
    Stream a HealthKit export into aggregated heart-rate windows.

    Returns:
        Path to the written timeseries JSONL.
    """
    raw_root = Path(raw_root)
    derived_root = Path(derived_root)
    if not raw_root.exists() and FALLBACK_RAW_ROOT.exists():
        raw_root = FALLBACK_RAW_ROOT
    if not derived_root.exists() and FALLBACK_DERIVED_ROOT.exists():
        derived_root = FALLBACK_DERIVED_ROOT

    export_path = raw_root / EXPORT_FILENAME
    if not export_path.exists():
        raise FileNotFoundError(f"Missing export.xml at {export_path}")

    derived_root.mkdir(parents=True, exist_ok=True)
    timeseries_path = derived_root / TIMESERIES_FILENAME
    metadata_path = derived_root / METADATA_FILENAME

    stats = ParseStats()
    with timeseries_path.open("w", encoding="utf-8") as out:
        windows_written = _write_windows(_iter_heart_rate(export_path, stats), out, window_minutes)

    metadata = {
        "export_path": str(export_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_minutes": window_minutes,
        "heart_rate_samples": stats.heart_rate_samples,
        "skipped_records": stats.skipped_records,
        "windows_written": windows_written,
        "source_hash": _sha256_file(export_path),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True))
    return timeseries_path


def main() -> None:
    preprocess_healthkit()


if __name__ == "__main__":
    main()
