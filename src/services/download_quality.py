"""Download profiles shared by persistence, workers and media caches."""

DEFAULT_QUALITY = "standard"
QUALITY_RESOLUTIONS = {DEFAULT_QUALITY: 480, "720": 720, "1080": 1080}


def validate_quality(quality: str) -> str:
    if quality not in QUALITY_RESOLUTIONS:
        raise ValueError(f"Unsupported download quality: {quality}")
    return quality
