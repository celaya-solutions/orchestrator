from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# Preferred absolute dataset locations; fall back to repo-local copies if present.
DEFAULT_RAW_ROOT = Path("/data/datasets/raw/transcripts")
DEFAULT_DERIVED_ROOT = Path("/data/datasets/derived")
FALLBACK_RAW_ROOT = Path("data/datasets/raw/transcripts")
FALLBACK_DERIVED_ROOT = Path("data/datasets/derived")

VOICE_TIMESERIES_FILENAME = "voice_timeseries.jsonl"
VOICE_METADATA_FILENAME = "voice_metadata.json"
HEALTHKIT_TIMESERIES_FILENAME = "healthkit_timeseries.jsonl"

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "if",
    "then",
    "else",
    "when",
    "while",
    "of",
    "in",
    "on",
    "for",
    "to",
    "with",
    "at",
    "by",
    "from",
    "as",
    "is",
    "it",
    "this",
    "that",
    "these",
    "those",
    "are",
    "be",
    "was",
    "were",
    "been",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "not",
    "no",
    "so",
    "we",
    "you",
    "i",
}


@dataclass
class Segment:
    id: str
    text: str
    path: Path
    start: Optional[datetime]
    end: Optional[datetime]

    @property
    def timestamped(self) -> bool:
        return self.start is not None


def _load_json(path: Path) -> Optional[object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_iso_datetime(value: str, tz_hint: timezone) -> Optional[datetime]:
    value = value.strip()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz_hint)
    return dt


def _parse_timecode(value: str) -> Optional[float]:
    match = re.match(
        r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})([.,](?P<ms>\d{1,3}))?", value.strip()
    )
    if not match:
        return None
    hours = int(match.group("h"))
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    ms = int(match.group("ms") or "0")
    return hours * 3600 + minutes * 60 + seconds + (ms / 1000.0)


def _detect_datetime_from_filename(path: Path, tz_hint: timezone) -> Optional[datetime]:
    patterns = [
        r"\d{4}-\d{2}-\d{2}[T_\-]\d{2}[:\-]\d{2}[:\-]?\d{0,2}",
        r"\d{8}T\d{6}",
        r"\d{8}_\d{6}",
        r"\d{4}-\d{2}-\d{2}",
    ]
    name = path.name
    for pattern in patterns:
        match = re.search(pattern, name)
        if not match:
            continue
        candidate = match.group(0)
        formats = [
            "%Y-%m-%dT%H-%M-%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d_%H-%M-%S",
            "%Y-%m-%d-%H-%M-%S",
            "%Y-%m-%d_%H:%M:%S",
            "%Y-%m-%d-%H:%M:%S",
            "%Y%m%dT%H%M%S",
            "%Y%m%d_%H%M%S",
            "%Y-%m-%d",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(candidate, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=tz_hint)
                return dt
            except ValueError:
                continue
    return None


def _find_sidecar_metadata(path: Path) -> Optional[Path]:
    candidates = [
        path.with_suffix(path.suffix + ".metadata.json"),
        path.with_suffix(".metadata.json"),
    ]
    if path.suffix not in (".json", ".jsonl"):
        candidates.append(path.with_suffix(".json"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _extract_base_time(metadata: Optional[object], tz_hint: timezone) -> Optional[datetime]:
    if not isinstance(metadata, dict):
        return None
    for key in ("timestamp", "start_time", "datetime", "recorded_at"):
        value = metadata.get(key)
        if not value:
            continue
        if isinstance(value, str):
            dt = _parse_iso_datetime(value, tz_hint)
            if dt:
                return dt
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value), tz_hint)
            except Exception:
                continue
    return None


def _coerce_time(
    raw: object, base_time: Optional[datetime], tz_hint: timezone
) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, str):
        dt = _parse_iso_datetime(raw, tz_hint)
        if dt:
            return dt
        seconds = _parse_timecode(raw)
        if seconds is not None and base_time is not None:
            return base_time + timedelta(seconds=seconds)
        return None
    if isinstance(raw, (int, float)):
        if base_time is not None:
            return base_time + timedelta(seconds=float(raw))
        try:
            return datetime.fromtimestamp(float(raw), tz_hint)
        except Exception:
            return None
    return None


def _tokenize_words(text: str) -> List[str]:
    return re.findall(r"\b[\w']+\b", text.lower())


def _token_count(text: str) -> int:
    return len(re.findall(r"\w+|[^\w\s]", text))


def _lexical_entropy(words: Sequence[str]) -> float:
    if not words:
        return 0.0
    freq: Dict[str, int] = {}
    for word in words:
        freq[word] = freq.get(word, 0) + 1
    total = float(len(words))
    entropy = 0.0
    for count in freq.values():
        p = count / total
        entropy -= p * math.log2(p)
    return entropy


