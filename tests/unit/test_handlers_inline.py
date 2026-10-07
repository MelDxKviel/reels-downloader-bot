"""Tests for inline-mode handler."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramServerError

from src.bot.handlers import inline as inline_h
from src.services.downloader import CarouselSlide, DownloadResult
from src.services.i18n import Translator

from ._helpers import make_bot, make_callback, make_db


def make_inline_query(text: str = "", user_id: int = 100):
    q = MagicMock()
    q.query = text
    q.from_user = MagicMock()
    q.from_user.id = user_id
    q.answer = AsyncMock()
    return q


def make_chosen_result(
    result_id: str = "", query: str = "", user_id: int = 100, inline_message_id="im1"
):
    c = MagicMock()
    c.result_id = result_id
    c.query = query
    c.from_user = MagicMock()
    c.from_user.id = user_id
    c.inline_message_id = inline_message_id
    return c


# ── inline_query_handler ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inline_query_empty_text_returns_hint():
    q = make_inline_query("")
    await inline_h.inline_query_handler(q, make_db(), Translator("en"))
    q.answer.assert_awaited()


@pytest.mark.asyncio
async def test_inline_query_answer_retries_telegram_504():
    q = make_inline_query("")
    q.answer.side_effect = [
        TelegramServerError(method=MagicMock(), message="Gateway Timeout (504)"),
        None,
    ]
    with patch("src.bot.telegram_retry.asyncio.sleep", AsyncMock()) as sleep:
        await inline_h.inline_query_handler(q, make_db(), Translator("en"))

    assert q.answer.await_count == 2
    sleep.assert_awaited_once_with(1.0)


@pytest.mark.asyncio
async def test_inline_query_invalid_url():
    q = make_inline_query("not a link")
    await inline_h.inline_query_handler(q, make_db(), Translator("en"))
    q.answer.assert_awaited()


@pytest.mark.asyncio
async def test_inline_query_url_no_cache():
    q = make_inline_query("https://youtube.com/watch?v=abc")
    with patch.object(inline_h.downloader, "get_cached_media_type", return_value=None):
        with patch.object(inline_h.downloader, "get_telegram_file_id", return_value=None):
            with patch.object(inline_h.downloader, "get_telegram_mp3_file_id", return_value=None):
                await inline_h.inline_query_handler(q, make_db(), Translator("en"))
    q.answer.assert_awaited()


@pytest.mark.asyncio
async def test_inline_query_url_cached_video():
    q = make_inline_query("https://youtube.com/watch?v=abc")
    with patch.object(inline_h.downloader, "get_cached_media_type", return_value="video"):
        with patch.object(inline_h.downloader, "get_telegram_file_id", return_value="vid_fid"):
            with patch.object(inline_h.downloader, "get_telegram_mp3_file_id", return_value=None):
                await inline_h.inline_query_handler(q, make_db(), Translator("en"))
    q.answer.assert_awaited()


@pytest.mark.asyncio
async def test_inline_query_url_cached_photo():
    q = make_inline_query("https://www.instagram.com/p/ABC/")
    with patch.object(inline_h.downloader, "get_cached_media_type", return_value="photo"):
        with patch.object(inline_h.downloader, "get_telegram_photo_file_id", return_value="ph"):
            with patch.object(inline_h.downloader, "get_telegram_mp3_file_id", return_value=None):
                await inline_h.inline_query_handler(q, make_db(), Translator("en"))
    q.answer.assert_awaited()


@pytest.mark.asyncio
async def test_inline_query_cached_carousel_is_not_flattened_to_cached_photo():
    q = make_inline_query("https://www.instagram.com/p/ABC/")
    slides = [CarouselSlide("https://cdn/1.jpg"), CarouselSlide("https://cdn/2.jpg")]
    with (
        patch.object(inline_h.downloader, "get_cached_carousel_slides", return_value=slides),
        patch.object(inline_h.downloader, "get_cached_media_type", return_value="photo"),
        patch.object(inline_h.downloader, "get_telegram_photo_file_id", return_value="first"),
        patch.object(inline_h.downloader, "get_telegram_mp3_file_id", return_value=None),
    ):
        await inline_h.inline_query_handler(q, make_db(), Translator("en"))

    first_result = q.answer.await_args.kwargs["results"][0]
    assert first_result.id.startswith("download:")


@pytest.mark.asyncio
async def test_inline_query_url_cached_mp3():
    q = make_inline_query("https://youtube.com/watch?v=abc")
    with patch.object(inline_h.downloader, "get_cached_media_type", return_value=None):
        with patch.object(inline_h.downloader, "get_telegram_file_id", return_value=None):
            with patch.object(inline_h.downloader, "get_telegram_mp3_file_id", return_value="m"):
                await inline_h.inline_query_handler(q, make_db(), Translator("en"))


# ── shorts search (feature-flagged) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_inline_query_text_shorts_disabled_returns_invalid():
    q = make_inline_query("funny cats")
    db = make_db()
    db.is_feature_enabled = AsyncMock(return_value=False)
    await inline_h.inline_query_handler(q, db, Translator("en"))
    q.answer.assert_awaited()
    # When disabled the existing "invalid_title" article is used (id="invalid").
    call_kwargs = q.answer.await_args.kwargs
    assert call_kwargs["results"][0].id == "invalid"


@pytest.mark.asyncio
async def test_inline_query_text_shorts_enabled_no_results():
    q = make_inline_query("funny cats")
    db = make_db()
    db.is_feature_enabled = AsyncMock(return_value=True)
    from src.bot.handlers import inline as ih

    with patch.object(ih, "search_shorts", AsyncMock(return_value=[])):
        await ih.inline_query_handler(q, db, Translator("en"))
    q.answer.assert_awaited()
    assert q.answer.await_args.kwargs["results"][0].id == "shorts_empty"


@pytest.mark.asyncio
async def test_inline_query_text_shorts_enabled_returns_results():
    from src.bot.handlers import inline as ih
    from src.services.youtube_search import ShortsSearchResult

    q = make_inline_query("funny cats")
    db = make_db()
    db.is_feature_enabled = AsyncMock(return_value=True)

    hits = [
        ShortsSearchResult(
            video_id="abc123",
            title="Cat 1",
            url="https://www.youtube.com/shorts/abc123",
            thumbnail="https://i.ytimg.com/vi/abc123/hqdefault.jpg",
            duration=15.0,
            channel="Cats",
        ),
        ShortsSearchResult(
            video_id="def456",
            title="Cat 2",
            url="https://www.youtube.com/shorts/def456",
            thumbnail=None,
            duration=None,
            channel=None,
        ),
    ]
    with patch.object(ih, "search_shorts", AsyncMock(return_value=hits)):
        with patch.object(ih, "get_cached_video_file_id", return_value=None):
            await ih.inline_query_handler(q, db, Translator("en"))
    q.answer.assert_awaited()
    result_ids = [r.id for r in q.answer.await_args.kwargs["results"]]
    assert result_ids == ["s:abc123", "s:def456"]


@pytest.mark.asyncio
async def test_inline_query_text_shorts_uses_cached_file_id():
    from src.bot.handlers import inline as ih
    from src.services.youtube_search import ShortsSearchResult

    q = make_inline_query("funny cats")
    db = make_db()
    db.is_feature_enabled = AsyncMock(return_value=True)

    hits = [
        ShortsSearchResult(
            video_id="abc123",
            title="Cat 1",
            url="https://www.youtube.com/shorts/abc123",
            thumbnail="https://i.ytimg.com/vi/abc123/hqdefault.jpg",
            duration=15.0,
            channel=None,
        ),
    ]
    with patch.object(ih, "search_shorts", AsyncMock(return_value=hits)):
        with patch.object(ih, "get_cached_video_file_id", return_value="cached_fid"):
            await ih.inline_query_handler(q, db, Translator("en"))
    result = q.answer.await_args.kwargs["results"][0]
    assert result.id == "sc:abc123"


@pytest.mark.asyncio
async def test_chosen_inline_shorts_cached_records_stats():
    cr = make_chosen_result("sc:abc123", "funny cats")
    bot = make_bot()
    db = make_db()
    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    db.record_download.assert_awaited()
    call_kwargs = db.record_download.await_args.kwargs
    assert call_kwargs["url"] == "https://www.youtube.com/shorts/abc123"
    assert call_kwargs["success"] is True


@pytest.mark.asyncio
async def test_chosen_inline_shorts_downloads_video(tmp_path):
    cr = make_chosen_result("s:abc123", "funny cats")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_upload_video_and_get_file_id", AsyncMock(return_value="fid")):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    bot.edit_message_media.assert_awaited()


@pytest.mark.asyncio
async def test_inline_query_unsupported_url_keeps_invalid_card_even_when_shorts_on():
    """URL on unsupported host (e.g. vimeo) must not be turned into a search query."""
    q = make_inline_query("https://vimeo.com/12345")
    db = make_db()
    db.is_feature_enabled = AsyncMock(return_value=True)
    from src.bot.handlers import inline as ih

    with patch.object(ih, "search_shorts", AsyncMock()) as search:
        await ih.inline_query_handler(q, db, Translator("en"))
    q.answer.assert_awaited()
    assert q.answer.await_args.kwargs["results"][0].id == "invalid"
    search.assert_not_called()
    # Flag must not even be queried for URL-looking input.
    db.is_feature_enabled.assert_not_called()


@pytest.mark.asyncio
async def test_inline_query_db_flag_lookup_exception_falls_back_to_invalid():
    q = make_inline_query("funny cats")
    db = make_db()
    db.is_feature_enabled = AsyncMock(side_effect=RuntimeError("db down"))
    await inline_h.inline_query_handler(q, db, Translator("en"))
    q.answer.assert_awaited()
    assert q.answer.await_args.kwargs["results"][0].id == "invalid"


@pytest.mark.asyncio
async def test_answer_shorts_search_swallows_search_exception():
    from src.bot.handlers import inline as ih

    q = make_inline_query("funny cats")
    with patch.object(ih, "search_shorts", AsyncMock(side_effect=RuntimeError("boom"))):
        await ih._answer_shorts_search(q, "funny cats", Translator("en"))
    q.answer.assert_awaited()
    assert q.answer.await_args.kwargs["results"][0].id == "shorts_empty"


@pytest.mark.asyncio
async def test_chosen_inline_shorts_empty_video_id_edits_error():
    cr = make_chosen_result("s:", "funny cats")
    bot = make_bot()
    db = make_db()
    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    bot.edit_message_text.assert_awaited()


# ── inline_loading_callback ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inline_loading_callback_answers():
    cb = make_callback("inline_loading")
    await inline_h.inline_loading_callback(cb, Translator("en"))
    cb.answer.assert_awaited()


# ── chosen_inline_handler ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chosen_inline_cached_video_only_stats():
    cr = make_chosen_result("cached:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    db.record_download.assert_awaited()


@pytest.mark.asyncio
async def test_chosen_inline_cached_no_url_in_query():
    cr = make_chosen_result("cached:abc", "no url here")
    bot = make_bot()
    db = make_db()
    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    db.record_download.assert_not_called()


@pytest.mark.asyncio
async def test_chosen_inline_unknown_prefix_returns():
    cr = make_chosen_result("weird:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    db.record_download.assert_not_called()


@pytest.mark.asyncio
async def test_chosen_inline_download_no_inline_message_id():
    cr = make_chosen_result("download:abc", "https://youtube.com/watch?v=x", inline_message_id=None)
    bot = make_bot()
    db = make_db()
    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_download_no_url_in_query():
    cr = make_chosen_result("download:abc", "not a url")
    bot = make_bot()
    db = make_db()
    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    bot.edit_message_text.assert_awaited()


@pytest.mark.asyncio
async def test_chosen_inline_download_download_exception():
    cr = make_chosen_result("download:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    with patch.object(inline_h.downloader, "download", AsyncMock(side_effect=RuntimeError("oops"))):
        await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    db.record_download.assert_awaited()


@pytest.mark.asyncio
async def test_chosen_inline_download_failed_result():
    cr = make_chosen_result("download:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    result = DownloadResult(success=False, error="SECRET_YTDLP_LOG HTTP 504 <raw>")
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    db.record_download.assert_awaited()
    text = bot.edit_message_text.await_args.kwargs["text"]
    assert text == "❌ <b>Failed to load</b>"
    assert "SECRET_YTDLP_LOG" not in text
    assert "504" not in text


@pytest.mark.asyncio
async def test_chosen_inline_download_video_success(tmp_path):
    cr = make_chosen_result("download:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(
        success=True, file_path=str(video), duration=9.0, width=720, height=1280
    )
    upload = AsyncMock(return_value="fid")
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_upload_video_and_get_file_id", upload):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    bot.edit_message_media.assert_awaited()
    upload.assert_awaited_once_with(bot, str(video), width=720, height=1280, duration=9.0)
    media = bot.edit_message_media.await_args.kwargs["media"]
    assert (media.width, media.height, media.duration) == (720, 1280, 9)
    assert video.exists()  # the shared media cache owns successful inline downloads


@pytest.mark.asyncio
async def test_chosen_inline_video_edit_retries_telegram_504(tmp_path):
    cr = make_chosen_result("download:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    bot.edit_message_media.side_effect = [
        TelegramServerError(method=MagicMock(), message="Gateway Timeout (504)"),
        None,
    ]
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video), width=720, height=1280)
    with (
        patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)),
        patch.object(inline_h, "_upload_video_and_get_file_id", AsyncMock(return_value="fid")),
        patch("src.bot.telegram_retry.asyncio.sleep", AsyncMock()) as sleep,
    ):
        await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))

    assert bot.edit_message_media.await_count == 2
    sleep.assert_awaited_once_with(1.0)
    assert db.record_download.await_args.kwargs["success"] is True


@pytest.mark.asyncio
async def test_chosen_inline_download_video_publish_failure(tmp_path):
    cr = make_chosen_result("download:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_upload_video_and_get_file_id", AsyncMock(return_value=None)):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_download_video_edit_failure(tmp_path):
    cr = make_chosen_result("download:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    bot.edit_message_media.side_effect = RuntimeError("edit failed")
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_upload_video_and_get_file_id", AsyncMock(return_value="fid")):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_download_photo_success(tmp_path):
    cr = make_chosen_result("download:abc", "https://www.instagram.com/p/ABC/")
    bot = make_bot()
    db = make_db()
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    result = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)]
    )
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_upload_photo_and_get_file_id", AsyncMock(return_value="pf")):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    bot.edit_message_media.assert_awaited()


@pytest.mark.asyncio
async def test_chosen_inline_photo_carousel_edits_rich_message_with_file_ids(tmp_path):
    url = "https://www.instagram.com/p/CAROUSEL/"
    cr = make_chosen_result("download:abc", url)
    bot = make_bot()
    bot.edit_message_text.side_effect = [
        TelegramServerError(method=MagicMock(), message="Gateway Timeout (504)"),
        None,
    ]
    db = make_db()
    paths = [tmp_path / "1.jpg", tmp_path / "2.jpg"]
    for path in paths:
        path.write_bytes(b"x")
    result = DownloadResult(
        success=True,
        file_path=str(paths[0]),
        title="Two photos",
        is_photo=True,
        photo_paths=[str(path) for path in paths],
        carousel_slides=[
            CarouselSlide("https://cdn/1.jpg"),
            CarouselSlide("https://cdn/2.jpg"),
        ],
    )
    upload_carousel = AsyncMock(return_value=["photo_file_1", "photo_file_2"])
    download = AsyncMock(return_value=result)
    with (
        patch.object(inline_h.downloader, "get_from_cache", return_value=None),
        patch.object(inline_h.downloader, "download", download),
        patch.object(inline_h, "_upload_carousel_photos_and_get_file_ids", upload_carousel),
        patch("src.bot.telegram_retry.asyncio.sleep", AsyncMock()) as sleep,
    ):
        await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))

    download.assert_awaited_once_with(
        url, allow_carousel=True, reserve=True, user_id=100, quality="standard"
    )
    upload_carousel.assert_awaited_once_with(bot, [str(path) for path in paths])
    assert bot.edit_message_text.await_count == 2
    sleep.assert_awaited_once_with(1.0)
    rich_message = bot.edit_message_text.await_args.kwargs["rich_message"]
    assert rich_message.media is not None
    assert [item.media.media for item in rich_message.media] == [
        "photo_file_1",
        "photo_file_2",
    ]
    bot.edit_message_media.assert_not_awaited()
    assert db.record_download.await_args.kwargs["success"] is True
    assert all(path.exists() for path in paths)  # cache owns the media after inline upload


@pytest.mark.asyncio
async def test_chosen_inline_photo_carousel_rich_failure_falls_back_to_first_photo(tmp_path):
    url = "https://www.instagram.com/p/CAROUSEL/"
    cr = make_chosen_result("download:abc", url)
    bot = make_bot()
    bot.edit_message_text.side_effect = RuntimeError("rich edit rejected")
    db = make_db()
    paths = [tmp_path / "1.jpg", tmp_path / "2.jpg"]
    for path in paths:
        path.write_bytes(b"x")
    result = DownloadResult(
        success=True,
        file_path=str(paths[0]),
        is_photo=True,
        photo_paths=[str(path) for path in paths],
        carousel_slides=[
            CarouselSlide("https://cdn/1.jpg"),
            CarouselSlide("https://cdn/2.jpg"),
        ],
    )
    with (
        patch.object(inline_h.downloader, "get_from_cache", return_value=None),
        patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)),
        patch.object(
            inline_h,
            "_upload_carousel_photos_and_get_file_ids",
            AsyncMock(return_value=["photo_file_1", "photo_file_2"]),
        ),
        patch.object(inline_h, "_upload_photo_and_get_file_id", AsyncMock(return_value="first")),
        patch.object(inline_h.downloader, "set_telegram_photo_file_id") as cache_first,
    ):
        await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))

    bot.edit_message_text.assert_awaited_once()  # file_id-rich only; URLs are forbidden inline
    bot.edit_message_media.assert_awaited_once()
    cache_first.assert_not_called()
    assert db.record_download.await_args.kwargs["success"] is True
    assert all(path.exists() for path in paths)  # cache owns the media after inline upload


@pytest.mark.asyncio
async def test_chosen_inline_download_photo_publish_failure(tmp_path):
    cr = make_chosen_result("download:abc", "https://www.instagram.com/p/ABC/")
    bot = make_bot()
    db = make_db()
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    result = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)]
    )
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_upload_photo_and_get_file_id", AsyncMock(return_value=None)):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_download_photo_edit_failure(tmp_path):
    cr = make_chosen_result("download:abc", "https://www.instagram.com/p/ABC/")
    bot = make_bot()
    bot.edit_message_media.side_effect = RuntimeError("fail")
    db = make_db()
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    result = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)]
    )
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_upload_photo_and_get_file_id", AsyncMock(return_value="pf")):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_mp3_success(tmp_path):
    cr = make_chosen_result("mp3:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    mp3 = tmp_path / "out.mp3"
    mp3.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video), title="Title")
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_convert_to_mp3", AsyncMock(return_value=str(mp3))):
            with patch.object(
                inline_h, "_upload_audio_and_get_file_id", AsyncMock(return_value="afid")
            ):
                await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    bot.edit_message_media.assert_awaited()


@pytest.mark.asyncio
async def test_chosen_inline_mp3_convert_exception(tmp_path):
    cr = make_chosen_result("mp3:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(
            inline_h, "_convert_to_mp3", AsyncMock(side_effect=RuntimeError("conv fail"))
        ):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_mp3_convert_returns_none(tmp_path):
    cr = make_chosen_result("mp3:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_convert_to_mp3", AsyncMock(return_value=None)):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_mp3_upload_failure(tmp_path):
    cr = make_chosen_result("mp3:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    mp3 = tmp_path / "out.mp3"
    mp3.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_convert_to_mp3", AsyncMock(return_value=str(mp3))):
            with patch.object(
                inline_h, "_upload_audio_and_get_file_id", AsyncMock(return_value=None)
            ):
                await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_mp3_edit_failure(tmp_path):
    cr = make_chosen_result("mp3:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    bot.edit_message_media.side_effect = RuntimeError("edit fail")
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    mp3 = tmp_path / "out.mp3"
    mp3.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_convert_to_mp3", AsyncMock(return_value=str(mp3))):
            with patch.object(
                inline_h, "_upload_audio_and_get_file_id", AsyncMock(return_value="afid")
            ):
                await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


# ── _safe_edit_text ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safe_edit_text_swallows_exception():
    bot = make_bot()
    bot.edit_message_text.side_effect = RuntimeError("nope")
    await inline_h._safe_edit_text(bot, "im1", "hello")


# ── _resolve_storage_chat_id ─────────────────────────────────────────────────


def test_resolve_storage_chat_id_video_storage_set():
    with patch("src.bot.handlers.inline.VIDEO_STORAGE_CHAT_ID", -100):
        assert inline_h._resolve_storage_chat_id() == -100


def test_resolve_storage_chat_id_admin_fallback():
    with patch("src.bot.handlers.inline.VIDEO_STORAGE_CHAT_ID", None):
        with patch("src.bot.handlers.inline.ADMIN_USERS", [42]):
            assert inline_h._resolve_storage_chat_id() == 42


def test_resolve_storage_chat_id_none():
    with patch("src.bot.handlers.inline.VIDEO_STORAGE_CHAT_ID", None):
        with patch("src.bot.handlers.inline.ADMIN_USERS", []):
            assert inline_h._resolve_storage_chat_id() is None


# ── _upload_*_and_get_file_id ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_video_no_storage():
    bot = make_bot()
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=None):
        result = await inline_h._upload_video_and_get_file_id(bot, "/tmp/x.mp4")
    assert result is None


@pytest.mark.asyncio
async def test_upload_video_send_fails():
    bot = make_bot()
    bot.send_video.side_effect = RuntimeError("fail")
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_video_and_get_file_id(bot, "/tmp/x.mp4")
    assert result is None


@pytest.mark.asyncio
async def test_upload_video_retries_telegram_504_then_succeeds():
    bot = make_bot()
    staging = MagicMock()
    staging.video = MagicMock()
    staging.video.file_id = "fid"
    staging.message_id = 1
    error = TelegramServerError(method=MagicMock(), message="Gateway Timeout (504)")
    bot.send_video.side_effect = [error, staging]

    with (
        patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42),
        patch("src.bot.telegram_retry.asyncio.sleep", AsyncMock()) as sleep,
    ):
        result = await inline_h._upload_video_and_get_file_id(bot, "/tmp/x.mp4")

    assert result == "fid"
    assert bot.send_video.await_count == 2
    sleep.assert_awaited_once_with(1.0)
    bot.delete_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_video_stops_after_three_telegram_504_responses():
    bot = make_bot()
    bot.send_video.side_effect = [
        TelegramServerError(method=MagicMock(), message="Gateway Timeout (504)") for _ in range(3)
    ]

    with (
        patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42),
        patch("src.bot.telegram_retry.asyncio.sleep", AsyncMock()) as sleep,
    ):
        result = await inline_h._upload_video_and_get_file_id(bot, "/tmp/x.mp4")

    assert result is None
    assert bot.send_video.await_count == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_upload_video_success():
    bot = make_bot()
    staging = MagicMock()
    staging.video = MagicMock()
    staging.video.file_id = "fid"
    staging.message_id = 1
    bot.send_video.return_value = staging
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_video_and_get_file_id(
            bot, "/tmp/x.mp4", width=720, height=1280, duration=4.2
        )
    assert result == "fid"
    kwargs = bot.send_video.await_args.kwargs
    assert (kwargs["width"], kwargs["height"], kwargs["duration"]) == (720, 1280, 4)
    assert kwargs["request_timeout"] == 180


@pytest.mark.asyncio
async def test_upload_video_no_video_in_staging():
    bot = make_bot()
    staging = MagicMock()
    staging.video = None
    staging.message_id = 1
    bot.send_video.return_value = staging
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_video_and_get_file_id(bot, "/tmp/x.mp4")
    assert result is None


@pytest.mark.asyncio
async def test_upload_video_delete_message_fails():
    bot = make_bot()
    staging = MagicMock()
    staging.video = MagicMock()
    staging.video.file_id = "fid"
    staging.message_id = 1
    bot.send_video.return_value = staging
    bot.delete_message.side_effect = RuntimeError("can't delete")
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_video_and_get_file_id(bot, "/tmp/x.mp4")
    assert result == "fid"


@pytest.mark.asyncio
async def test_upload_photo_no_storage():
    bot = make_bot()
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=None):
        assert await inline_h._upload_photo_and_get_file_id(bot, "/tmp/x.jpg") is None


@pytest.mark.asyncio
async def test_upload_photo_send_fails():
    bot = make_bot()
    bot.send_photo.side_effect = RuntimeError("fail")
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        assert await inline_h._upload_photo_and_get_file_id(bot, "/tmp/x.jpg") is None


@pytest.mark.asyncio
async def test_upload_photo_success():
    bot = make_bot()
    staging = MagicMock()
    size = MagicMock()
    size.file_id = "pf"
    staging.photo = [size]
    staging.message_id = 1
    bot.send_photo.return_value = staging
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_photo_and_get_file_id(bot, "/tmp/x.jpg")
    assert result == "pf"


@pytest.mark.asyncio
async def test_upload_photo_no_photo_in_staging():
    bot = make_bot()
    staging = MagicMock()
    staging.photo = None
    staging.message_id = 1
    bot.send_photo.return_value = staging
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_photo_and_get_file_id(bot, "/tmp/x.jpg")
    assert result is None


@pytest.mark.asyncio
async def test_upload_photo_delete_fails():
    bot = make_bot()
    staging = MagicMock()
    size = MagicMock()
    size.file_id = "pf"
    staging.photo = [size]
    staging.message_id = 1
    bot.send_photo.return_value = staging
    bot.delete_message.side_effect = RuntimeError("nope")
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_photo_and_get_file_id(bot, "/tmp/x.jpg")
    assert result == "pf"


@pytest.mark.asyncio
async def test_upload_audio_no_storage():
    bot = make_bot()
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=None):
        assert await inline_h._upload_audio_and_get_file_id(bot, "/tmp/x.mp3") is None


@pytest.mark.asyncio
async def test_upload_audio_send_fails():
    bot = make_bot()
    bot.send_audio.side_effect = RuntimeError("fail")
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        assert await inline_h._upload_audio_and_get_file_id(bot, "/tmp/x.mp3") is None


@pytest.mark.asyncio
async def test_upload_audio_success():
    bot = make_bot()
    staging = MagicMock()
    staging.audio = MagicMock()
    staging.audio.file_id = "afid"
    staging.message_id = 1
    bot.send_audio.return_value = staging
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_audio_and_get_file_id(bot, "/tmp/x.mp3")
    assert result == "afid"


@pytest.mark.asyncio
async def test_upload_audio_no_audio_in_staging():
    bot = make_bot()
    staging = MagicMock()
    staging.audio = None
    staging.message_id = 1
    bot.send_audio.return_value = staging
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_audio_and_get_file_id(bot, "/tmp/x.mp3")
    assert result is None


@pytest.mark.asyncio
async def test_upload_audio_delete_fails():
    bot = make_bot()
    staging = MagicMock()
    staging.audio = MagicMock()
    staging.audio.file_id = "afid"
    staging.message_id = 1
    bot.send_audio.return_value = staging
    bot.delete_message.side_effect = RuntimeError("nope")
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_audio_and_get_file_id(bot, "/tmp/x.mp3")
    assert result == "afid"


@pytest.mark.asyncio
async def test_chosen_inline_mp3_remove_temp_file_fails(tmp_path):
    """Cleanup of temp mp3 file may fail — exception must be swallowed."""
    cr = make_chosen_result("mp3:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    mp3 = tmp_path / "out.mp3"
    mp3.write_bytes(b"x")
    from src.services.downloader import DownloadResult

    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_convert_to_mp3", AsyncMock(return_value=str(mp3))):
            with patch.object(
                inline_h, "_upload_audio_and_get_file_id", AsyncMock(return_value="afid")
            ):
                with patch("src.bot.handlers.inline.os.remove", side_effect=OSError("nope")):
                    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    bot.edit_message_media.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kinds", [(False, True), (True, True)])
async def test_inline_mixed_and_video_carousels_upload_every_slide_in_order(tmp_path, kinds):
    bot, db = make_bot(), make_db()
    url = "https://instagram.com/p/MIXED/"
    slides = []
    for index, is_video in enumerate(kinds):
        path = tmp_path / f"{index}.{'mp4' if is_video else 'jpg'}"
        path.write_bytes(b"media")
        slides.append(
            CarouselSlide(f"https://cdn/{path.name}", is_video, str(path), 720, 1280, 4 + index)
        )
    result = DownloadResult(
        success=True,
        file_path=slides[0].local_path,
        is_photo=not kinds[0],
        carousel_slides=slides,
    )
    upload_video = AsyncMock(side_effect=lambda _bot, path, **kw: "video_" + Path(path).stem)
    upload_photo = AsyncMock(side_effect=lambda _bot, path: "photo_" + Path(path).stem)
    with (
        patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)),
        patch.object(inline_h.downloader, "release_result") as release,
        patch.object(inline_h, "_upload_video_and_get_file_id", upload_video),
        patch.object(inline_h, "_upload_photo_and_get_file_id", upload_photo),
    ):
        await inline_h.chosen_inline_handler(
            make_chosen_result("download:abc", url), bot, db, Translator("en")
        )
    rich = bot.edit_message_text.await_args.kwargs["rich_message"]
    assert [item.media.media for item in rich.media] == [
        ("video_" if is_video else "photo_") + str(index) for index, is_video in enumerate(kinds)
    ]
    assert upload_video.await_count == sum(kinds)
    assert upload_photo.await_count == len(kinds) - sum(kinds)
    for call in upload_video.await_args_list:
        assert call.kwargs["width"] == 720 and call.kwargs["height"] == 1280
    bot.edit_message_media.assert_not_awaited()
    release.assert_called_once_with(result)
    assert db.record_download.await_args.kwargs["success"] is True


@pytest.mark.asyncio
async def test_mixed_carousel_upload_failure_never_returns_partial_ids():
    slides = [
        CarouselSlide("https://cdn/a.jpg", local_path="a.jpg"),
        CarouselSlide("https://cdn/b.mp4", True, "b.mp4"),
    ]
    with (
        patch.object(inline_h, "_upload_photo_and_get_file_id", AsyncMock(return_value="photo")),
        patch.object(inline_h, "_upload_video_and_get_file_id", AsyncMock(return_value=None)),
    ):
        assert await inline_h._upload_carousel_and_get_file_ids(make_bot(), slides) is None
