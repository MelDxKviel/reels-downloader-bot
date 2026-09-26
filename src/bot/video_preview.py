"""Local preview attachments shared by every video upload path."""

from aiogram.types import FSInputFile

from src.services.media import CarouselSlide, DownloadResult


def video_preview_inputs(media: DownloadResult | CarouselSlide) -> dict:
    return {
        field: FSInputFile(path)
        for field, path in (("thumbnail", media.thumbnail_path), ("cover", media.cover_path))
        if path
    }
