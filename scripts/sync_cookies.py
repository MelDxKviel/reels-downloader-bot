#!/usr/bin/env python3
"""
sync_cookies.py — собрать куки YouTube/Instagram из локального браузера,
залить их на сервер с ботом и при необходимости перезапустить бота.

Как работает:
  1. Через `yt-dlp --cookies-from-browser` вытаскиваем ВЕСЬ cookie-jar браузера
     в один временный файл (одна операция — браузер хранит куки всех сайтов вместе).
  2. Фильтруем jar по доменам нужной платформы (на сервер уезжают только
     youtube/instagram-куки, а не весь браузерный сеанс).
  3. Заливаем каждый файл на сервер по SSH (контент идёт через stdin, не через
     аргументы — без проблем с длиной командной строки на Windows) и пишем его
     «на месте» (тот же inode), чтобы бот подхватил новые куки на лету.
  4. Если что-то изменилось — перезапускаем сервис бота (`docker compose up -d`).

Вся конфигурация — через переменные окружения. Список см. в
scripts/sync-cookies.env.example. Запуск:

    # обычный прогон (читает .env.sync / .env рядом или из окружения)
    uv run python scripts/sync_cookies.py
    # или просто: python scripts/sync_cookies.py

    # посмотреть что соберётся, ничего не заливая
    python scripts/sync_cookies.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, NoReturn, Optional, Tuple

# stdout может оказаться в кодировке без кириллицы (редирект в файл/пайп) — не падаем.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass


# --- Платформы ---------------------------------------------------------------

# Домены, чьи куки уезжают на сервер для каждой платформы.
# Для YouTube нужны и google.com-куки (авторизация аккаунта: SAPISID и т.п.).
PLATFORM_DOMAINS: Dict[str, Tuple[str, ...]] = {
    "youtube": ("youtube.com", "youtube-nocookie.com", "google.com"),
    "instagram": ("instagram.com", "cdninstagram.com"),
}

# Переменная окружения с путём к файлу на хосте сервера + дефолт (как в docker-compose).
PLATFORM_HOST_PATH_ENV: Dict[str, Tuple[str, str]] = {
    "youtube": ("YT_COOKIES_FILE_HOST_PATH", "./cookies.txt"),
    "instagram": ("INSTA_COOKIES_FILE_HOST_PATH", "./instagram-cookies.txt"),
}

# Ключевые куки авторизации — если их нет, скорее всего пользователь не залогинен.
PLATFORM_KEY_COOKIES: Dict[str, Tuple[str, ...]] = {
    "youtube": ("SID", "SAPISID", "__Secure-3PSID", "LOGIN_INFO"),
    "instagram": ("sessionid",),
}

PLATFORM_ALIASES: Dict[str, str] = {
    "yt": "youtube",
    "youtube": "youtube",
    "ig": "instagram",
    "insta": "instagram",
    "instagram": "instagram",
}

# Стабильный публичный ролик — «карьер», чтобы yt-dlp запустился и сбросил jar.
# Само видео не качается (--skip-download); важен только дамп куки на выходе.
DEFAULT_CARRIER_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


# --- Лог ---------------------------------------------------------------------


def _p(tag: str, msg: str) -> None:
    print(f"{tag} {msg}", flush=True)


def info(msg: str) -> None:
    _p("[*]", msg)


def ok(msg: str) -> None:
    _p("[OK]", msg)


def warn(msg: str) -> None:
    _p("[WARN]", msg)


def err(msg: str) -> None:
    _p("[ERR]", msg)


def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    sys.exit(code)


# --- Конфиг из окружения -----------------------------------------------------


def _parse_env_value(value: str) -> str:
    """Значение из .env: снять кавычки либо обрезать инлайн-комментарий ` # ...`.

    Кавычки сохраняют значение дословно (можно класть '#'). Без кавычек комментарий
    начинается с '#', перед которым пробел/таб — поэтому пароль вида secret#1 не режется
    (нет пробела перед '#'); если в значении нужен ' #', возьми его в кавычки.
    """
    value = value.strip()
    if value and value[0] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end != -1 else value[1:]
    for i, ch in enumerate(value):
        if ch == "#" and (i == 0 or value[i - 1] in " \t"):
            return value[:i].rstrip()
    return value


def load_env_file(path: Path) -> None:
    """Мини-парсер .env: KEY=VALUE, без перезаписи уже заданных переменных."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        die(f"не удалось прочитать env-файл {path}: {e}")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            os.environ.setdefault(key, _parse_env_value(value))


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str) -> bool:
    return env(name).lower() in ("1", "true", "yes", "on")