def _top_keywords(words: Sequence[str], limit: int = 10) -> List[str]:
    freq: Dict[str, int] = {}
    for word in words:
        if word in STOPWORDS:
            continue
        freq[word] = freq.get(word, 0) + 1
    ranked = sorted(freq.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ranked[:limit]]


def _interval_coverage_seconds(intervals: List[Tuple[datetime, datetime]]) -> float:
    if not intervals:
        return 0.0
    merged: List[Tuple[datetime, datetime]] = []
    for start, end in sorted(intervals, key=lambda it: it[0]):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    total = 0.0
    for start, end in merged:
        total += (end - start).total_seconds()
    return total


def _sha256_file(path: Path, digest: Optional[hashlib._Hash] = None) -> str:
    close_overall = digest is None
    digest = digest or hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    if close_overall:
        return digest.hexdigest()
    return ""


def _load_healthkit_windows(
    path: Path, tz_hint: timezone
) -> List[Tuple[datetime, datetime, str, str]]:
    windows: List[Tuple[datetime, datetime, str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except Exception:
            continue
        start_raw = record.get("window_start")
        end_raw = record.get("window_end")
        if not start_raw or not end_raw:
            continue
        start_dt = _parse_iso_datetime(start_raw, tz_hint)
        end_dt = _parse_iso_datetime(end_raw, tz_hint)
        if start_dt is None or end_dt is None:
            continue
        windows.append((start_dt, end_dt, start_raw, end_raw))
    return windows


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _parse_plaintext(
    path: Path, base_time: Optional[datetime], tz_hint: timezone
) -> List[Segment]:
    text = _read_text_file(path)
    if not text.strip():
        return []
    start = base_time
    end = None
    return [Segment(id=f"{path.stem}-0", text=text, path=path, start=start, end=end)]


def _parse_srt_or_vtt(
    path: Path, base_time: Optional[datetime], tz_hint: timezone
) -> List[Segment]:
    content = _read_text_file(path)
    if not content.strip():
        return []
    blocks = re.split(r"\n\s*\n", content.strip())
    segments: List[Segment] = []
    cue_idx = 0
    for block in blocks:
        lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if lines[0].upper().startswith("WEBVTT"):
            lines = lines[1:]
        if not lines:
            continue
        time_line = None
        for line in lines:
            if "-->" in line:
                time_line = line
                break
        if not time_line:
            continue
        try:
            raw_start, raw_end = [part.strip() for part in time_line.split("-->")]
        except ValueError:
            continue
        start_seconds = _parse_timecode(raw_start)
        end_seconds = _parse_timecode(raw_end)
        if start_seconds is None:
            continue
        start_dt = base_time + timedelta(seconds=start_seconds) if base_time else None
        end_dt = None
        if end_seconds is not None:
            end_dt = base_time + timedelta(seconds=end_seconds) if base_time else None
        # Remaining lines after the timecode are text
        text_lines = []
        seen_timecode = False
        for line in lines:
            if not seen_timecode and "-->" in line:
                seen_timecode = True
                continue
            if seen_timecode:
                text_lines.append(line)
        text = "\n".join(text_lines).strip()
        if not text:
            continue
        segments.append(
            Segment(
                id=f"{path.stem}-{cue_idx}",
                text=text,
                path=path,
                start=start_dt,
                end=end_dt,
            )
        )
        cue_idx += 1
    return segments


def _segments_from_sequence(
    seq: Iterable[object], path: Path, base_time: Optional[datetime], tz_hint: timezone
) -> List[Segment]:
    segments: List[Segment] = []
    for idx, entry in enumerate(seq):
        if isinstance(entry, dict):
            text = str(entry.get("text", "")).strip()
            if not text:
                continue
            start = _coerce_time(
                entry.get("start", entry.get("timestamp")), base_time, tz_hint
            )
            end = _coerce_time(entry.get("end"), base_time, tz_hint)
            if start is None and base_time is not None:
                start = base_time
            seg_id = str(entry.get("id", f"{path.stem}-{idx}"))
            segments.append(Segment(id=seg_id, text=text, path=path, start=start, end=end))
        else:
            text = str(entry).strip()
            if text:
                segments.append(
                    Segment(id=f"{path.stem}-{idx}", text=text, path=path, start=base_time, end=None)
                )
    return segments


def _parse_json_file(
    path: Path, base_time: Optional[datetime], tz_hint: timezone
) -> List[Segment]:
    data = _load_json(path)
    if data is None:
        return []
    if isinstance(data, list):
        return _segments_from_sequence(data, path, base_time, tz_hint)
    if isinstance(data, dict):
        inner_base = _extract_base_time(data, tz_hint) or base_time
        if "segments" in data and isinstance(data["segments"], list):
            return _segments_from_sequence(data["segments"], path, inner_base, tz_hint)
        if "text" in data:
            text = str(data.get("text", "")).strip()
            if not text:
                return []
            start = _coerce_time(data.get("timestamp") or data.get("start"), inner_base, tz_hint)
            end = _coerce_time(data.get("end"), inner_base, tz_hint)
            if start is None and inner_base is not None:
                start = inner_base
            return [
                Segment(
                    id=str(data.get("id", path.stem)),
                    text=text,
                    path=path,
                    start=start,
                    end=end,
                )
            ]
    return []


def _parse_jsonl_file(
    path: Path, base_time: Optional[datetime], tz_hint: timezone
) -> List[Segment]:
    segments: List[Segment] = []
    for line in _read_text_file(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        if isinstance(record, dict) and "segments" in record and isinstance(record["segments"], list):
            inner_base = _extract_base_time(record, tz_hint) or base_time
            segments.extend(_segments_from_sequence(record["segments"], path, inner_base, tz_hint))
        else:
            segments.extend(_segments_from_sequence([record], path, base_time, tz_hint))
    return segments


def _aggregate_segments_by_window(
    windows: List[Tuple[datetime, datetime, str, str]], segments: List[Segment]
) -> List[List[Segment]]:
    assignments: List[List[Segment]] = [[] for _ in windows]
    timed_segments = [s for s in segments if s.start is not None]
    timed_segments.sort(key=lambda s: (s.start, s.id))
    window_idx = 0
    for seg in timed_segments:
        seg_start = seg.start or datetime.min.replace(tzinfo=timezone.utc)
        seg_end = seg.end or (seg_start + timedelta(microseconds=1))
        while window_idx < len(windows) and windows[window_idx][1] <= seg_start:
            window_idx += 1
        idx = window_idx
        while idx < len(windows) and windows[idx][0] < seg_end:
            assignments[idx].append(seg)
            idx += 1
    return assignments


def _compute_window_record(
    window: Tuple[datetime, datetime, str, str],
    window_segments: List[Segment],
    prev_embedding: Optional[List[float]],
    embedder: Optional[Callable[[List[str]], Sequence[Sequence[float]]]],
) -> Tuple[dict, Optional[List[float]]]:
    start_dt, end_dt, start_raw, end_raw = window
    window_texts: List[str] = []
    words: List[str] = []
    intervals: List[Tuple[datetime, datetime]] = []
    timed_words = 0

    for seg in window_segments:
        text = seg.text.strip()
        if not text:
            continue
        window_texts.append(text)
        seg_words = _tokenize_words(text)
        words.extend(seg_words)
        if seg.start is not None and seg.end is not None:
            clipped_start = max(seg.start, start_dt)
            clipped_end = min(seg.end, end_dt)
            if clipped_end > clipped_start:
                intervals.append((clipped_start, clipped_end))
                timed_words += len(seg_words)

    token_count = sum(_token_count(text) for text in window_texts)
    word_count = len(words)
    unique_word_ratio = (len(set(words)) / word_count) if word_count else 0.0
    lexical_entropy = _lexical_entropy(words)
    spoken_seconds = _interval_coverage_seconds(intervals)
    window_seconds = (end_dt - start_dt).total_seconds()

    speech_rate = None
    pause_density = None
    if spoken_seconds > 0:
        speech_rate = (timed_words / (spoken_seconds / 60.0)) if timed_words else None
        pause_density = (
            max(window_seconds - spoken_seconds, 0.0) / window_seconds if window_seconds else None
        )
    elif intervals:
        pause_density = 1.0 if window_seconds else None

    keywords = _top_keywords(words)
    concatenated = " ".join(window_texts)
    excerpt_hash = hashlib.sha256(concatenated.encode("utf-8")).hexdigest()

    window_embedding: Optional[List[float]] = None
    embedding_drift = None
    if embedder and window_texts:
        try:
            embeddings = embedder([concatenated])
            if embeddings:
                window_embedding = embeddings[0]
        except Exception:
            window_embedding = None
    if window_embedding is not None and prev_embedding is not None:
        dot = sum(a * b for a, b in zip(window_embedding, prev_embedding))
        norm_a = math.sqrt(sum(a * a for a in window_embedding))
        norm_b = math.sqrt(sum(b * b for b in prev_embedding))
        if norm_a and norm_b:
            cosine = dot / (norm_a * norm_b)
            embedding_drift = 1 - cosine

    path_map: Dict[str, List[str]] = {}
    for seg in window_segments:
        key = str(seg.path)
        path_map.setdefault(key, []).append(seg.id)
    source_refs = [
        {"path": path, "segment_ids": sorted(ids)} for path, ids in sorted(path_map.items())
    ]

    record = {
        "window_start": start_raw,
        "window_end": end_raw,
        "token_count": token_count,
        "word_count": word_count,
        "speech_rate_wpm": speech_rate,
        "pause_density": pause_density,
        "segment_count": len(window_segments),
        "unique_word_ratio": unique_word_ratio,
        "lexical_entropy": lexical_entropy,
        "embedding_drift": embedding_drift,
        "top_keywords": keywords,
        "excerpt_hash": excerpt_hash,
        "source_refs": source_refs,
    }
    return record, window_embedding


def preprocess_transcripts(
    raw_root: Path = DEFAULT_RAW_ROOT,
    derived_root: Path = DEFAULT_DERIVED_ROOT,
    window_minutes: int = 5,
    embedder: Optional[Callable[[List[str]], Sequence[Sequence[float]]]] = None,
) -> Path:
    """
    Convert raw transcript files into HealthKit-aligned voice windows.

    Returns:
        Path to the written voice_timeseries JSONL.
    """
    raw_root = Path(raw_root)
    derived_root = Path(derived_root)
    if not raw_root.exists() and FALLBACK_RAW_ROOT.exists():
        raw_root = FALLBACK_RAW_ROOT
    if not derived_root.exists() and FALLBACK_DERIVED_ROOT.exists():
        derived_root = FALLBACK_DERIVED_ROOT

    healthkit_path = derived_root / HEALTHKIT_TIMESERIES_FILENAME
    if not healthkit_path.exists():
        raise FileNotFoundError(f"Missing healthkit_timeseries.jsonl at {healthkit_path}")

    tz_hint = timezone.utc
    windows = _load_healthkit_windows(healthkit_path, tz_hint)
    if not windows:
        raise ValueError("healthkit_timeseries.jsonl is empty or invalid.")
    tz_hint = windows[0][0].tzinfo or timezone.utc

    transcript_paths: List[Path] = []
    if raw_root.exists():
        for path in sorted(raw_root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".txt", ".md", ".json", ".jsonl", ".srt", ".vtt"}:
                continue
            transcript_paths.append(path)

    segments: List[Segment] = []
    for path in transcript_paths:
        sidecar_path = _find_sidecar_metadata(path)
        sidecar = _load_json(sidecar_path) if sidecar_path else None
        base_time = _extract_base_time(sidecar, tz_hint) or _detect_datetime_from_filename(path, tz_hint)

        if path.suffix.lower() in {".txt", ".md"}:
            parsed = _parse_plaintext(path, base_time, tz_hint)
        elif path.suffix.lower() == ".json":
            parsed = _parse_json_file(path, base_time, tz_hint)
        elif path.suffix.lower() == ".jsonl":
            parsed = _parse_jsonl_file(path, base_time, tz_hint)
        elif path.suffix.lower() in {".srt", ".vtt"}:
            parsed = _parse_srt_or_vtt(path, base_time, tz_hint)
        else:
            parsed = []

        segments.extend(parsed)

    assignments = _aggregate_segments_by_window(windows, segments)
    assigned_ids = {id(seg) for bucket in assignments for seg in bucket}
    unassigned_count = len([seg for seg in segments if id(seg) not in assigned_ids])

    derived_root.mkdir(parents=True, exist_ok=True)
    timeseries_path = derived_root / VOICE_TIMESERIES_FILENAME
    metadata_path = derived_root / VOICE_METADATA_FILENAME

    previous_embedding: Optional[List[float]] = None
    with timeseries_path.open("w", encoding="utf-8") as out:
        for window, window_segments in zip(windows, assignments):
            record, window_embedding = _compute_window_record(
                window, window_segments, previous_embedding, embedder
            )
            if window_embedding is not None:
                previous_embedding = window_embedding
            out.write(json.dumps(record, separators=(",", ":")) + "\n")

    segments_with_timestamps = sum(1 for s in segments if s.timestamped)
    window_minutes = int((windows[0][1] - windows[0][0]).total_seconds() // 60) or window_minutes
    source_files = [str(path) for path in transcript_paths]

    digest = hashlib.sha256()
    _sha256_file(healthkit_path, digest)
    for path in transcript_paths:
        _sha256_file(path, digest)

    metadata = {
        "source_files": source_files,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_size_minutes": window_minutes,
        "segment_counts": {
            "segments_total": len(segments),
            "segments_with_timestamps": segments_with_timestamps,
        },
        "record_counts": {"windows": len(windows)},
        "hash": digest.hexdigest(),
    }
    if unassigned_count:
        metadata["segment_counts"]["segments_unassigned"] = unassigned_count

    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True))
    return timeseries_path


def main() -> None:
    preprocess_transcripts()


if __name__ == "__main__":
    main()
