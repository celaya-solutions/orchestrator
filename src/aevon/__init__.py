"""AEVON utilities."""

# Lazy exports to avoid eager module import when running as `python -m aevon.*`.
def preprocess_healthkit(*args, **kwargs):  # type: ignore[override]
    from .preprocess_healthkit import preprocess_healthkit as _preprocess_healthkit

    return _preprocess_healthkit(*args, **kwargs)


def preprocess_transcripts(*args, **kwargs):  # type: ignore[override]
    from .preprocess_transcripts import preprocess_transcripts as _preprocess_transcripts

    return _preprocess_transcripts(*args, **kwargs)


__all__ = ["preprocess_healthkit", "preprocess_transcripts"]
