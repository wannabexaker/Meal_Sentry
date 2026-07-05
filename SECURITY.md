# Security Policy

## Reporting a Vulnerability

Please report security issues privately via **GitHub Security Advisories**:
open a draft advisory at
`https://github.com/wannabexaker/Meal_Sentry/security/advisories/new`.

Do **not** open public GitHub issues for security vulnerabilities.

## Scope & notes

- **Secrets**: the Telegram bot token and the allowed user id are read only from the
  environment (`MEALSENTRY_TOKEN`, `MEALSENTRY_USER_ID`). Never commit them. `config.yaml`
  and `mealsentry.env` are git-ignored.
- **Access control**: the bot is single-user — it hard-rejects any Telegram id other than
  `MEALSENTRY_USER_ID`.
- **API**: the FastAPI backend binds to `127.0.0.1` by default and is read-only. If you
  change `api_host` to a non-loopback address, put it behind an authenticating reverse
  proxy — it ships without its own auth.
- **Database**: all SQL is parameterized. The SQLite file lives outside version control.
