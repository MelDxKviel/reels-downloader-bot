"""
Pure URL utility functions: normalization, platform detection, Instagram fallback logic.
"""

import hashlib
import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Hostnames (and their subdomains) that we will accept for downloading.
# Matched against the parsed URL host, never against the raw string, so
# look-alikes like ``youtube.com.evil.com`` cannot slip through.
_SUPPORTED_HOSTS = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "instagram.com",
        "tiktok.com",
        "twitter.com",
        "x.com",
    }
)

INSTAGRAM_AUTH_ERROR_MARKERS = (
    "sign in",
    "login",
    "login_required",
    "requires authentication",
    "authentication required",
    "not logged in",
    "consent_required",
    "checkpoint_required",
)

# URL pattern shared by all text/inline handlers. The lookahead after the
# trusted host enforces a hostname boundary so look-alikes like
# ``https://m.youtube.com.evil.com/path`` do NOT match as supported URLs —
# without it the regex would greedily include the evil suffix in
# ``[^\s<>"\']*`` and the match would reach the downloader.
URL_PATTERN = re.compile(
    r"https?://(?:[\w-]+\.)*"
    r"(?:youtube\.com|youtu\.be|instagram\.com|kkinstagram\.com"
    r"|tiktok\.com|twitter\.com|x\.com)"
    r"(?=[/:?#]|$)"
    r'[^\s<>"\']*',
    re.IGNORECASE,
)

# Punctuation we strip from the tail of a captured URL. Users commonly write
# sentences like "смотри https://x.com/abc, круто", and the regex above would
# otherwise greedily include the trailing comma/dot/quote.
_TRAILING_URL_PUNCT = ".,;:!?)\"'»>]}"

_TRACKING_PARAM_NAMES = frozenset({"si", "feature", "ref"})


def _is_tracking_param(name: str) -> bool:
    return name.startswith("utm_") or name in _TRACKING_PARAM_NAMES


def normalize_url(url: str) -> str:
    """Strip tracking parameters (utm_*, si, feature, ref) from a URL.

    Uses a proper URL parser so that removing the first query parameter does
    not leave a dangling ``&`` (the previous regex-only version produced
    invalid URLs like ``?utm=x&v=y`` → ``&v=y``).
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if not parsed.query:
        return url
    filtered = [
        (name, value)
        for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_param(name)
    ]
    new_query = urlencode(filtered)
    return urlunparse(parsed._replace(query=new_query))


def extract_url(text: Optional[str]) -> Optional[str]:
    """Return the first supported URL in ``text`` with trailing punctuation stripped.

    Returns ``None`` if no match is found.
    """
    if not text:
        return None
    match = URL_PATTERN.search(text)
    if not match:
        return None
    url = match.group(0)
    while url and url[-1] in _TRAILING_URL_PUNCT:
        url = url[:-1]
    return url or None


def get_url_hash(url: str) -> str:
    """Return a 16-char MD5 hex digest of the normalized URL."""
    return hashlib.md5(normalize_url(url).encode()).hexdigest()[:16]


def is_supported_url(url: str) -> bool:
    """True if ``url`` points at a host we know how to download from.

    Uses ``urlparse`` so look-alike domains (``youtube.com.evil.com``) are
    rejected — substring matching is not safe here.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(host == base or host.endswith("." + base) for base in _SUPPORTED_HOSTS)


def get_platform_name(url: str) -> str:
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "YouTube"
    elif "instagram.com" in url_lower:
        return "Instagram"
    elif "tiktok.com" in url_lower:
        return "TikTok"
    elif "twitter.com" in url_lower or "x.com" in url_lower:
        return "X/Twitter"
    return "Unknown"


def is_youtube_url(url: str) -> bool:
    url_lower = url.lower()
    return "youtube.com" in url_lower or "youtu.be" in url_lower


def is_instagram_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "instagram.com" or host.endswith(".instagram.com")


def is_instagram_post_url(url: str) -> bool:
    """True for Instagram URLs pointing to a single post/reel/tv item."""
    if not (is_instagram_url(url) or is_kkinstagram_url(url)):
        return False
    path = urlparse(url).path or ""
    return bool(re.search(r"/(?:p|reel|reels|tv)/[^/?#]+", path, re.IGNORECASE))


def is_instagram_photo_candidate_url(url: str) -> bool:
    """
    True only для URL вида /p/<shortcode> — такие посты могут быть фото или
    каруселью фото. /reel/, /reels/ и /tv/ всегда видео, проверять их на
    фото бессмысленно.
    """
    if not (is_instagram_url(url) or is_kkinstagram_url(url)):
        return False
    path = urlparse(url).path or ""
    return bool(re.search(r"/p/[^/?#]+", path, re.IGNORECASE))


def is_kkinstagram_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "kkinstagram.com" or host.endswith(".kkinstagram.com")


def build_kkinstagram_url(url: str) -> Optional[str]:
    if not is_instagram_url(url):
        return None
    parsed = urlparse(url)
    return urlunparse(parsed._replace(netloc="kkinstagram.com"))


def should_retry_with_kkinstagram(url: str, error_msg_lower: str) -> bool:
    if is_kkinstagram_url(url) or not is_instagram_url(url):
        return False
    return any(marker in error_msg_lower for marker in INSTAGRAM_AUTH_ERROR_MARKERS)
