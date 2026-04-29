"""Russian translations."""

MESSAGES = {
    # ── Common / shared ──────────────────────────────────────────────────
    "common.cancel_button": "❌ Отменить",
    "common.not_your_operation": "Это не ваша операция.",
    "common.processing": "⏳ Обрабатываю...",
    "common.access_denied_callback": "⛔ Нет доступа",
    "common.unknown_error": "Неизвестная ошибка",
    "common.unknown_platform": "Unknown",
    # ── /start ──────────────────────────────────────────────────────────
    "start.text": (
        "👋 Привет, <b>{name}</b>!\n\n"
        "🎬 Я помогу скачать видео с популярных платформ:\n"
        "• YouTube\n"
        "• Instagram Reels\n"
        "• TikTok\n"
        "• X (Twitter)\n\n"
        "📎 Просто отправь мне ссылку на видео, и я пришлю его тебе!\n\n"
        "⚡ <b>Inline-режим:</b> в любом чате наберите "
        "<code>@имя_бота ссылка</code> — и видео отправится прямо из этого чата.\n\n"
        "⚠️ <i>Ограничение: максимальный размер видео — 50 МБ</i>"
    ),
    # ── /help ───────────────────────────────────────────────────────────
    "help.text": (
        "📖 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Скопируй ссылку на видео\n"
        "2️⃣ Отправь её мне в чат\n"
        "3️⃣ Подожди, пока я скачаю и отправлю видео\n\n"
        "📱 <b>Поддерживаемые платформы:</b>\n"
        "• <b>YouTube</b> — youtube.com, youtu.be\n"
        "• <b>Instagram</b> — instagram.com/reel/, instagram.com/p/\n"
        "• <b>TikTok</b> — tiktok.com, vm.tiktok.com\n"
        "• <b>X/Twitter</b> — twitter.com, x.com\n\n"
        "🛠 <b>Дополнительные команды:</b>\n"
        "• /round — конвертировать видео в кружок (макс 60 сек)\n"
        "• /gif — конвертировать видео в GIF (макс 10 сек)\n"
        "• /mp3 — извлечь аудио из видео в формате MP3\n"
        "• /voice — конвертировать видео/аудио/ссылку в голосовое сообщение\n"
        "• /language — выбрать язык интерфейса\n\n"
        "⚡ <b>Inline-режим:</b>\n"
        "В любом чате введите <code>@имя_бота ссылка</code> — бот предложит карточку "
        "для отправки видео, и ролик появится прямо в чате.\n"
        "Уже скачанные видео отправляются моментально из кэша.\n\n"
        "⚠️ <b>Ограничения:</b>\n"
        "• Максимальный размер видео: 50 МБ\n"
        "• Приватные видео не поддерживаются\n\n"
        "💡 <b>Совет:</b> Если видео слишком большое, попробуй найти его в более низком качестве"
    ),
    "help.admin_suffix": (
        "\n\n🔐 <b>Вы администратор.</b> Используйте /adminhelp для списка команд управления."
    ),
    # ── /id ─────────────────────────────────────────────────────────────
    "id.text": (
        "👤 <b>Информация о вас:</b>\n\n"
        "🆔 ID: <code>{user_id}</code>\n"
        "📛 Имя: {full_name}\n"
        "🔗 Username: @{username}"
    ),
    "id.username_unset": "не указан",
    # ── /cache ──────────────────────────────────────────────────────────
    "cache.text": (
        "💾 <b>Информация о кэше:</b>\n\n"
        "📁 Видео в кэше: {count}\n"
        "📊 Размер: {size_mb:.1f} МБ\n\n"
        "<i>Используйте /clearcache для очистки</i>"
    ),
    "cache.cleared": "🗑 Кэш очищен!\nУдалено файлов: {count}",
    # ── /language ───────────────────────────────────────────────────────
    "language.choose": "🌐 <b>Выберите язык интерфейса:</b>",
    "language.changed": "✅ Язык интерфейса переключён на <b>Русский</b>.",
    "language.unsupported": "❌ Этот язык не поддерживается.",
    # ── Download (URL handler) ──────────────────────────────────────────
    "download.invalid_link_hint": (
        "🤔 Похоже, вы хотели отправить ссылку, но она некорректна.\n"
        "Пожалуйста, скопируйте полную ссылку на видео."
    ),
    "download.send_link_hint": (
        "📎 Отправьте мне ссылку на видео с YouTube, Instagram, TikTok или X/Twitter.\n"
        "Используйте /help для получения справки."
    ),
    "download.start_status": "⏳ Скачиваю с <b>{platform}</b>...\nЭто может занять некоторое время.",
    "download.failed": "❌ <b>Не удалось скачать</b>\n\nПричина: {reason}",
    "download.from_cache_status": "📤 Отправляю {media_label} из кэша...",
    "download.send_status": "📤 Отправляю {media_label}...",
    "download.generic_error": "❌ <b>Произошла ошибка</b>\n\nПопробуйте позже или используйте другую ссылку.",
    "download.send_error": "❌ <b>Ошибка при отправке</b>\n\nПопробуйте позже.",
    "download.media_label.video": "видео",
    "download.media_label.photo": "фото",
    # ── /download ───────────────────────────────────────────────────────
    "download.cmd.prompt": "🔗 Отправьте ссылку на видео с YouTube, Instagram, TikTok или X/Twitter.",
    "download.cmd.cancelled": "❌ Загрузка отменена.",
    "download.cmd.url_not_found": "🤔 Ссылка не найдена. Отправьте ссылку на видео или нажмите ❌ для отмены.",
    # ── /mp3 ────────────────────────────────────────────────────────────
    "mp3.prompt": (
        "🎵 Отправьте ссылку на видео или mp4-файл.\n\n"
        "Я извлеку аудио и пришлю его в формате <b>MP3</b>."
    ),
    "mp3.cancelled": "❌ Конвертация в MP3 отменена.",
    "mp3.url_not_found": "🤔 Ссылка не найдена. Отправьте ссылку на видео или нажмите ❌ для отмены.",
    "mp3.not_video": "⚠️ Это не видеофайл. Отправьте mp4 или ссылку на видео.",
    "mp3.download_status": "⏳ Скачиваю видео с <b>{platform}</b>...",
    "mp3.download_error": "❌ <b>Ошибка при скачивании</b>\n\nПопробуйте позже.",
    "mp3.failed": "❌ <b>Не удалось скачать видео</b>\n\nПричина: {reason}",
    "mp3.convert_status": "🔄 Конвертирую в MP3...",
    "mp3.convert_error": "❌ <b>Ошибка конвертации</b>\n\nFFmpeg не найден или произошла ошибка обработки.",
    "mp3.send_error": "❌ <b>Ошибка при отправке</b>\n\nПопробуйте позже.",
    "mp3.upload_video_status": "⏳ Загружаю видео...",
    "mp3.upload_failed": "❌ <b>Не удалось загрузить файл</b>",
    # ── /voice ──────────────────────────────────────────────────────────
    "voice.prompt": (
        "🎤 Отправьте ссылку на видео, mp4-файл или аудиофайл (MP3 и др.).\n\n"
        "Я извлеку аудио и пришлю его как <b>голосовое сообщение</b>."
    ),
    "voice.cancelled": "❌ Создание голосового сообщения отменено.",
    "voice.url_not_found": "🤔 Ссылка не найдена. Отправьте ссылку, файл или нажмите ❌ для отмены.",
    "voice.unsupported_format": "⚠️ Неподдерживаемый формат. Отправьте видео, аудиофайл или ссылку на видео.",
    "voice.download_status": "⏳ Скачиваю видео с <b>{platform}</b>...",
    "voice.download_error": "❌ <b>Ошибка при скачивании</b>\n\nПопробуйте позже.",
    "voice.failed": "❌ <b>Не удалось скачать видео</b>\n\nПричина: {reason}",
    "voice.convert_status": "🔄 Конвертирую в голосовое сообщение...",
    "voice.convert_error": "❌ <b>Ошибка конвертации</b>\n\nFFmpeg не найден или произошла ошибка обработки.",
    "voice.send_error": "❌ <b>Ошибка при отправке</b>\n\nПопробуйте позже.",
    "voice.upload_video_status": "⏳ Загружаю видео...",
    "voice.upload_audio_status": "⏳ Загружаю аудио...",
    "voice.upload_file_status": "⏳ Загружаю файл...",
    "voice.upload_failed": "❌ <b>Не удалось загрузить файл</b>",
    # ── /round ──────────────────────────────────────────────────────────
    "round.prompt": (
        "🎥 Отправьте ссылку на видео или mp4-файл.\n\n"
        "⏱ Максимальная длительность кружка: <b>60 секунд</b>"
    ),
    "round.cancelled": "❌ Создание кружка отменено.",
    "round.url_not_found": "🤔 Ссылка не найдена. Отправьте ссылку на видео или нажмите ❌ для отмены.",
    "round.not_video": "⚠️ Это не видеофайл. Отправьте mp4 или ссылку на видео.",
    "round.download_status": "⏳ Скачиваю видео с <b>{platform}</b>...",
    "round.download_error": "❌ <b>Ошибка при скачивании</b>\n\nПопробуйте позже.",
    "round.failed": "❌ <b>Не удалось скачать видео</b>\n\nПричина: {reason}",
    "round.convert_status": "🔄 Конвертирую в кружок...",
    "round.convert_error": "❌ <b>Ошибка конвертации</b>\n\nFFmpeg не найден или произошла ошибка обработки.",
    "round.send_error": "❌ <b>Ошибка при отправке</b>\n\nПопробуйте позже.",
    "round.upload_video_status": "⏳ Загружаю видео...",
    "round.upload_failed": "❌ <b>Не удалось загрузить файл</b>",
    # ── /gif ────────────────────────────────────────────────────────────
    "gif.prompt": (
        "🎞 Отправьте ссылку на видео или mp4-файл.\n\n"
        "⏱ Видео будет обрезано до <b>10 секунд</b>\n"
        "📐 Размер: 480px · 10 fps · оптимизированная палитра"
    ),
    "gif.cancelled": "❌ Создание GIF отменено.",
    "gif.url_not_found": "🤔 Ссылка не найдена. Отправьте ссылку на видео или нажмите ❌ для отмены.",
    "gif.not_video": "⚠️ Это не видеофайл. Отправьте mp4 или ссылку на видео.",
    "gif.download_status": "⏳ Скачиваю видео с <b>{platform}</b>...",
    "gif.download_error": "❌ <b>Ошибка при скачивании</b>\n\nПопробуйте позже.",
    "gif.failed": "❌ <b>Не удалось скачать видео</b>\n\nПричина: {reason}",
    "gif.convert_status": "🔄 Конвертирую в GIF...",
    "gif.convert_error": "❌ <b>Ошибка конвертации</b>\n\nFFmpeg не найден или произошла ошибка обработки.",
    "gif.send_error": "❌ <b>Ошибка при отправке</b>\n\nПопробуйте позже.",
    "gif.upload_video_status": "⏳ Загружаю видео...",
    "gif.upload_failed": "❌ <b>Не удалось загрузить файл</b>",
    # ── Inline ──────────────────────────────────────────────────────────
    "inline.loading_button": "⏳ Загружается…",
    "inline.loading_callback": "⏳ Видео загружается, подождите…",
    "inline.hint_title": "Введите ссылку на видео",
    "inline.hint_description": "YouTube · Instagram · TikTok · X/Twitter",
    "inline.hint_message": (
        "📎 Чтобы скачать видео через inline-режим, отправьте ссылку "
        "с поддерживаемой платформы (YouTube, Instagram, TikTok, X/Twitter)."
    ),
    "inline.invalid_title": "❌ Ссылка не распознана",
    "inline.invalid_description": "Поддерживаются: YouTube, Instagram, TikTok, X/Twitter",
    "inline.invalid_message": "❌ Не удалось распознать ссылку.\nПоддерживаются: YouTube, Instagram, TikTok, X/Twitter.",
    "inline.cached_photo_title": "🖼 Скачать с {platform}",
    "inline.cached_video_title": "🎬 Скачать с {platform}",
    "inline.cached_description": "Отправить моментально (из кэша)",
    "inline.download_title": "📥 Скачать с {platform}",
    "inline.download_status": "⏳ Загружаю с <b>{platform}</b>...",
    "inline.mp3_title": "🎵 MP3 с {platform}",
    "inline.mp3_description": "Извлечь аудио в формате MP3",
    "inline.mp3_status": "⏳ Извлекаю MP3 с <b>{platform}</b>...",
    "inline.error.generic": "❌ <b>Произошла ошибка</b>\n\nПопробуйте позже.",
    "inline.error.failed": "❌ <b>Не удалось скачать</b>\n\nПричина: {reason}",
    "inline.error.publish_video": (
        "❌ <b>Не удалось опубликовать</b>\n\n"
        "Inline-storage не настроен или файл не загрузился. "
        "Попросите администратора настроить <code>VIDEO_STORAGE_CHAT_ID</code>."
    ),
    "inline.error.publish_photo": (
        "❌ <b>Не удалось опубликовать</b>\n\n"
        "Inline-storage не настроен или файл не загрузился. "
        "Попросите администратора настроить <code>VIDEO_STORAGE_CHAT_ID</code>."
    ),
    "inline.error.publish_mp3": (
        "❌ <b>Не удалось опубликовать MP3</b>\n\n"
        "Inline-storage не настроен. Попросите администратора настроить <code>VIDEO_STORAGE_CHAT_ID</code>."
    ),
    "inline.error.send": "❌ <b>Не удалось отправить</b>\n\nПопробуйте позже.",
    "inline.error.send_mp3": "❌ <b>Не удалось отправить MP3</b>\n\nПопробуйте позже.",
    "inline.error.mp3_convert": "❌ <b>Не удалось конвертировать в MP3</b>\n\nFFmpeg не найден или ошибка обработки.",
    # ── Admin commands ──────────────────────────────────────────────────
    "admin.only": "⛔ Эта команда доступна только администраторам.",
    "admin.invalid_id": "❌ Некорректный ID пользователя. Укажите числовой ID.",
    "admin.adduser.usage": (
        "📝 <b>Использование:</b>\n"
        "<code>/adduser USER_ID</code>\n\n"
        "Пример: <code>/adduser 123456789</code>"
    ),
    "admin.adduser.success": "✅ Пользователь {info} добавлен!",
    "admin.adduser.exists": "ℹ️ Пользователь <code>{user_id}</code> уже существует.",
    "admin.removeuser.usage": (
        "📝 <b>Использование:</b>\n"
        "<code>/removeuser USER_ID</code>\n\n"
        "Пример: <code>/removeuser 123456789</code>"
    ),
    "admin.removeuser.success": "✅ Пользователь {info} удалён!",
    "admin.removeuser.not_found": "❌ Пользователь <code>{user_id}</code> не найден.",
    "admin.users.empty": "📝 Список пользователей пуст.",
    "admin.users.title": "👥 <b>Разрешённые пользователи:</b>",
    "admin.users.added_at": "добавлен: {date}",
    "admin.users.date_unknown": "—",
    "admin.stats.text": (
        "📊 <b>Статистика бота</b>\n\n"
        "<b>Всего:</b>\n"
        "• Загрузок: {total}\n"
        "• Успешных: {success}\n"
        "• Неудачных: {failed}\n"
        "• Активных пользователей: {active}\n\n"
        "<b>За 24 часа:</b>\n"
        "• Загрузок: {total_24h}\n"
        "• Успешных: {success_24h}\n\n"
        "<b>За 7 дней:</b>\n"
        "• Загрузок: {total_7d}\n"
        "• Успешных: {success_7d}\n\n"
        "<b>По платформам (всего):</b>\n"
    ),
    "admin.userstats.usage": (
        "📝 <b>Использование:</b>\n"
        "<code>/userstats USER_ID</code>\n\n"
        "Пример: <code>/userstats 123456789</code>"
    ),
    "admin.userstats.title": "📊 <b>Статистика пользователя</b>",
    "admin.userstats.id": "🆔 ID: <code>{user_id}</code>",
    "admin.userstats.name": "👤 Имя: {name}",
    "admin.userstats.username": "📛 Ник: @{username}",
    "admin.userstats.added": "📅 Добавлен: {date}",
    "admin.userstats.last_active": "🕐 Последняя активность: {date}",
    "admin.userstats.downloads_section": (
        "<b>Загрузки:</b>\n"
        "• Всего: {total}\n"
        "• Успешных: {success}\n"
        "• Неудачных: {failed}\n\n"
        "<b>По платформам:</b>\n"
    ),
    "admin.adminhelp.text": (
        "🔐 <b>Команды администратора:</b>\n\n"
        "👥 <b>Управление пользователями:</b>\n"
        "/adduser <code>USER_ID</code> — добавить пользователя\n"
        "/removeuser <code>USER_ID</code> — удалить пользователя\n"
        "/users — список всех пользователей\n\n"
        "📊 <b>Статистика:</b>\n"
        "/stats — общая статистика бота\n"
        "/userstats <code>USER_ID</code> — статистика пользователя\n\n"
        "💡 <b>Совет:</b> Чтобы узнать ID пользователя, попросите его отправить боту команду /id"
    ),
    # ── Bot command menu descriptions ──────────────────────────────────
    "menu.start": "Запустить бота",
    "menu.help": "Справка по использованию",
    "menu.download": "Скачать видео по URL",
    "menu.mp3": "Извлечь аудио в MP3",
    "menu.voice": "Конвертировать в голосовое сообщение",
    "menu.round": "Конвертировать в кружок (видео-заметка)",
    "menu.gif": "Конвертировать в GIF",
    "menu.id": "Показать ваш Telegram ID",
    "menu.language": "Выбрать язык интерфейса",
    "menu.adduser": "Добавить пользователя",
    "menu.removeuser": "Удалить пользователя",
    "menu.users": "Список пользователей",
    "menu.stats": "Глобальная статистика",
    "menu.userstats": "Статистика пользователя",
    "menu.adminhelp": "Справка для администратора",
    # ── Downloader error codes ──────────────────────────────────────────
    "downloader.error.unsupported_url": "URL не поддерживается. Поддерживаемые платформы: YouTube, Instagram, TikTok, X/Twitter",
    "downloader.error.no_info": "Не удалось получить информацию о видео",
    "downloader.error.no_playlist_video": "Не удалось получить видео из плейлиста",
    "downloader.error.file_too_large": "Скачанный файл слишком большой ({size_mb}MB). Максимум: {max_mb}MB",
    "downloader.error.file_not_downloaded": "Файл не был скачан",
    "downloader.error.ffmpeg_required": (
        "Нужен FFmpeg для скачивания этого видео (требуется склейка аудио+видео).\n"
        "Установите FFmpeg и добавьте его в PATH, затем попробуйте ещё раз."
    ),
    "downloader.error.video_unavailable": "Видео недоступно",
    "downloader.error.private_video": "Это приватное видео",
    "downloader.error.auth_required": "Требуется авторизация для просмотра этого видео",
    "downloader.error.download_failed": "Ошибка скачивания: {message}",
    "downloader.error.unexpected": "Неожиданная ошибка: {message}",
    "downloader.error.download_exception": "Ошибка при скачивании: {message}",
}
