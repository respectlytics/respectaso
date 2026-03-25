# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RespectASO is a local-first, privacy-respecting App Store Optimization (ASO) keyword research tool for iOS developers. It runs entirely on the user's machine using the public iTunes Search API — no API keys, no accounts, no telemetry.

**Stack:** Python 3.12 + Django 5.1, SQLite, Gunicorn, WhiteNoise, Tailwind CSS (CDN), Docker

## Development Commands

```bash
# Local development (without Docker)
python manage.py migrate
python manage.py runserver

# Docker (primary workflow)
docker compose up -d          # Start
docker compose down           # Stop
docker compose build --no-cache && docker compose up -d  # Rebuild
```

There is no formal test suite. Manual testing is done via the running app or Django shell:

```bash
python manage.py shell
```

## Architecture

The project is a single Django app (`aso/`) with a thin project config layer (`core/`).

### Key modules

| File | Responsibility |
|------|---------------|
| `aso/services.py` | Core algorithms: `PopularityEstimator`, `DifficultyCalculator`, `DownloadEstimator`, `ITunesSearchService` (iTunes API wrapper) |
| `aso/models.py` | Three models: `App`, `Keyword`, `SearchResult` with FK relationships |
| `aso/views.py` | All HTTP handlers — dashboard, search, opportunity finder, bulk operations, CSV export, auto-refresh status |
| `aso/scheduler.py` | Background daemon that auto-refreshes stale keywords hourly |
| `aso/forms.py` | Django forms: `AppForm`, `KeywordSearchForm`, `OpportunitySearchForm` |
| `core/settings.py` | Django settings; app version is defined here as `VERSION` |
| `core/context_processors.py` | Injects `VERSION` into all templates |

### Data flow

1. User submits keyword(s) + country/countries via the dashboard or opportunity finder
2. `views.py` validates input via forms, then delegates to `services.py`
3. `ITunesSearchService` calls the iTunes Search API
4. `PopularityEstimator` and `DifficultyCalculator` score the results using multi-signal models
5. `DownloadEstimator` projects download volumes per ranking position
6. Results are saved to `SearchResult` → `Keyword` → `App` via Django ORM
7. Templates render scoring with color-coded tiers; `aso_tags.py` provides custom template filters

### Scoring algorithms (services.py)

- **Popularity (1–100):** 6-signal model — result count, leader strength, title match density, market depth, specificity penalty, exact phrase bonus
- **Difficulty (0–100):** 7 weighted sub-scores — rating volume (30%), dominant players (20%), rating quality, market maturity, publisher diversity, app count, content relevance (each 10%)
- **Download estimates:** 3-stage pipeline — popularity → daily searches → position tap-through rate → install conversion

### Persistence

SQLite at `DATA_DIR/db.sqlite3`. In Docker, `DATA_DIR` is `/app/data`, mounted as a named volume (`aso_data`) so data survives container rebuilds. The `.env` file and secret key are also stored in this volume.

### Auto-refresh scheduler

`scheduler.py` runs as a background thread started from `aso/apps.py` `ready()`. It wakes hourly, checks which keywords are stale (based on their `updated_at`), and silently refreshes them. The `auto-refresh/status/` endpoint exposes its state to the frontend.

## URL structure

- `/` — Dashboard (search + history)
- `/opportunity/` — Multi-country opportunity finder
- `/apps/` — App rank tracker
- `/methodology/` — Scoring explanation
- `/setup/` — Deployment guide
- `/search/` — Search API endpoint (POST)
- `/export/history.csv` — CSV export

## Version management

The version string lives in `core/settings.py` as `VERSION`. It is surfaced in the UI via the context processor and checked against GitHub releases via `/version-check/`.
