# RespectASO

<p align="center">
  <img src="desktop/assets/RespectASO.iconset/icon_256x256.png" alt="RespectASO" width="128">
</p>

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![macOS](https://img.shields.io/badge/macOS-Download_.dmg-purple?logo=apple&logoColor=white)](https://github.com/respectlytics/respectaso/releases/latest)
[![Version](https://img.shields.io/github/v/release/respectlytics/respectaso?color=purple&label=version)](https://github.com/respectlytics/respectaso/releases/latest)

**Free, open-source ASO keyword research tool for macOS. No API keys. No accounts. No data leaves your machine.**

RespectASO helps iOS developers research App Store keywords privately. Download the `.dmg`, drag to Applications, and get keyword popularity scores, difficulty analysis, competitor breakdowns, and download estimates — all without sending your research data to third-party services.

---

## Quick Start (Development)

```bash
# Install just (if not already installed)
brew install just

# Start the backend
just backend
```

This runs migrations and starts the Django dev server. Open the URL printed in the terminal to inspect the app.

## Why RespectASO?

Most ASO tools require paid subscriptions, API keys, and send your keyword research to their servers. RespectASO takes a different approach:

- **No API keys or credentials needed** — uses only the public iTunes Search API
- **Runs entirely on your machine** — all API calls originate from your local network
- **No telemetry, no analytics, no tracking** — zero data sent to any third party
- **Free and open-source** — AGPL-3.0 licensed, forever
- **Native Mac app** — download the `.dmg`, drag to Applications, done

## Features

| Feature | Description |
|---------|-------------|
| **Keyword Popularity** | Estimated popularity scores (1–100) based on analysis of iTunes Search API competitor data |
| **Difficulty Score** | Competition difficulty analysis across multiple factors with ranking tier breakdowns for Top 5, Top 10, and Top 20 |
| **Ranking Tiers** | Separate difficulty analysis for Top 5, Top 10, and Top 20 positions — because breaking into the top 5 is different from reaching the top 20 |
| **Download Estimates** | Estimated daily downloads per ranking position based on search volume, tap-through rates, and conversion rates |
| **Competitor Analysis** | See the top 10 apps ranking for each keyword with ratings, reviews, genre, release date, and direct App Store links |
| **Country Opportunity Finder** | Scan up to 30 App Store regions at once to find which countries offer the best ranking opportunities |
| **Multi-Keyword Search** | Research up to 20 keywords at once (comma-separated) |
| **Multi-Country Search** | Search the same keyword across multiple countries simultaneously |
| **App Rank Tracking** | Add your apps and see where you rank for each keyword alongside competitor data |
| **Search History** | Browse past keyword research with sorting, filtering, and expandable detail views |
| **CSV Export** | Export your keyword research data for use in spreadsheets |
| **ASO Targeting Advice** | Automatic keyword classification (Sweet Spot, Good Target, Hidden Gem, High Competition, Moderate, Low Volume, Avoid) based on opportunity scoring |

## Download

**→ [Download RespectASO.dmg](https://github.com/respectlytics/respectaso/releases/latest)** (macOS 12+, Apple Silicon)

Open the `.dmg` and drag **RespectASO** into your **Applications** folder. Your data is stored at `~/Library/Application Support/RespectASO/` and survives app updates.

<details>
<summary><strong>🐳 Docker (free features only)</strong></summary>

Docker provides the **free edition** of RespectASO (keyword research, difficulty scoring, ranking tracking). AI-powered Pro features require the native macOS app above.

#### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) installed and running

#### Install via Docker

```bash
git clone https://github.com/respectlytics/respectaso.git
cd respectaso
docker compose up -d
# Open http://localhost
```

</details>

## How Scoring Works

RespectASO uses the **iTunes Search API** as its only data source — no Apple Search Ads credentials, no scraping, no paid APIs.

### Popularity Score (1–100)

Estimates how frequently a keyword is searched by analyzing multiple signals from iTunes Search results, including the number and quality of competing apps, keyword relevance patterns, and market depth. Higher scores mean more people are searching for that keyword.

### Difficulty Score (1–100)

Estimates how hard it would be to rank for a keyword by evaluating competition strength across factors like existing app ratings, market dominance, publisher diversity, and content relevance.

**Tiers:** Very Easy (&lt;16) · Easy (16–35) · Moderate (36–55) · Hard (56–75) · Very Hard (76–90) · Extreme (91+)

| Factor | Weight | What It Measures |
|--------|--------|------------------|
| Rating volume | 30% | How many ratings competitors have |
| Dominant players | 20% | Whether a few apps dominate (100K+ ratings) |
| Rating quality | 10% | Average star ratings of competitors |
| Market maturity | 10% | How long competitors have been on the App Store |
| Publisher diversity | 10% | Whether results come from many publishers or a few |
| App count | 10% | Total number of relevant results |
| Content relevance | 10% | How well competitors match the keyword |

For the complete algorithm reference with every formula, calibration band, and interpolation method, see [docs/iOS_KEYWORD_VOLUME.md](docs/iOS_KEYWORD_VOLUME.md).

## Project Structure

Estimates daily downloads per ranking position based on search volume, expected tap-through rates by position, and install conversion rates. Results are shown as conservative–optimistic ranges with tier breakdowns for Top 5, Top 6–10, and Top 11–20.

For full methodology details, visit the **Methodology** page inside the app or explore the [source code](https://github.com/respectlytics/respectaso).

## Configuration

<details>
<summary><strong>Custom Local Domain (Docker only)</strong></summary>

If running via Docker, you can use a cleaner URL. Add this to your `/etc/hosts` file:

```bash
sudo sh -c 'echo "127.0.0.1  respectaso.private" >> /etc/hosts'
```
aso/
  services.py       # Core engine: all scoring algorithms (2,068 lines)
  models.py          # Database models: App, Keyword, SearchResult
  views.py           # HTTP handlers (HTML pages + JSON API endpoints)
  scheduler.py       # Background auto-refresh daemon
  forms.py           # Django form validation
  templates/aso/     # Server-rendered UI (Django templates + Tailwind + vanilla JS)
  templatetags/      # Custom template filters
  migrations/        # Database schema evolution

core/                # Django project config (settings, urls, wsgi)
desktop/             # Native macOS app wrapper (pywebview + PyInstaller)
static/              # Favicons and logos
docs/                # Documentation
```

## Tech Stack

- **Python 3.12** + **Django 5.1** — backend + ORM
- **SQLite** — local single-user database
- **pywebview** — native macOS WebKit window
- **Tailwind CSS** (CDN) — dark theme UI (server-rendered, no JS framework)
- **iTunes Search API** — only external data source (public, no auth)

## Documentation

| Document | Description |
|----------|-------------|
| [iOS Keyword Volume & Algorithms](docs/iOS_KEYWORD_VOLUME.md) | Complete reference for all scoring algorithms, signals, weights, formulas, and external trend data sources |
| [Trend Signals Roadmap](docs/TREND_SIGNALS_ROADMAP.md) | Modular architecture for 17 external trend data sources with API docs, pricing, and implementation plans |
| [Headless Migration Guide](docs/HEADLESS_MIGRATION.md) | How to extract the core engine and integrate into a FastAPI + PostgreSQL project |
| [Security Policy](docs/SECURITY.md) | Vulnerability reporting and security design principles |
| [Contributing Guide](docs/CONTRIBUTING.md) | How to submit bug reports, feature requests, and pull requests |

## Privacy

RespectASO is designed with privacy as a core principle:

- **100% local** — the tool runs entirely on your machine
- **No accounts** — no registration, no login, no user tracking
- **No telemetry** — zero analytics, zero phone-home, zero data collection
- **No API keys** — uses only the public iTunes Search API
- **Your data stays yours** — keyword research never leaves your network

## License

[AGPL-3.0](LICENSE) — free to use, modify, and distribute. If you modify and deploy RespectASO as a service, you must share your changes under the same license.

## Contact

[respectaso@loheden.com](mailto:respectaso@loheden.com)

---

**Built by [Respectlytics](https://respectlytics.com/?utm_source=respectaso&utm_medium=readme&utm_campaign=oss)** — Privacy-focused mobile analytics for iOS & Android.
