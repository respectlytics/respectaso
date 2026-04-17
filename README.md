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
| **Country Opportunity Finder** | Scan up to 30 App Store regions at once to find which countries offer the best ranking opportunities for your keyword |
| **Multi-Keyword Search** | Research up to 20 keywords at once (comma-separated) |
| **Multi-Country Search** | Search the same keyword across multiple countries simultaneously |
| **App Rank Tracking** | Add your apps and see where you rank for each keyword alongside competitor data |
| **Search History** | Browse past keyword research with sorting, filtering, and expandable detail views |
| **CSV Export** | Export your keyword research data for use in spreadsheets |
| **ASO Targeting Advice** | Automatic keyword classification (Sweet Spot, Good Target, Hidden Gem, High Competition, Moderate, Low Volume, Avoid) based on opportunity scoring |

## Quick Start

### 1. Download

**→ [Download RespectASO.dmg](https://github.com/respectlytics/respectaso/releases/latest)** (macOS 12+, Apple Silicon)

### 2. Install

Open the `.dmg` and drag **RespectASO** into your **Applications** folder.

### 3. Launch

Open RespectASO from Applications (or Spotlight: ⌘ Space → "RespectASO"). The app window opens automatically — type a keyword, select a country, and click Search.

> **First launch:** If macOS shows a security dialog, right-click the app → Open → Open. This is only needed once — the app is code-signed and notarized by Apple.

### Updating

When an update is available, a banner appears on the Dashboard with release notes and a **Download Update** button. Download the new `.dmg`, drag to Applications (replace the old version), and relaunch. Your data is preserved — it lives in `~/Library/Application Support/RespectASO/`, separate from the app bundle.

### Data Location

Your keywords, search history, and settings are stored at:

```
~/Library/Application Support/RespectASO/
```

This data survives app updates and deletions. Delete this folder only if you want a completely fresh start.

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
```

Open **[http://localhost](http://localhost)** in your browser.

#### Updating (Docker)

```bash
cd respectaso
git pull
docker compose down
docker compose build --no-cache
docker compose up -d
```

#### Migrating from Docker to Native App

Your existing data carries over. Run this one-time migration:

```bash
curl -fsSL https://raw.githubusercontent.com/respectlytics/respectaso/main/desktop/migrate-from-docker.sh | bash
```

Then install the native app and verify your data is intact. Once confirmed:

```bash
docker compose down     # Stop the container
docker compose down -v  # Also remove the volume (only after confirming native app works)
```

</details>

## How Scoring Works

RespectASO uses the **iTunes Search API** as its only data source — no Apple Search Ads credentials, no scraping, no paid APIs.

### Popularity Score (1–100)

Estimates how frequently a keyword is searched by analyzing multiple signals from iTunes Search results, including the number and quality of competing apps, keyword relevance patterns, and market depth. Higher scores mean more people are searching for that keyword.

### Difficulty Score (1–100)

Estimates how hard it would be to rank for a keyword by evaluating competition strength across factors like existing app ratings, market dominance, publisher diversity, and content relevance.

**Tiers:** Very Easy (&lt;16) · Easy (16–35) · Moderate (36–55) · Hard (56–75) · Very Hard (76–90) · Extreme (91+)

### Download Estimates

Estimates daily downloads per ranking position based on search volume, expected tap-through rates by position, and install conversion rates. Results are shown as conservative–optimistic ranges with tier breakdowns for Top 5, Top 6–10, and Top 11–20.

For full methodology details, visit the **Methodology** page inside the app or explore the [source code](https://github.com/respectlytics/respectaso).

## Configuration

<details>
<summary><strong>Custom Local Domain (Docker only)</strong></summary>

If running via Docker, you can use a cleaner URL. Add this to your `/etc/hosts` file:

```bash
sudo sh -c 'echo "127.0.0.1  respectaso.private" >> /etc/hosts'
```

Then access the tool at **[http://respectaso.private](http://respectaso.private)**

The `.private` TLD is reserved by [RFC 6762](https://www.rfc-editor.org/rfc/rfc6762) and avoids conflicts with macOS mDNS resolution (unlike `.local`).

</details>

## Tech Stack

- **Python 3.12** + **Django 5.1**
- **pywebview** — native macOS WebKit window
- **SQLite** — local single-user database
- **wsgiref** — built-in Python WSGI server
- **WhiteNoise** — efficient static file serving
- **Tailwind CSS** (CDN) — dark theme UI
- **PyInstaller** — macOS `.app` bundle

## Privacy

RespectASO is designed with privacy as a core principle:

- **100% local** — the tool runs entirely on your machine as a native app
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

[respectaso@loheden.com](mailto:respectaso@loheden.com)

---

**Built by [Respectlytics](https://respectlytics.com/?utm_source=respectaso&utm_medium=readme&utm_campaign=oss)** — Privacy-focused mobile analytics for iOS & Android. We help developers avoid collecting personal data in the first place.
