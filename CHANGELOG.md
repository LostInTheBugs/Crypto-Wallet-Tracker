# Changelog

## 2026.08.002-c1 — 2026-08-01

- **Fix — false update notification**: the installed version was read as `0.0.0` at runtime in the Docker container (the `VERSION` file was never copied into the image), so `/api/version/changes` wrongly reported `update_available: true` and listed every release as newer than the installed one.
- **Reliable installed-version lookup**: `_load_current_version()` now tries, in order: the `APP_VERSION` environment variable, the repo-root `VERSION` file, a `VERSION` file in the current working directory, `/app/VERSION` (container layout), and finally the `verCurrent` value baked into `public/index.html`. Content is stripped and validated against the version regex; `0.0.0` is returned only as a last resort.
- **Docker build**: the image now `COPY`s `VERSION` to `/app/VERSION` and accepts an `APP_VERSION` build arg (wired through `docker-compose.yml`), so the container always knows the actually deployed version — `current` from `/api/version/changes` now matches `verCurrent`.

## 2026.08.002 (2026-08-01)

- **Update client**: new-version notification badge + "What's new" diff window showing all release notes between installed and latest versions, powered by GitHub Releases API.
- **Version endpoint**: `/api/version/latest` now uses GitHub Releases API (returns `tag`, `name`, `body`, `html_url`, `published_at`) instead of the tags API.
- **New endpoint**: `GET /api/version/changes` returns all releases strictly newer than the installed version with full release notes, sorted newest first.
- **Version comparison**: strict 3-digit `YYYY.MM.NNN` with optional `-cX` correction suffix (e.g. `2026.08.002-c1`). Non-conforming tags are ignored.
- **Caching**: short-lived in-memory cache (300s) for GitHub API responses to avoid rate limiting.
- **i18n**: FR/EN strings for the notification badge, changes modal, and all new UI elements.
- **Health endpoint**: `/api/health` now reads version from the `VERSION` file instead of a hardcoded value.

## 2026.08.001 (2026-08-01)

- **Port**: default listen port changed from 80 to 8001 (overridable via `PORT` env variable). Updated in `Dockerfile`, `docker-compose.yml`, `.env.example`, and `install.sh`.
- **Version**: new `VERSION` file at repository root containing `2026.08.001`. Version number updated in `README.md` and `public/index.html` (`verCurrent`).
- **README**: added GitHub releases link, updated version and port documentation.
- **CHANGELOG**: created `CHANGELOG.md` (this file).
