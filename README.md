# RespectASO

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](docker-compose.yml)
[![Version](https://img.shields.io/badge/version-1.0.0-purple.svg)](https://github.com/respectlytics/respectaso/releases/tag/v1.0.0)

**Free, open-source ASO keyword research tool — self-hosted via Docker. No API keys. No accounts. No data leaves your machine.**

RespectASO helps iOS developers research App Store keywords privately. Run it locally with a single Docker command and get keyword popularity scores, difficulty analysis, competitor breakdowns, and download estimates — all without sending your research data to third-party services.

---

## Why RespectASO?

Most ASO tools require paid subscriptions, API keys, and send your keyword research to their servers. RespectASO takes a different approach:

- **No API keys or credentials needed** — uses only the public iTunes Search API
- **Runs entirely on your machine** — all API calls originate from your local network
- **No telemetry, no analytics, no tracking** — zero data sent to any third party
- **Free and open-source** — AGPL-3.0 licensed, forever
- **Single Docker command** — up and running in 30 seconds

## Features

| Feature | Description |
|---------|-------------|
| **Keyword Popularity** | Estimated popularity scores (1–100) derived from a 6-signal model analyzing iTunes Search competitor data |
| **Difficulty Score** | 7 weighted sub-scores (rating volume, dominant players, rating quality, market age, publisher diversity, app count, content relevance) with ranking tier analysis |
| **Ranking Tiers** | Separate difficulty analysis for Top 5, Top 10, and Top 20 positions — because breaking into the top 5 is different from reaching the top 20 |
| **Download Estimates** | Estimated daily downloads per ranking position based on search volume, tap-through rates, and conversion rates |
| **Competitor Analysis** | See the top 10 apps ranking for each keyword with ratings, reviews, genre, release date, and direct App Store links |
| **Country Opportunity Finder** | Scan up to 30 App Store regions at once to find which countries offer the best ranking opportunities for your keyword |
| **Multi-Keyword Search** | Research up to 20 keywords at once (comma-separated) |
| **Multi-Country Search** | Search the same keyword across multiple countries simultaneously |
| **App Rank Tracking** | Add your apps and see where you rank for each keyword alongside competitor data |
| **Search History** | Browse past keyword research with sorting, filtering, and expandable detail views |
| **CSV Export** | Export your keyword research data for use in spreadsheets |
| **ASO Targeting Advice** | Automatic keyword classification (Sweet Spot, Hidden Gem, Low Volume, Avoid, etc.) based on popularity vs. difficulty |

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) installed and running

### 1. Clone and run

```bash
git clone https://github.com/respectlytics/respectaso.git
cd respectaso
docker compose up -d
```

### 2. Open in your browser

**→ [http://localhost](http://localhost)**

That's it. The first startup takes a few seconds (database migration + static files).

On first launch, the tool automatically:
- Generates a secure Django secret key
- Runs database migrations
- Collects static files
- Starts the Gunicorn server

You'll see the RespectASO dashboard ready to search. Type a keyword, select a country, and click Search.

## How Scoring Works

RespectASO uses the **iTunes Search API** as its only data source — no Apple Search Ads credentials, no scraping, no paid APIs.

### Popularity Score (1–100)

A 6-signal composite model that estimates how often a keyword is searched:

| Signal | Weight | What It Measures |
|--------|--------|------------------|
| Result count | 0–25 pts | How many apps appear for this keyword |
| Leader strength | 0–30 pts | Rating volume of the top-ranking apps |
| Title match density | 0–20 pts | How many apps use this exact keyword in their title |
| Market depth | 0–10 pts | Whether strong apps appear deep in results |
| Specificity penalty | -5 to -30 | Adjusts for generic terms that inflate result counts |
| Exact phrase bonus | 0–15 pts | Rewards multi-word keywords with precise matches |

### Difficulty Score (1–100)

A 7-factor weighted system that estimates how hard it is to rank:

| Factor | Weight | What It Measures |
|--------|--------|------------------|
| Rating volume | 30% | How many ratings competitors have |
| Dominant players | 20% | Whether a few apps dominate (100K+ ratings) |
| Rating quality | 10% | Average star ratings of competitors |
| Market maturity | 10% | How long competitors have been on the App Store |
| Publisher diversity | 10% | Whether results come from many publishers or a few |
| App count | 10% | Total number of relevant results |
| Content relevance | 10% | How well competitors match the keyword |

**Interpretation:** Very Easy (&lt;16) · Easy (16–35) · Moderate (36–55) · Hard (56–75) · Very Hard (76–90) · Extreme (91+)

### Download Estimates

A 3-stage pipeline estimates daily downloads per ranking position:

1. **Popularity → Daily Searches** — piecewise-linear mapping calibrated against real App Store observations
2. **Position → Tap-Through Rate** — power-law decay from position #1 (30%) to position #20 (0.06%)
3. **Tap → Install Conversion** — range of 35%–55% for free apps

Results are shown as conservative–optimistic ranges per position, with tier breakdowns for Top 5, Top 6–10, and Top 11–20.

For more details, visit the **Methodology** page inside the app.

## Configuration

### Changing the Port

The default `docker-compose.yml` maps port **80** on your host to port 8080 in the container. If port 80 is already in use:

```yaml
ports:
  - "9090:8080"  # Access at http://localhost:9090
```

### Custom Local Domain

For a cleaner URL, add this to your `/etc/hosts` file:

**macOS / Linux:**
```bash
sudo sh -c 'echo "127.0.0.1  respectaso.private" >> /etc/hosts'
```

**Windows (run as Administrator):**
```
echo 127.0.0.1  respectaso.private >> C:\Windows\System32\drivers\etc\hosts
```

Then access the tool at **[http://respectaso.private](http://respectaso.private)**

The `.private` TLD is reserved by [RFC 6762](https://www.rfc-editor.org/rfc/rfc6762) and avoids conflicts with macOS mDNS resolution (unlike `.local`).

### Data Persistence

Your data is stored in a Docker volume (`aso_data`). Your database and secret key survive container restarts and rebuilds.

To back up your data:
```bash
docker cp respectaso-web-1:/app/data ./backup
```

### Updating to a New Version

```bash
cd respectaso
git pull
docker compose down
docker compose build --no-cache
docker compose up -d
```

Your data is preserved — only the application code is updated.

### Automatic Startup

The `docker-compose.yml` includes `restart: unless-stopped`, so the tool automatically restarts when Docker starts. No need to run `docker compose up` again after a reboot.

## Tech Stack

- **Python 3.12** + **Django 5.1**
- **SQLite** — local single-user database
- **Gunicorn** — production WSGI server
- **WhiteNoise** — efficient static file serving
- **Tailwind CSS** (CDN) — dark theme UI
- **Docker** — single-command deployment

## Privacy

RespectASO is designed with privacy as a core principle:

- **100% local** — the tool runs entirely on your machine inside Docker
- **No accounts** — no registration, no login, no user tracking
- **No telemetry** — zero analytics, zero phone-home, zero data collection
- **No API keys** — uses only the public iTunes Search API (no credentials required)
- **No third-party services** — all API calls go directly from your machine to Apple's public API
- **Your data stays yours** — keyword research, competitor analysis, and search history never leave your network

We built RespectASO because we believe developers should be able to research keywords without handing their competitive intelligence to a third party.

## License

[AGPL-3.0](LICENSE) — free to use, modify, and distribute. If you modify and deploy RespectASO as a service, you must share your changes under the same license.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Security

See [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

## Contact

[respectlytics@loheden.com](mailto:respectlytics@loheden.com)

---

**Built by [Respectlytics](https://respectlytics.com/?utm_source=respectaso&utm_medium=readme&utm_campaign=oss)** — Privacy-focused mobile analytics for iOS & Android. We help developers avoid collecting personal data in the first place.