def split_cmd(value: str) -> List[str]:
    """Разбить строку команды/опций в argv.

    На Windows используем posix=False, иначе shlex съедает обратные слеши в путях
    (напр. YTDLP_CMD=C:\\tools\\yt-dlp.exe). Для удалённого sh-скрипта это не
    касается — там везде shlex.quote под POSIX.
    """
    return shlex.split(value, posix=(os.name != "nt"))


def env_required(name: str) -> str:
    value = env(name)
    if not value:
        die(
            f"не задана обязательная переменная {name}. "
            f"Заполни env (см. scripts/sync-cookies.env.example)."
        )
    return value


# --- yt-dlp ------------------------------------------------------------------


def detect_ytdlp() -> List[str]:
    """Подобрать рабочий способ вызова yt-dlp."""
    override = env("YTDLP_CMD")
    candidates: List[List[str]] = []
    if override:
        candidates.append(split_cmd(override))
    candidates.append([sys.executable, "-m", "yt_dlp"])
    candidates.append(["yt-dlp"])
    for cmd in candidates:
        try:
            r = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            return cmd
    die(
        "yt-dlp не найден. Установи его (`uv sync` в проекте, либо `pip install yt-dlp`) "
        "или укажи вызов через YTDLP_CMD."
    )


def build_browser_spec() -> str:
    """Собрать аргумент для --cookies-from-browser: BROWSER[+KEYRING][:PROFILE][::CONTAINER]."""
    browser = env("COOKIE_BROWSER", "chrome").lower()
    spec = browser
    keyring = env("COOKIE_BROWSER_KEYRING")
    profile = env("COOKIE_BROWSER_PROFILE")
    container = env("COOKIE_BROWSER_CONTAINER")
    if keyring:
        spec += "+" + keyring
    if profile:
        spec += ":" + profile
    if container:
        spec += "::" + container
    return spec


