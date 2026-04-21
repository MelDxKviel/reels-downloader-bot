# Security Policy

## Supported Versions

Only the latest commit on the `main` branch receives security fixes. No versioned releases are maintained at this time.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report security issues privately by emailing the maintainer or opening a [GitHub Security Advisory](https://github.com/meldxkviel/reels-downloader-bot/security/advisories/new) in this repository.

Include in your report:

- A clear description of the vulnerability and its impact
- Steps to reproduce or a proof-of-concept
- The commit hash or date you tested against
- Any suggested remediation if you have one

You can expect an acknowledgement within **72 hours** and a resolution or status update within **14 days**. If the issue is confirmed, a fix will be committed and you will be credited unless you prefer otherwise.

## Sensitive Credentials — Operator Responsibilities

This bot handles several secrets that must be protected by whoever deploys it:

| Secret | Risk if leaked |
|---|---|
| `BOT_TOKEN` | Full control over the bot (send messages, access updates, impersonate the bot) |
| `ADMIN_USERS` | Reveals which Telegram accounts have admin privileges |
| `DATABASE_URL` | Direct access to all user IDs and download history |
| `POSTGRES_PASSWORD` | Database compromise |
| `YT_COOKIES_FILE` / `INSTA_COOKIES_FILE` | Account takeover on YouTube / Instagram |

Recommended practices:

- Store all secrets in `.env` and **never commit it** — `.env` must be listed in `.gitignore`.
- Never commit cookie files; add `cookies/` to `.gitignore`.
- In Docker deployments use secrets or environment-only injection — avoid baking secrets into images.
- Rotate `BOT_TOKEN` immediately via [@BotFather](https://t.me/BotFather) if it is ever exposed.
- Restrict database access to the bot container only (use Docker networks, not a public port).

## Access Control

The bot enforces a two-tier whitelist:

- **Admins** — defined statically in `ADMIN_USERS` at startup. Keep this list minimal.
- **Regular users** — added at runtime via `/adduser`. Only admins can add or remove them.

Users not in either group have all messages silently dropped by `UserAccessMiddleware` before any handler runs. Verify that your `ADMIN_USERS` value is correct before deployment — an empty value disables all admin commands.

## Known Limitations

- **No rate limiting** — the bot does not limit how frequently an allowed user can trigger downloads. A whitelisted user can exhaust server resources or hit platform rate limits. Consider adding an external rate limiter if you expose the bot to many users.
- **Local file storage** — downloaded files are stored on disk. Ensure the `DOWNLOAD_DIR` volume is not publicly accessible and has appropriate filesystem permissions.
- **Cookie file validation** — cookie files are validated for format on startup, but their contents are not verified cryptographically. A tampered cookie file will cause download failures rather than a security incident.
- **yt-dlp dependency** — this project relies on `yt-dlp`, a third-party library that executes platform-specific extraction code. Keep it updated (`uv sync --upgrade`) to pick up security and compatibility fixes.

## Out of Scope

The following are not considered security vulnerabilities for this project:

- Denial-of-service via Telegram's own infrastructure
- Rate limiting by YouTube, Instagram, TikTok, or X on the operator's IP
- Content policy violations on the platforms being downloaded from
- Issues reproducible only with an already-compromised `BOT_TOKEN`
