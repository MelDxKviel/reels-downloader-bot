"""Shared test helpers: mock factories for aiogram objects."""

from unittest.mock import AsyncMock, MagicMock


def make_message(text: str = "", user_id: int = 100, chat_id: int = 100):
    msg = MagicMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.from_user.full_name = "Test User"
    msg.from_user.username = "tester"
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.answer = AsyncMock()
    msg.answer_video = AsyncMock()
    msg.answer_photo = AsyncMock()
    msg.answer_media_group = AsyncMock()
    msg.answer_animation = AsyncMock()
    msg.answer_audio = AsyncMock()
    msg.answer_voice = AsyncMock()
    msg.answer_video_note = AsyncMock()
    msg.answer_document = AsyncMock()
    msg.delete = AsyncMock()
    msg.edit_text = AsyncMock()
    msg.message_id = 42
    msg.video = None
    msg.audio = None
    msg.document = None
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock()
    msg.bot.download_file = AsyncMock()
    msg.bot.delete_message = AsyncMock()
    return msg


def make_status_message():
    sm = MagicMock()
    sm.edit_text = AsyncMock()
    sm.delete = AsyncMock()
    sm.message_id = 99
    return sm


def make_callback(data: str = "", user_id: int = 100):
    cb = MagicMock()
    cb.data = data
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.from_user.language_code = "en"
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    return cb


def make_state(data=None):
    state = MagicMock()
    data = data or {}
    state.get_data = AsyncMock(return_value=data)
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.clear = AsyncMock()
    return state


def make_bot():
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()
    bot.get_chat = AsyncMock()
    bot.send_video = AsyncMock()
    bot.send_photo = AsyncMock()
    bot.send_audio = AsyncMock()
    bot.delete_message = AsyncMock()
    bot.edit_message_text = AsyncMock()
    bot.edit_message_media = AsyncMock()
    return bot


def make_db():
    db = MagicMock()
    db.add_user = AsyncMock(return_value=True)
    db.remove_user = AsyncMock(return_value=True)
    db.get_user = AsyncMock(return_value=None)
    db.get_all_users = AsyncMock(return_value=[])
    db.get_global_stats = AsyncMock(
        return_value={
            "total_downloads": 0,
            "successful_downloads": 0,
            "failed_downloads": 0,
            "active_users": 0,
            "by_platform": {},
        }
    )
    db.get_user_stats = AsyncMock(
        return_value={
            "total_downloads": 0,
            "successful_downloads": 0,
            "failed_downloads": 0,
            "by_platform": {},
            "last_activity": None,
        }
    )
    db.record_download = AsyncMock()
    db.get_user_language = AsyncMock(return_value=None)
    db.set_user_language = AsyncMock(return_value=True)
    db.get_setting = AsyncMock(return_value=None)
    db.set_setting = AsyncMock()
    db.is_feature_enabled = AsyncMock(return_value=False)
    db.set_feature_enabled = AsyncMock()
    db.get_cache_autoclean = AsyncMock(return_value=False)
    db.set_cache_autoclean = AsyncMock()
    db.get_cache_max_age_hours = AsyncMock(return_value=168)
    db.set_cache_max_age_hours = AsyncMock()
    return db