def extract_full_jar(ytdlp: List[str], browser_spec: str, out_path: Path) -> None:
    """Выгрузить весь cookie-jar браузера в Netscape-файл out_path."""
    carrier = env("COOKIE_CARRIER_URL", DEFAULT_CARRIER_URL)
    cmd = ytdlp + [
        "--ignore-config",
        "--cookies-from-browser",
        browser_spec,
        "--cookies",
        str(out_path),
        "--skip-download",
        "--no-playlist",
        "--ignore-errors",
        "--no-warnings",
        carrier,
    ]
    info(f"Выгружаю куки из браузера: {browser_spec}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        die("yt-dlp завис при чтении куки из браузера (timeout 180s).")
    except OSError as e:
        die(f"не удалось запустить yt-dlp: {e}")

    # yt-dlp сохраняет cookie-jar при выходе даже если извлечение URL не удалось,
    # поэтому ориентируемся на наличие файла, а не на код возврата.
    if not out_path.exists() or out_path.stat().st_size == 0:
        sys.stderr.write(r.stderr or "")
        hint = ""
        if browser_spec.split(":")[0].split("+")[0] in (
            "chrome",
            "chromium",
            "edge",
            "brave",
            "opera",
            "vivaldi",
        ):
            hint = (
                "\nПодсказка: Chrome 127+ шифрует куки (App-Bound Encryption) и yt-dlp "
                "иногда не может их расшифровать. Попробуй COOKIE_BROWSER=firefox, "
                "закрыть браузер на время выгрузки или указать отдельный профиль."
            )
        die(f"не удалось получить куки из браузера (пустой результат).{hint}")


# --- Фильтрация куки ---------------------------------------------------------


def _domain_matches(domain: str, suffixes: Tuple[str, ...]) -> bool:
    domain = domain.lstrip(".").lower()
    return any(domain == s or domain.endswith("." + s) for s in suffixes)


def filter_jar(jar_text: str, suffixes: Tuple[str, ...]) -> Tuple[List[str], set]:
    """Вернуть (строки куки для нужных доменов, множество имён куки).

    Сохраняет строки `#HttpOnly_...` — это полноценные куки (напр. instagram sessionid),
    а не комментарии.
    """
    kept: List[str] = []
    names: set = set()
    for raw in jar_text.splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        probe = line
        if line.startswith("#HttpOnly_"):
            probe = line[len("#HttpOnly_") :]
        elif line.startswith("#"):
            continue  # настоящий комментарий/заголовок
        parts = probe.split("\t")
        if len(parts) < 7:
            continue
        if _domain_matches(parts[0], suffixes):
            kept.append(line)
            names.add(parts[5])
    return kept, names


def render_cookie_file(lines: List[str]) -> bytes:
    header = "# Netscape HTTP Cookie File\n# Auto-generated by scripts/sync_cookies.py\n"
    body = "\n".join(lines)
    return (header + body + "\n").encode("utf-8")


# --- SSH ---------------------------------------------------------------------


def ssh_base() -> List[str]:
    args = ["ssh"]
    port = env("DEPLOY_PORT", "22")
    if port and port != "22":
        args += ["-p", port]
    key = env("DEPLOY_KEY")
    if key:
        args += ["-i", key]
    args += [
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=15",
    ]
    extra = env("SSH_EXTRA_OPTS")
    if extra:
        args += split_cmd(extra)
    args.append(f"{env_required('DEPLOY_USER')}@{env_required('DEPLOY_HOST')}")
    return args


def check_ssh_available() -> None:
    try:
        subprocess.run(["ssh", "-V"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        die(
            "ssh не найден в PATH. На Windows установи OpenSSH Client "
            "(Settings → Apps → Optional Features → OpenSSH Client)."
        )


class Transport:
    """Канал выполнения команд на сервере. run() возвращает (rc, stdout, stderr)."""

    def run(self, command: str, data: Optional[bytes] = None, timeout: int = 300):
        raise NotImplementedError

    def close(self) -> None:
        pass


class SubprocessSSH(Transport):
    """Системный ssh (аутентификация по ключу/agent). Без сторонних зависимостей."""

    def __init__(self) -> None:
        self._base = ssh_base()

    def run(self, command: str, data: Optional[bytes] = None, timeout: int = 300):
        r = subprocess.run(self._base + [command], input=data, capture_output=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr


class ParamikoSSH(Transport):
    """SSH через paramiko — поддерживает пароль; одно соединение на весь прогон."""

    def __init__(self) -> None:
        import paramiko

        host = env_required("DEPLOY_HOST")
        user = env_required("DEPLOY_USER")
        port = int(env("DEPLOY_PORT", "22") or "22")
        password = env("DEPLOY_PASSWORD") or None
        key = env("DEPLOY_KEY") or None
        passphrase = env("DEPLOY_KEY_PASSPHRASE") or None
        # при пароле не даём paramiko уходить в agent/поиск ключей
        use_keys = not password

        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(
                hostname=host,
                port=port,
                username=user,
                password=password,
                key_filename=key,
                passphrase=passphrase,
                allow_agent=use_keys,
                look_for_keys=use_keys,
                timeout=15,
            )
        except Exception:
            client.close()
            raise
        self._client = client

    def run(self, command: str, data: Optional[bytes] = None, timeout: int = 300):
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        if data is not None:
            stdin.write(data)
            stdin.flush()
        try:
            stdin.channel.shutdown_write()
        except Exception:
            pass
        out = stdout.read()
        err = stderr.read()
        rc = stdout.channel.recv_exit_status()
        return rc, out, err

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


def make_transport() -> Transport:
    """Выбрать транспорт: paramiko если задан пароль (или USE_PARAMIKO), иначе системный ssh."""
    want_paramiko = bool(env("DEPLOY_PASSWORD")) or env_bool("USE_PARAMIKO")
    if want_paramiko:
        try:
            import paramiko  # noqa: F401
        except ImportError:
            die(
                "Для SSH по паролю нужен paramiko. Запусти один из вариантов:\n"
                "    uv run --with paramiko python scripts/sync_cookies.py\n"
                "    uv add paramiko       (добавит в проект)\n"
                "    pip install paramiko"
            )
        info("SSH: paramiko (пароль/ключ).")
        try:
            return ParamikoSSH()
        except Exception as e:
            die(f"не удалось подключиться по SSH: {e}")
    check_ssh_available()
    info("SSH: системный ssh (ключ/agent).")
    return SubprocessSSH()


def _validated_target(deploy_path: str, target_path: str) -> tuple[str, str]:
    """Keep cookie targets strictly below the configured Linux deployment directory."""
    for value in (deploy_path, target_path):
        if not value or "\\" in value or any(ord(char) < 32 for char in value):
            raise ValueError("пути должны быть непустыми Linux-путями без управляющих символов")
        if ".." in value.split("/"):
            raise ValueError("переходы '..' в путях куки запрещены")

    root = PurePosixPath(deploy_path)
    if not root.is_absolute() or root == PurePosixPath("/") or deploy_path.startswith("//"):
        raise ValueError("DEPLOY_PATH должен быть абсолютным путём к каталогу проекта, не '/'")
    if target_path.endswith(("/", "/.")) or target_path.startswith("//"):
        raise ValueError("путь куки должен указывать на файл, а не каталог")

    target = PurePosixPath(target_path)
    if target.is_absolute():
        try:
            target = target.relative_to(root)
        except ValueError as exc:
            raise ValueError("файл куки должен находиться внутри DEPLOY_PATH") from exc
    if not target.parts:
        raise ValueError("путь куки не может совпадать с DEPLOY_PATH")
    return str(root), str(target)


def build_install_script(deploy_path: str, target_rel: str) -> str:
    """POSIX sh: принять контент из stdin и переписать файл на месте.

    Запись «на месте» (cat > target) сохраняет inode, поэтому смонтированный в
    контейнер файл обновляется без рестарта. Только пустую папку, созданную Docker
    вместо отсутствующего файла, можно заменить. Симлинки и специальные файлы запрещены.
    """
    deploy_path, target_rel = _validated_target(deploy_path, target_rel)
    q = shlex.quote
    parents = " ".join(q(part) for part in PurePosixPath(target_rel).parts[:-1])
    return f"""set -eu
umask 077
DEPLOY_PATH={q(deploy_path)}
TARGET_REL={q(target_rel)}
cd -P "$DEPLOY_PATH"
DEPLOY_PATH="$(pwd -P)"
PARENT="$DEPLOY_PATH"
for component in {parents}; do
  PARENT="$PARENT/$component"
  if [ -L "$PARENT" ] || {{ [ -e "$PARENT" ] && [ ! -d "$PARENT" ]; }}; then
    echo "Cookie parent must be a directory, not a symlink or special file" >&2
    exit 1
  fi
  if [ ! -d "$PARENT" ]; then mkdir -m 700 -- "$PARENT"; fi
done
TARGET="$DEPLOY_PATH/$TARGET_REL"
if [ -L "$TARGET" ] || {{ [ -e "$TARGET" ] && [ ! -f "$TARGET" ] && [ ! -d "$TARGET" ]; }}; then
  echo "Cookie target must be a regular file, not a symlink or special file" >&2
  exit 1
fi
if [ -f "$TARGET" ]; then
  link_count="$(stat -c %h -- "$TARGET")"
  if [ "$link_count" -ne 1 ]; then
    echo "Cookie target must not have additional hard links" >&2
    exit 1
  fi
fi
TMP="$(mktemp "$PARENT/.cookie-sync.XXXXXX")"
BACKUP=""
INSTALLING=0
CREATED=0
cleanup() {{
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$status" -ne 0 ] && [ "$INSTALLING" -eq 1 ]; then
    if [ -n "$BACKUP" ] && [ -f "$TARGET" ] && [ ! -L "$TARGET" ]; then
      cat "$BACKUP" > "$TARGET" || echo "Cookie rollback failed" >&2
    elif [ "$CREATED" -eq 1 ] && [ -f "$TARGET" ] && [ ! -L "$TARGET" ]; then
      rm -f -- "$TARGET"
    fi
  fi
  rm -f -- "$TMP"
  if [ -n "$BACKUP" ]; then rm -f -- "$BACKUP"; fi
  exit "$status"
}}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM
cat > "$TMP"
test -s "$TMP" || {{ echo "Refusing to install empty cookies" >&2; exit 1; }}
if [ -d "$TARGET" ]; then
  rmdir -- "$TARGET" || {{ echo "Cookie target directory must be empty" >&2; exit 1; }}
fi
if [ -f "$TARGET" ] && cmp -s "$TMP" "$TARGET"; then
  chmod 600 -- "$TARGET"
  echo UNCHANGED
else
  if [ -f "$TARGET" ]; then
    BACKUP="$(mktemp "$PARENT/.cookie-backup.XXXXXX")"
    cat "$TARGET" > "$BACKUP"
  else
    (set -C; : > "$TARGET")
    CREATED=1
  fi
  chmod 600 -- "$TARGET"
  INSTALLING=1
  cat "$TMP" > "$TARGET"
  INSTALLING=0
  echo CHANGED
fi
"""


def upload_and_install(
    transport: Transport, content: bytes, deploy_path: str, target_rel: str
) -> str:
    """Залить content на сервер в target_rel (отн. deploy_path) на месте.

    Возвращает 'CHANGED' или 'UNCHANGED'. Контент идёт через stdin команды.
    """
    try:
        remote = build_install_script(deploy_path, target_rel)
    except ValueError as exc:
        die(f"небезопасный путь куки: {exc}")
    rc, out, errb = transport.run(remote, data=content, timeout=120)
    if rc != 0:
        sys.stderr.write(errb.decode("utf-8", "replace"))
        die(f"не удалось залить {target_rel} на сервер (ssh exit {rc}).")
    lines = out.decode("utf-8", "replace").strip().splitlines()
    return lines[-1] if lines else "UNCHANGED"


def restart_bot(transport: Transport, deploy_path: str) -> None:
    q = shlex.quote
    override = env("RESTART_CMD")
    if override:
        cmd = override
    else:
        compose = env("COMPOSE_CMD", "docker compose")
        service = env("BOT_SERVICE", "bot")
        # up -d: применит изменения compose (напр. новый маунт инсты) и пересоздаст
        # контейнер при необходимости; если ничего не изменилось — no-op.
        cmd = f"cd {q(deploy_path)} && {compose} up -d {q(service)}"
    info(f"Перезапускаю бота: {cmd}")
    rc, out, errb = transport.run(cmd, timeout=300)
    if rc != 0:
        sys.stderr.write(errb.decode("utf-8", "replace"))
        die(f"перезапуск не удался (ssh exit {rc}).")
    ok("Бот перезапущен.")


# --- Платформы / аргументы ---------------------------------------------------


def resolve_platforms(cli_value: Optional[str]) -> List[str]:
    raw = cli_value if cli_value is not None else env("COOKIE_PLATFORMS", "youtube,instagram")
    result: List[str] = []
    for token in raw.replace(";", ",").split(","):
        token = token.strip().lower()
        if not token:
            continue
        canonical = PLATFORM_ALIASES.get(token)
        if not canonical:
            die(f"неизвестная платформа '{token}'. Допустимо: youtube, instagram.")
        if canonical not in result:
            result.append(canonical)
    if not result:
        die("не выбрано ни одной платформы (COOKIE_PLATFORMS пуст).")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Собрать куки из браузера, залить на сервер и перезапустить бота.",
    )
    parser.add_argument("--env-file", metavar="PATH", help="загрузить переменные из файла")
    parser.add_argument(
        "--platforms",
        metavar="LIST",
        help="переопределить COOKIE_PLATFORMS (напр. youtube,instagram)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только собрать и показать, что уедет; ничего не заливать",
    )
    parser.add_argument(
        "--restart",
        choices=["auto", "always", "never"],
        help="переопределить RESTART_BOT (auto: только при изменениях)",
    )
    parser.add_argument("--verbose", action="store_true", help="подробный вывод")
    return parser.parse_args()


# --- main --------------------------------------------------------------------


def main() -> int:
    args = parse_args()

    # 1. Загрузка конфигурации: --env-file -> .env.sync -> .env (реальное окружение в приоритете).
    if args.env_file:
        path = Path(args.env_file)
        if not path.exists():
            die(f"env-файл не найден: {path}")
        load_env_file(path)
        info(f"Загружен env-файл: {path}")
    else:
        for candidate in (Path(".env.sync"), Path(".env")):
            if candidate.exists():
                load_env_file(candidate)
                info(f"Загружен env-файл: {candidate}")
                break

    dry_run = args.dry_run or env_bool("DRY_RUN")
    restart_mode = (args.restart or env("RESTART_BOT", "auto")).lower()
    if restart_mode not in ("auto", "always", "never"):
        die(f"RESTART_BOT должен быть auto|always|never, а не '{restart_mode}'.")
    strict = env_bool("COOKIE_SYNC_STRICT")
    platforms = resolve_platforms(args.platforms)

    info(f"Платформы: {', '.join(platforms)}")
    transport: Optional[Transport] = None
    if not dry_run:
        deploy_path = env_required("DEPLOY_PATH")
        # проверим обязательные переменные и подключимся заранее, до тяжёлой выгрузки
        env_required("DEPLOY_HOST")
        env_required("DEPLOY_USER")
        transport = make_transport()
    else:
        deploy_path = env("DEPLOY_PATH", "<DEPLOY_PATH>")
        warn("DRY-RUN: на сервер ничего не уйдёт, бот не тронут.")

    ytdlp = detect_ytdlp()
    browser_spec = build_browser_spec()

    tmpdir = Path(tempfile.mkdtemp(prefix="cookie-sync-"))
    preview_dir = Path("cookie-sync-preview")
    changed_any = False
    problems = 0
    try:
        full_jar = tmpdir / "full_jar.txt"
        extract_full_jar(ytdlp, browser_spec, full_jar)
        jar_text = full_jar.read_text(encoding="utf-8", errors="ignore")

        for platform in platforms:
            suffixes = PLATFORM_DOMAINS[platform]
            lines, names = filter_jar(jar_text, suffixes)
            if not lines:
                warn(
                    f"{platform}: куки не найдены (домены {', '.join(suffixes)}). "
                    f"Залогинься в браузере {browser_spec.split(':')[0]} и повтори."
                )
                problems += 1
                continue

            key_cookies = PLATFORM_KEY_COOKIES.get(platform, ())
            if key_cookies and not (set(key_cookies) & names):
                warn(
                    f"{platform}: нет ключевых куки авторизации "
                    f"({', '.join(key_cookies)}) — возможно, ты не залогинен."
                )

            content = render_cookie_file(lines)
            env_name, default_path = PLATFORM_HOST_PATH_ENV[platform]
            target_rel = env(env_name, default_path)
            info(f"{platform}: {len(lines)} куки -> {target_rel}")

            if dry_run:
                preview_dir.mkdir(exist_ok=True)
                preview_file = preview_dir / f"{platform}.txt"
                preview_file.write_bytes(content)
                ok(f"{platform}: превью записано в {preview_file}")
                continue

            status = upload_and_install(transport, content, deploy_path, target_rel)
            if status == "CHANGED":
                changed_any = True
                ok(f"{platform}: обновлено на сервере ({target_rel}).")
            else:
                info(f"{platform}: без изменений ({target_rel}).")

        # 4. Перезапуск.
        if dry_run:
            info("DRY-RUN завершён.")
        elif restart_mode == "never":
            info("Перезапуск отключён (RESTART_BOT=never).")
        elif restart_mode == "always" or (restart_mode == "auto" and changed_any):
            restart_bot(transport, deploy_path)
        else:
            info("Куки не менялись — перезапуск не требуется.")

    finally:
        if transport is not None:
            transport.close()
        for child in tmpdir.glob("*"):
            try:
                child.unlink()
            except OSError:
                pass
        try:
            tmpdir.rmdir()
        except OSError:
            pass

    if problems and strict:
        die(f"{problems} платформ(а) без куки, COOKIE_SYNC_STRICT=1 -> ошибка.", code=3)
    if problems:
        warn(f"Готово, но с предупреждениями: {problems} платформ(а) без куки.")
    else:
        ok("Готово.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        err("Прервано пользователем.")
        sys.exit(130)
