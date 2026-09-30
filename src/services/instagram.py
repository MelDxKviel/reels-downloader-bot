"""Instagram implementation for the downloader."""

import html as html_lib
import http.cookiejar
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlparse

from src.services.media import (
    _IMAGE_EXTENSIONS,
    _INSTAGRAM_APP_ID,
    _INSTAGRAM_ASBD_ID,
    _INSTAGRAM_SHORTCODE_ALPHABET,
    BROWSER_USER_AGENT,
    MAX_CAROUSEL_ITEMS,
    CarouselSlide,
    DownloadResult,
    _download_retry_delay,
    _is_instagram_cdn_host,
)
from src.services.url_utils import (
    build_kkinstagram_url,
    is_instagram_photo_candidate_url,
    is_instagram_url,
    is_kkinstagram_url,
)

logger = logging.getLogger(__name__)


class _InstagramScriptParser(HTMLParser):
    """Collect script bodies using HTML tag boundaries, preserving JSON escapes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.scripts: list[str] = []
        self._script_chunks: Optional[list[str]] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "script":
            self._script_chunks = []

    def handle_data(self, data: str) -> None:
        if self._script_chunks is not None:
            self._script_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_chunks is not None:
            self.scripts.append("".join(self._script_chunks))
            self._script_chunks = None


class InstagramMixin:
    @staticmethod
    def _instagram_shortcode_to_media_id(shortcode: str) -> Optional[str]:
        """Decode an Instagram shortcode into the numeric product API media ID."""

        if not shortcode:
            return None
        # Instagram sometimes appends a 28-character private-post suffix. This
        # mirrors yt-dlp's decoder while the exact returned ``code`` is still
        # validated before any API payload is trusted.
        shortcode = InstagramMixin._instagram_canonical_shortcode(shortcode)

        media_id = 0
        for character in shortcode:
            digit = _INSTAGRAM_SHORTCODE_ALPHABET.find(character)
            if digit < 0:
                return None
            media_id = media_id * len(_INSTAGRAM_SHORTCODE_ALPHABET) + digit
        return str(media_id)

    @staticmethod
    def _instagram_canonical_shortcode(shortcode: str) -> str:
        return shortcode[:-28] if len(shortcode) > 28 else shortcode

    def _fetch_instagram_product_info(
        self,
        shortcode: str,
        cookie_jar: http.cookiejar.CookieJar,
    ) -> Optional[dict]:
        """Fetch exact authenticated media metadata used by yt-dlp itself."""

        if not any(cookie.name == "sessionid" and cookie.value for cookie in cookie_jar):
            return None
        media_id = self._instagram_shortcode_to_media_id(shortcode)
        if not media_id:
            return None

        api_url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
        for retry_index in range(3):
            try:
                request = urllib.request.Request(
                    api_url,
                    headers={
                        "User-Agent": BROWSER_USER_AGENT,
                        "Accept": "*/*",
                        "X-IG-App-ID": _INSTAGRAM_APP_ID,
                        "X-ASBD-ID": _INSTAGRAM_ASBD_ID,
                        "X-IG-WWW-Claim": "0",
                        "Origin": "https://www.instagram.com",
                        "Referer": f"https://www.instagram.com/p/{shortcode}/",
                    },
                )
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
                with opener.open(request, timeout=15) as response:
                    payload = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
            except urllib.error.HTTPError as exc:
                is_transient = exc.code in {408, 425, 429} or 500 <= exc.code < 600
                if is_transient and retry_index < 2:
                    delay = _download_retry_delay(retry_index)
                    logger.warning(
                        "Временная Instagram API HTTP %s, повтор через %ss",
                        exc.code,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                logger.debug("Instagram product API failed for %s: %s", shortcode, exc)
                return None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if retry_index < 2:
                    delay = _download_retry_delay(retry_index)
                    logger.warning(
                        "Временная ошибка Instagram API, повтор через %ss: %s",
                        delay,
                        exc,
                    )
                    time.sleep(delay)
                    continue
                logger.debug("Instagram product API failed for %s: %s", shortcode, exc)
                return None
            except (UnicodeDecodeError, ValueError) as exc:
                logger.debug("Invalid Instagram product API response for %s: %s", shortcode, exc)
                return None

            items = payload.get("items") if isinstance(payload, dict) else None
            product = items[0] if isinstance(items, list) and items else None
            canonical_shortcode = self._instagram_canonical_shortcode(shortcode)
            if not isinstance(product, dict) or product.get("code") != canonical_shortcode:
                logger.warning("Instagram product API returned mismatched media for %s", shortcode)
                return None
            return product
        return None

    @staticmethod
    def _instagram_product_media_kind(product: dict) -> str:
        """Classify exact product metadata without mistaking a video cover for a photo."""

        def node_kind(node: object) -> str:
            if not isinstance(node, dict):
                return "unknown"
            media_type = str(node.get("media_type") or "")
            if (
                media_type == "2"
                or bool(node.get("video_versions"))
                or bool(node.get("video_dash_manifest"))
            ):
                return "video"
            if media_type == "1" and isinstance(node.get("image_versions2"), dict):
                return "photo"
            return "unknown"

        root_media_type = str(product.get("media_type") or "")
        if root_media_type != "8":
            return node_kind(product)

        children = product.get("carousel_media")
        if not isinstance(children, list) or not children:
            return "unknown"
        child_kinds = [node_kind(child) for child in children]
        if "video" in child_kinds:
            return "video"
        if all(kind == "photo" for kind in child_kinds):
            return "photo"
        return "unknown"

    @classmethod
    def _instagram_product_media_urls(cls, product: dict) -> tuple[list[str], Optional[str]]:
        """Extract ordered full-size image URLs and the first video URL from product JSON."""

        children = product.get("carousel_media")
        nodes = children if isinstance(children, list) and children else [product]
        image_urls: list[str] = []
        video_url: Optional[str] = None

        for node in nodes[:MAX_CAROUSEL_ITEMS]:
            if not isinstance(node, dict):
                continue
            image_versions = node.get("image_versions2")
            candidates = (
                image_versions.get("candidates") if isinstance(image_versions, dict) else None
            )
            ranked_images: list[tuple[int, str]] = []
            for candidate in candidates if isinstance(candidates, list) else []:
                if not isinstance(candidate, dict):
                    continue
                image_url = candidate.get("url")
                if not isinstance(image_url, str):
                    continue
                if os.path.splitext(urlparse(image_url).path)[1].lower() not in _IMAGE_EXTENSIONS:
                    continue
                try:
                    area = int(candidate.get("width") or 0) * int(candidate.get("height") or 0)
                except (TypeError, ValueError):
                    area = 0
                ranked_images.append((area, image_url))
            if ranked_images:
                # Exactly one selected image per product child. Do not dedupe:
                # repeated carousel slides are still distinct ordered items.
                image_urls.append(max(ranked_images, key=lambda item: item[0])[1])

            if video_url is None:
                versions = node.get("video_versions")
                ranked_videos: list[tuple[int, str]] = []
                for version in versions if isinstance(versions, list) else []:
                    if not isinstance(version, dict) or not isinstance(version.get("url"), str):
                        continue
                    try:
                        area = int(version.get("width") or 0) * int(version.get("height") or 0)
                    except (TypeError, ValueError):
                        area = 0
                    ranked_videos.append((area, version["url"]))
                if ranked_videos:
                    video_url = max(ranked_videos, key=lambda item: item[0])[1]

        return image_urls, video_url

    @classmethod
    def _instagram_product_to_media_info(cls, product: dict) -> Optional[dict]:
        media_kind = cls._instagram_product_media_kind(product)
        if media_kind == "unknown":
            return None

        image_urls, video_url = cls._instagram_product_media_urls(product)
        if media_kind == "photo":
            children = product.get("carousel_media")
            expected_images = (
                min(len(children), MAX_CAROUSEL_ITEMS)
                if isinstance(children, list) and children
                else 1
            )
            if len(image_urls) != expected_images:
                # Never silently flatten/truncate an authenticated carousel.
                return None

        caption = product.get("caption")
        caption_text = caption.get("text") if isinstance(caption, dict) else None
        user = product.get("user")
        username = user.get("username") if isinstance(user, dict) else None
        title = caption_text or (f"Post by {username}" if username else None)
        return {
            "image_urls": cls._limit_instagram_image_variants(image_urls),
            "image_urls_are_ordered_slides": True,
            "video_url": video_url,
            "has_video": media_kind == "video",
            "media_kind": media_kind,
            "title": title,
        }

    @staticmethod
    def _instagram_image_key(image_url: str) -> str:
        """Group signed/resized variants that point to the same Instagram asset."""

        parsed = urlparse(image_url)
        return f"{parsed.hostname or ''}{parsed.path}"

    @classmethod
    def _limit_instagram_image_variants(cls, image_urls: list[str]) -> list[str]:
        """Keep variants for the first N distinct slides without truncating slides."""

        selected_keys: set[str] = set()
        selected_urls: list[str] = []
        for image_url in image_urls:
            key = cls._instagram_image_key(image_url)
            if key not in selected_keys:
                if len(selected_keys) >= MAX_CAROUSEL_ITEMS:
                    continue
                selected_keys.add(key)
            selected_urls.append(image_url)
        return selected_urls

    @classmethod
    def _instagram_slide_count(cls, image_urls: list[str]) -> int:
        """Count distinct assets instead of signed/resized URL variants."""

        return len({cls._instagram_image_key(image_url) for image_url in image_urls})

    @classmethod
    def _extract_instagram_target_payload(
        cls, html: str, shortcode: Optional[str]
    ) -> Optional[str]:
        """Return the most complete JSON object belonging to ``shortcode``.

        Instagram's main page can contain media for recommendations as well as
        the requested post. Only dictionaries whose *own* ``shortcode``/``code``
        field matches are candidates; enclosing feed objects are never trusted.
        """

        if not shortcode:
            return None
        # Keep JSON escaping intact. In particular, blindly replacing ``\"``
        # corrupts perfectly valid captions containing quotes and can make the
        # complete target script impossible to parse.
        probe = html_lib.unescape(html)
        target_nodes: list[dict] = []

        def visit(value: object) -> None:
            if isinstance(value, dict):
                if value.get("shortcode") == shortcode or value.get("code") == shortcode:
                    target_nodes.append(value)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
            elif isinstance(value, str):
                stripped = value.strip()
                if shortcode in stripped and stripped[:1] in {"{", "["}:
                    try:
                        visit(json.loads(stripped))
                    except (ValueError, json.JSONDecodeError):
                        pass

        parser = _InstagramScriptParser()
        parser.feed(probe)
        parser.close()
        json_sources = parser.scripts
        stripped_probe = probe.strip()
        if stripped_probe[:1] in {"{", "["}:
            json_sources.append(stripped_probe)
        for source in json_sources:
            try:
                visit(json.loads(source.strip()))
            except (ValueError, json.JSONDecodeError):
                continue

        # Some pages wrap JSON in JavaScript assignments. Fall back to balanced
        # object extraction, but still accept only an object's own exact field.
        marker = re.compile(rf'"(?:shortcode|code)"\s*:\s*"{re.escape(shortcode)}"')
        for match in marker.finditer(probe):
            stack: list[int] = []
            in_string = False
            escaped = False
            for index, char in enumerate(probe):
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == "{":
                    stack.append(index)
                elif char == "}" and stack:
                    start = stack.pop()
                    if start <= match.start() < index:
                        payload = probe[start : index + 1]
                        try:
                            node = json.loads(payload)
                        except (ValueError, json.JSONDecodeError):
                            continue
                        if isinstance(node, dict) and (
                            node.get("shortcode") == shortcode or node.get("code") == shortcode
                        ):
                            target_nodes.append(node)

        if not target_nodes:
            return None

        def completeness(node: dict) -> tuple[int, int]:
            carousel_media = node.get("carousel_media")
            carousel_count = len(carousel_media) if isinstance(carousel_media, list) else 0
            sidecar = node.get("edge_sidecar_to_children")
            edges = sidecar.get("edges") if isinstance(sidecar, dict) else None
            if isinstance(edges, list):
                carousel_count = max(carousel_count, len(edges))
            serialized = json.dumps(node, ensure_ascii=False)
            parsed = cls._parse_instagram_html(serialized)
            image_count = cls._instagram_slide_count(parsed["image_urls"])
            return carousel_count, image_count

        best = max(target_nodes, key=completeness)
        return json.dumps(best, ensure_ascii=False)

    def _fetch_instagram_media_info(self, url: str) -> Optional[dict]:
        """
        Запрашивает Instagram-пост и собирает список изображений (для поддержки
        каруселей), URL видео и заголовок. Пробует несколько эндпоинтов:
        embed-страницу Instagram (там карусели рендерятся с несколькими <img>),
        основную страницу и зеркало kkinstagram.
        Возвращает dict с ключами image_urls (list), video_url, has_video, title
        или None, если ничего полезного не удалось извлечь.
        """
        shortcode = self._extract_ig_shortcode(url)
        cookie_jar = self._load_instagram_cookie_jar()

        # With session cookies yt-dlp uses this exact product API. Photo entries
        # intentionally contain no video formats, which newer yt-dlp versions
        # surface as a generic "No video formats found" error. Inspect the exact
        # target first so photo carousels (including /reel/ share URLs) are
        # positively identified before yt-dlp gets a chance to reject them.
        if shortcode and cookie_jar is not None:
            product = self._fetch_instagram_product_info(shortcode, cookie_jar)
            if product is not None:
                product_media = self._instagram_product_to_media_info(product)
                if product_media is not None:
                    return product_media

        # /reel/, /reels/ and /tv/ remain video by default. Only authoritative
        # product metadata above may prove that such a share URL is photographic;
        # unscoped HTML/og:image must never turn a failed video into its cover.
        if not is_instagram_photo_candidate_url(url):
            return None

        candidates: list[str] = []
        if shortcode:
            candidates.append(f"https://www.instagram.com/p/{shortcode}/embed/captioned")
            candidates.append(f"https://www.instagram.com/p/{shortcode}/embed/")
        candidates.append(url)
        kk = build_kkinstagram_url(url)
        if kk and kk not in candidates:
            candidates.append(kk)

        trusted_image_urls: list[str] = []
        target_main_image_urls: list[str] = []
        mirror_image_urls: list[str] = []
        fallback_image_urls: list[str] = []
        video_url: Optional[str] = None
        has_video_marker = False
        has_photo_marker = False
        title: Optional[str] = None

        for candidate in candidates:
            html = self._http_get_html(candidate, cookie_jar=cookie_jar)
            if not html:
                continue

            candidate_path = (urlparse(candidate).path or "").lower()
            is_target_embed = is_instagram_url(candidate) and "/embed" in candidate_path
            is_target_mirror = is_kkinstagram_url(candidate)
            target_payload = (
                self._extract_instagram_target_payload(html, shortcode)
                if is_instagram_url(candidate) and not is_target_embed
                else None
            )
            is_target_main = target_payload is not None
            is_trusted_target = is_target_embed or is_target_main or is_target_mirror
            parsed = self._parse_instagram_html(target_payload or html)
            if is_target_embed:
                image_urls = trusted_image_urls
            elif is_target_main:
                image_urls = target_main_image_urls
            elif is_target_mirror:
                image_urls = mirror_image_urls
            else:
                image_urls = fallback_image_urls
            for img in parsed["image_urls"]:
                if img not in image_urls:
                    image_urls.append(img)
            if parsed["video_url"] and not video_url:
                video_url = parsed["video_url"]
            if parsed.get("has_video_marker") and is_trusted_target:
                has_video_marker = True
            if parsed.get("media_kind") == "photo" and is_trusted_target:
                has_photo_marker = True
            if parsed["title"] and not title:
                title = parsed["title"]

            # Ранний выход: уже точно видео или уже набрали максимум слайдов —
            # дальше ходить по источникам бессмысленно. При 1–9 слайдах
            # продолжаем обходить все источники: один endpoint иногда отдаёт
            # только первый слайд, другой — полную карусель.
            if has_video_marker:
                break
            active_images = max(
                (trusted_image_urls, target_main_image_urls, mirror_image_urls),
                key=lambda urls: (self._instagram_slide_count(urls), len(urls)),
            )
            active_slide_count = self._instagram_slide_count(active_images)
            if has_photo_marker and active_slide_count >= MAX_CAROUSEL_ITEMS:
                break

        # Embed and the target-only mirror are safe carousel sources. Prefer the
        # more complete one; use the main page only if neither yielded media,
        # because its JSON can include recommendation thumbnails.
        trusted_sources = max(
            (trusted_image_urls, target_main_image_urls, mirror_image_urls),
            key=lambda urls: (self._instagram_slide_count(urls), len(urls)),
        )
        image_urls = trusted_sources or fallback_image_urls
        if not image_urls and not video_url:
            return None

        return {
            "image_urls": self._limit_instagram_image_variants(image_urls),
            "video_url": video_url,
            "has_video": has_video_marker,
            "media_kind": (
                "video" if has_video_marker else "photo" if has_photo_marker else "unknown"
            ),
            "title": title,
        }

    @staticmethod
    def _extract_ig_shortcode(url: str) -> Optional[str]:
        path = urlparse(url).path or ""
        m = re.search(r"/(?:p|reel|reels|tv)/([^/?#]+)", path, re.IGNORECASE)
        return m.group(1) if m else None

    @staticmethod
    def _http_get_html(
        candidate: str,
        cookie_jar: Optional[http.cookiejar.CookieJar] = None,
    ) -> Optional[str]:
        for retry_index in range(3):
            try:
                req = urllib.request.Request(
                    candidate,
                    headers={
                        "User-Agent": BROWSER_USER_AGENT,
                        "Accept": (
                            "text/html,application/xhtml+xml,application/xml;q=0.9,"
                            "image/webp,*/*;q=0.8"
                        ),
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                if cookie_jar is not None:
                    opener = urllib.request.build_opener(
                        urllib.request.HTTPCookieProcessor(cookie_jar)
                    )
                    ctx = opener.open(req, timeout=15)
                else:
                    ctx = urllib.request.urlopen(req, timeout=15)
                with ctx as resp:
                    content_type = (resp.headers.get("Content-Type") or "").lower()
                    if "html" not in content_type:
                        return None
                    raw = resp.read(4 * 1024 * 1024)
                return raw.decode("utf-8", errors="ignore")
            except urllib.error.HTTPError as exc:
                is_transient = exc.code in {408, 425, 429} or 500 <= exc.code < 600
                if is_transient and retry_index < 2:
                    delay = _download_retry_delay(retry_index)
                    logger.warning(
                        "Временная HTTP %s при HTML probe, повтор через %ss: %s",
                        exc.code,
                        delay,
                        candidate,
                    )
                    time.sleep(delay)
                    continue
                logger.debug("HTML fetch failed for %s: %s", candidate, exc)
                return None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if retry_index < 2:
                    delay = _download_retry_delay(retry_index)
                    logger.warning(
                        "Временная ошибка HTML probe, повтор через %ss: %s (%s)",
                        delay,
                        candidate,
                        exc,
                    )
                    time.sleep(delay)
                    continue
                logger.debug("HTML fetch failed for %s: %s", candidate, exc)
                return None
        return None

    @classmethod
    def _parse_instagram_html(cls, html: str) -> dict:
        """
        Возвращает dict: image_urls (list, порядок слайдов, дедуп),
        video_url, title.
        """
        image_urls: list[str] = []

        # Instagram's current product payload stores full-size covers/photos in
        # image_versions2.candidates. Keep the largest candidate first; unlike
        # og:image these retain the original portrait aspect ratio. A cover is
        # not proof that the post itself is a photo.
        json_probe = html_lib.unescape(html).replace(r"\"", '"')
        for block_match in re.finditer(
            r'"image_versions2"\s*:\s*\{.*?"candidates"\s*:\s*\[(.*?)\]',
            json_probe,
            re.DOTALL,
        ):
            block = block_match.group(1)
            candidates: list[tuple[int, str]] = []
            for object_match in re.finditer(r"\{([^{}]*)\}", block, re.DOTALL):
                item = object_match.group(1)
                url_match = re.search(r'"url"\s*:\s*"((?:[^"\\]|\\.)*)"', item)
                if not url_match:
                    continue
                image_url = cls._decode_json_str(url_match.group(1))
                if os.path.splitext(urlparse(image_url).path)[1].lower() not in _IMAGE_EXTENSIONS:
                    continue
                width_match = re.search(r'"width"\s*:\s*(\d+)', item)
                height_match = re.search(r'"height"\s*:\s*(\d+)', item)
                width = int(width_match.group(1)) if width_match else 0
                height = int(height_match.group(1)) if height_match else 0
                candidates.append((width * height, image_url))
            if candidates:
                _area, image_url = max(candidates, key=lambda item: item[0])
                cls._append_unique(image_urls, image_url)

        # Older payloads have no image_versions2. Only then fall back to the
        # legacy sources below; mixing them into a modern result can append the
        # square og:image cover as an extra carousel slide.
        if not image_urls:
            # 1) Legacy display_url из встроенного JSON. Это оригинал без кропа и
            # в правильном порядке слайдов карусели; все остальные источники ниже —
            # фолбэки, и часто отдают квадратно обрезанный превью.
            for m in re.finditer(r'"display_url"\s*:\s*"((?:[^"\\]|\\.)*)"', html):
                cls._append_unique(image_urls, cls._decode_json_str(m.group(1)))

            # 2) og:image. На основной странице поста = display_url, но на
            # /embed/ endpoint'ах Instagram подменяет на кроп 1080x1080
            # (параметр stp=dst-jpg_e35_sNxN), поэтому ставим после display_url.
            for raw in cls._find_meta_contents(html, "og:image"):
                cls._append_unique(image_urls, raw)
            for raw in cls._find_meta_contents(html, "og:image:url"):
                cls._append_unique(image_urls, raw)
            for raw in cls._find_meta_contents(html, "og:image:secure_url"):
                cls._append_unique(image_urls, raw)

            # 3) <img class="EmbeddedMediaImage" src="..."> на embed-странице.
            for m in re.finditer(
                r'<img[^>]+class=["\'][^"\']*EmbeddedMediaImage[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
                html,
                re.IGNORECASE,
            ):
                cls._append_unique(image_urls, html_lib.unescape(m.group(1)))
            for m in re.finditer(
                r'<img[^>]+src=["\']([^"\']+)["\'][^>]+class=["\'][^"\']*EmbeddedMediaImage[^"\']*["\']',
                html,
                re.IGNORECASE,
            ):
                cls._append_unique(image_urls, html_lib.unescape(m.group(1)))

        video_contents = (
            list(cls._find_meta_contents(html, "og:video"))
            or list(cls._find_meta_contents(html, "og:video:url"))
            or list(cls._find_meta_contents(html, "og:video:secure_url"))
        )
        video_url = video_contents[0] if video_contents else None

        # Reels/video posts often have no legacy og:video. Current Instagram
        # product JSON uses media_type=2, video_versions/video_dash_manifest;
        # older payloads use GraphVideo/video_url/is_video=true.
        if not video_url:
            video_match = re.search(r'"video_url"\s*:\s*"((?:[^"\\]|\\.)*)"', json_probe)
            if video_match:
                video_url = cls._decode_json_str(video_match.group(1))
        video_patterns = (
            r'"is_video"\s*:\s*true\b',
            r'"__typename"\s*:\s*"GraphVideo"',
            r'"media_type"\s*:\s*2\b',
            r'"video_versions"\s*:\s*\[\s*\{',
            r'"video_dash_manifest"\s*:\s*"(?!")',
        )
        og_types = list(cls._find_meta_contents(html, "og:type"))
        has_video_marker = bool(video_url) or any(
            re.search(pattern, json_probe, re.IGNORECASE) for pattern in video_patterns
        )
        has_video_marker = has_video_marker or any(
            value.lower().startswith("video") for value in og_types
        )

        photo_patterns = (
            r'"is_video"\s*:\s*false\b',
            r'"__typename"\s*:\s*"GraphImage"',
            r'"media_type"\s*:\s*1\b',
        )
        has_photo_marker = any(
            re.search(pattern, json_probe, re.IGNORECASE) for pattern in photo_patterns
        )
        media_kind = "video" if has_video_marker else "photo" if has_photo_marker else "unknown"

        title_contents = list(cls._find_meta_contents(html, "og:title"))
        title = title_contents[0] if title_contents else None

        return {
            "image_urls": image_urls,
            "video_url": video_url,
            "has_video_marker": has_video_marker,
            "media_kind": media_kind,
            "title": title,
        }

    @staticmethod
    def _find_meta_contents(html: str, property_name: str):
        pattern1 = (
            rf'<meta[^>]*?property=["\']{re.escape(property_name)}["\']'
            r'[^>]*?content=["\']([^"\']*)["\']'
        )
        pattern2 = (
            r'<meta[^>]*?content=["\']([^"\']*)["\']'
            rf'[^>]*?property=["\']{re.escape(property_name)}["\']'
        )
        for m in re.finditer(pattern1, html, re.IGNORECASE):
            yield html_lib.unescape(m.group(1))
        for m in re.finditer(pattern2, html, re.IGNORECASE):
            yield html_lib.unescape(m.group(1))

    @staticmethod
    def _decode_json_str(raw: str) -> str:
        """Декодирует строковое значение из JSON (экранированное \\/ и \\uXXXX)."""
        try:
            return json.loads(f'"{raw}"')
        except (ValueError, json.JSONDecodeError):
            return raw.replace("\\/", "/")

    @staticmethod
    def _is_resized_variant(url: str) -> bool:
        """
        Instagram кодирует обрезанные/уменьшенные варианты изображения в
        параметре stp=...sNxN (либо cNxN) query-string. Оригинальный
        display_url такой разметки не содержит. Используется для сортировки
        вариантов одного и того же ассета: оригиналы вперёд, ресайзы сзади.
        """
        return bool(re.search(r"[?&]stp=[^&]*\d+x\d+", url))

    @staticmethod
    def _append_unique(target: list, value: Optional[str]) -> None:
        if not value:
            return
        value = value.strip()
        if value and value not in target:
            target.append(value)

    def _try_instagram_photo(self, url: str) -> Optional[DownloadResult]:
        """
        Если Instagram-пост без видео (фото или карусель фото), скачивает до
        20 фото и возвращает DownloadResult(is_photo=True, photo_paths=[...]).
        Возвращает None, если фото не обнаружено — тогда вызывающий код падает в
        обычный video-flow через yt-dlp.
        """
        meta = self._fetch_instagram_media_info(url)
        if not meta or meta.get("has_video") or not meta.get("image_urls"):
            return None

        # Reject login/consent/error pages: branding assets come from
        # static.cdninstagram.com; actual post media comes from scontent* subdomains.
        # We require the scontent* prefix AND a trusted CDN suffix — the bare
        # "scontent" substring check would accept arbitrary hosts like
        # scontent.evil.com as long as they appeared in the parsed HTML.
        cdn_images = []
        for u in meta["image_urls"]:
            host = (urlparse(u).hostname or "").lower()
            if host.startswith("scontent") and _is_instagram_cdn_host(host):
                cdn_images.append(u)
        if not cdn_images:
            return None

        # Для одной и той же фотографии Instagram может отдать несколько
        # URL — полноразмерный display_url и cropped превью из og:image с
        # одинаковым filename, но разными query-параметрами (stp=...&oh=...).
        # Группируем по hostname+path, затем внутри группы поднимаем наверх
        # варианты без маркера ресайза: display_url обычно приходит раньше
        # og:image, но при смешанных ответах endpoint'ов cropped вариант
        # может оказаться первым — сортировка гарантирует full-size приоритет.
        # Остальные варианты держим как fallback, если подпись oh/oe протухла.
        groups: list[list[str]] = []
        if meta.get("image_urls_are_ordered_slides"):
            # Product metadata already contributes exactly one largest image
            # per child. Preserve repeated slides even when their CDN path is
            # identical; grouping them as signed variants would flatten them.
            groups = [[image_url] for image_url in cdn_images]
        else:
            groups_by_key: dict[str, list[str]] = {}
            for image_url in cdn_images:
                key = self._instagram_image_key(image_url)
                variants = groups_by_key.get(key)
                if variants is None:
                    variants = [image_url]
                    groups_by_key[key] = variants
                    groups.append(variants)
                else:
                    variants.append(image_url)

        batch_id = str(uuid.uuid4())[:8]
        downloaded: list[str] = []
        slide_urls: list[str] = []
        for idx, variants in enumerate(groups):
            variants.sort(key=self._is_resized_variant)
            output_base = str(self.download_dir / f"{batch_id}_{idx}")
            downloaded_path: Optional[str] = None
            for variant_url in variants:
                path = self._download_image_sync(variant_url, output_base)
                if path:
                    downloaded_path = path
                    downloaded.append(downloaded_path)
                    # Запоминаем URL варианта, который реально скачался: его же
                    # отдадим Telegram в rich-карусели — раз скачали мы, скорее
                    # всего скачает и он (тот же подписанный CDN-URL).
                    slide_urls.append(variant_url)
                    break

            if downloaded_path is None:
                # Returning a silently truncated carousel is worse than a clean
                # failure: let yt-dlp/other fallbacks try to recover all slides.
                self._delete_entry_files({"photo_paths": downloaded})
                return None

        if not downloaded:
            return None

        title = meta.get("title") or "Photo"
        # Карусель (≥2 слайдов) можно отправить нативным <tg-slideshow>;
        # одиночное фото отправляется обычным sendPhoto, slideshow не нужен.
        carousel_slides = (
            [
                CarouselSlide(url=u, is_video=False, local_path=path)
                for u, path in zip(slide_urls, downloaded, strict=True)
            ]
            if len(slide_urls) >= 2
            else None
        )
        return DownloadResult(
            success=True,
            file_path=downloaded[0],
            title=title,
            is_photo=True,
            photo_paths=downloaded,
            media_type_confirmed=meta.get("media_kind") == "photo",
            carousel_slides=carousel_slides,
        )
