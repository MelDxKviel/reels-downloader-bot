"""
Pure URL utility functions: normalization, platform detection, Instagram fallback logic.
"""

import hashlib
import re
from typing import Optional
from urllib.parse import urlparse, urlunparse

SUPPORTED_PATTERNS = [
    r"(youtube\.com|youtu\.be)",
    r"(instagram\.com/reel|instagram\.com/p)",
    r"(tiktok\.com|vm\.tiktok\.com)",
    r"(twitter\.com|x\.com)",
]

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


def normalize_url(url: str) -> str:
    """Strip tracking parameters (utm_*, si, feature, ref) from a URL."""
    return re.sub(r"[?&](utm_\w+|si|feature|ref)=[^&]*", "", url)


def get_url_hash(url: str) -> str:
    """Return a 16-char MD5 hex digest of the normalized URL."""
    return hashlib.md5(normalize_url(url).encode()).hexdigest()[:16]


def is_supported_url(url: str) -> bool:
    for pattern in SUPPORTED_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False


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
