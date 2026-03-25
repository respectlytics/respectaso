# MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an MCP server to RespectASO that exposes 12 tools (read, search, management) over HTTP+SSE so LLM API calls from other local Docker projects can do ASO research.

**Architecture:** FastMCP (`mcp` Python SDK) runs as an ASGI app mounted at `/mcp/` via `core/asgi.py`. The `/mcp/` path bypasses Django's URL routing and is dispatched directly to FastMCP's Starlette app. All other paths continue to Django as before. Tool functions call the Django ORM and `services.py` directly — no HTTP hop. Gunicorn switches from sync to `uvicorn.workers.UvicornWorker` to support SSE streaming; this is a 2-line Dockerfile change, not a server migration.

**Tech Stack:** Python 3.12, Django 5.1, `mcp[cli]>=1.6,<2.0`, `uvicorn>=0.30,<1.0`, Django ORM, asgiref

---

## Implementation Note: Deviation from Spec

The approved spec described `aso/mcp_views.py` as a Django view bridge using `asgiref.sync.async_to_sync`. During planning, this approach was found insufficient for SSE streaming (Gunicorn sync workers cannot stream async SSE responses without blocking). The plan instead:

- Routes `/mcp` at the ASGI level in `core/asgi.py` (no Django view needed)
- Switches Gunicorn to uvicorn workers (2-line Dockerfile change)
- Eliminates the need for `aso/mcp_views.py` entirely

The spec goal is fully achieved; only the implementation detail changes.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `aso/mcp_server.py` | FastMCP instance + all 12 tool definitions |
| Create | `aso/tests/__init__.py` | Test package marker |
| Create | `aso/tests/test_mcp_read_tools.py` | Tests for 5 read tools |
| Create | `aso/tests/test_mcp_search_tools.py` | Tests for 4 search tools (iTunes mocked) |
| Create | `aso/tests/test_mcp_management_tools.py` | Tests for 3 management tools |
| Create | `docs/mcp.md` | User-facing integration documentation |
| Modify | `core/asgi.py` | Route `/mcp` to FastMCP ASGI app |
| Modify | `requirements.txt` | Add `mcp[cli]` and `uvicorn` |
| Modify | `Dockerfile` | Switch CMD to uvicorn workers |
| Modify | `docker-compose.yml` | Add `container_name` and `respectaso_net` network |
| No change | `core/urls.py` | `/mcp/` is routed at the ASGI layer — no Django URL entry needed |

---

## Task 1: Dependencies + Docker Config

**Files:**
- Modify: `requirements.txt`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add dependencies to requirements.txt**

Replace the contents of `requirements.txt` with:
```
Django>=5.1,<5.2
gunicorn==23.0.0
uvicorn>=0.30,<1.0
whitenoise==6.8.2
requests==2.32.3
python-dotenv==1.0.1
mcp[cli]>=1.6,<2.0
```

- [ ] **Step 2: Verify the mcp package installs cleanly**

```bash
pip install "mcp[cli]>=1.6,<2.0" "uvicorn>=0.30,<1.0"
```

Expected: packages install without conflicts. Note the exact installed version (e.g., `mcp==1.6.0`) and pin it in requirements.txt if desired.

- [ ] **Step 3: Switch Dockerfile CMD to uvicorn workers**

In `Dockerfile`, change line 17 from:
```dockerfile
CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "2"]
```
To:
```dockerfile
CMD ["gunicorn", "core.asgi:application", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8080", "--workers", "1"]
```

- [ ] **Step 4: Update docker-compose.yml**

Replace `docker-compose.yml` with:
```yaml
services:
  web:
    build: .
    container_name: respectaso-web
    ports:
      - "80:8080"
    volumes:
      - aso_data:/app/data
    restart: unless-stopped
    environment:
      - SECRET_KEY=${SECRET_KEY:-}
    networks:
      - respectaso_net

volumes:
  aso_data:

networks:
  respectaso_net:
    name: respectaso_net
    driver: bridge
    attachable: true
```

- [ ] **Step 5: Verify Django still starts cleanly**

```bash
python manage.py check
```

Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 6: Commit**

```bash
git add requirements.txt Dockerfile docker-compose.yml
git commit -m "feat: add mcp + uvicorn deps; add Docker network config"
```

---

## Task 2: ASGI Routing

**Files:**
- Modify: `core/asgi.py`

- [ ] **Step 1: Create the tests package**

Create `aso/tests/__init__.py` (empty file).

Note: The ASGI routing in `core/asgi.py` is invisible to Django's test client (`django.test.Client` goes through the WSGI stack, not ASGI). ASGI routing is validated by the Docker smoke test in Task 6 instead. No unit test is written for this task. Django is fully initialized by `get_asgi_application()` before any request arrives, so deferred imports inside tool functions are safe.

- [ ] **Step 2: Create aso/mcp_server.py with a bare FastMCP instance**

```python
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from .forms import COUNTRY_CHOICES  # module-level — required for test patching

mcp = FastMCP("RespectASO")
```

- [ ] **Step 3: Update core/asgi.py to route /mcp to FastMCP**

```python
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

_django_app = get_asgi_application()
_mcp_asgi_app = None


def _get_mcp_app():
    global _mcp_asgi_app
    if _mcp_asgi_app is None:
        from aso.mcp_server import mcp
        _mcp_asgi_app = mcp.streamable_http_app()
    return _mcp_asgi_app


async def application(scope, receive, send):
    if scope["type"] == "http" and scope.get("path", "").startswith("/mcp"):
        await _get_mcp_app()(scope, receive, send)
    else:
        await _django_app(scope, receive, send)
```

- [ ] **Step 4: Verify Django still checks cleanly**

```bash
python manage.py check
```

Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 5: Commit**

```bash
git add core/asgi.py aso/mcp_server.py aso/tests/__init__.py
git commit -m "feat: route /mcp to FastMCP ASGI app"
```

---

## Task 3: Read Tools

**Files:**
- Modify: `aso/mcp_server.py`
- Create: `aso/tests/test_mcp_read_tools.py`

The 5 read tools query the Django ORM directly. No iTunes API calls.

Output shapes:
- `list_apps` → `list[dict]` with keys `id, name, bundle_id, track_id, store_url, icon_url, seller_name`
- `list_keywords` → `list[dict]` with keys `id, keyword, app_id, app_name, results` (results = list of latest per country)
- `get_keyword_scores` → `dict` with keys `popularity_score, difficulty_score, difficulty_label, targeting_advice, competitors_data, app_rank, searched_at` (or `{"error": "..."}`)
- `get_keyword_trend` → `list[dict]` with keys `date, popularity, difficulty, rank, country`
- `get_search_history` → `dict` with keys `results, page, total_pages, total_count`

- [ ] **Step 1: Write failing tests for all 5 read tools**

Create `aso/tests/test_mcp_read_tools.py`:
```python
from django.test import TestCase
from django.utils import timezone

from aso.models import App, Keyword, SearchResult
from aso.mcp_server import list_apps, list_keywords, get_keyword_scores, get_keyword_trend, get_search_history


class ListAppsToolTest(TestCase):
    def test_empty(self):
        result = list_apps()
        self.assertEqual(result, [])

    def test_returns_app_fields(self):
        App.objects.create(name="My App", bundle_id="com.test.app", track_id=123456)
        result = list_apps()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "My App")
        self.assertEqual(result[0]["bundle_id"], "com.test.app")
        self.assertIn("id", result[0])


class ListKeywordsToolTest(TestCase):
    def setUp(self):
        self.app = App.objects.create(name="My App")
        self.kw = Keyword.objects.create(keyword="fitness", app=self.app)
        SearchResult.objects.create(
            keyword=self.kw,
            popularity_score=60,
            difficulty_score=40,
            country="us",
        )

    def test_returns_all_keywords_no_filter(self):
        result = list_keywords()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["keyword"], "fitness")
        self.assertEqual(len(result[0]["results"]), 1)

    def test_filters_by_app_id(self):
        other_kw = Keyword.objects.create(keyword="running")
        result = list_keywords(app_id=self.app.id)
        keywords = [r["keyword"] for r in result]
        self.assertIn("fitness", keywords)
        self.assertNotIn("running", keywords)

    def test_latest_result_per_country(self):
        # Two results for same keyword+country — only latest should appear
        SearchResult.objects.create(
            keyword=self.kw,
            popularity_score=70,
            difficulty_score=35,
            country="us",
        )
        result = list_keywords(app_id=self.app.id)
        self.assertEqual(len(result[0]["results"]), 1)
        self.assertEqual(result[0]["results"][0]["popularity_score"], 70)


class GetKeywordScoresToolTest(TestCase):
    def setUp(self):
        self.kw = Keyword.objects.create(keyword="yoga")
        self.sr = SearchResult.objects.create(
            keyword=self.kw,
            popularity_score=55,
            difficulty_score=30,
            country="us",
            app_rank=5,
        )

    def test_not_found(self):
        result = get_keyword_scores(keyword_id=99999)
        self.assertIn("error", result)

    def test_returns_scores(self):
        result = get_keyword_scores(keyword_id=self.kw.id)
        self.assertEqual(result["popularity_score"], 55)
        self.assertEqual(result["difficulty_score"], 30)
        self.assertEqual(result["app_rank"], 5)
        self.assertIn("label", result["targeting_advice"])
        self.assertIn("description", result["targeting_advice"])
        self.assertNotIn("css_classes", result["targeting_advice"])

    def test_country_filter(self):
        SearchResult.objects.create(
            keyword=self.kw, popularity_score=10, difficulty_score=80, country="gb"
        )
        result = get_keyword_scores(keyword_id=self.kw.id, country="gb")
        self.assertEqual(result["popularity_score"], 10)


class GetKeywordTrendToolTest(TestCase):
    def setUp(self):
        self.kw = Keyword.objects.create(keyword="meditation")
        SearchResult.objects.create(
            keyword=self.kw, popularity_score=40, difficulty_score=25, country="us"
        )
        SearchResult.objects.create(
            keyword=self.kw, popularity_score=45, difficulty_score=28, country="us"
        )

    def test_returns_data_points(self):
        result = get_keyword_trend(keyword_id=self.kw.id)
        self.assertEqual(len(result), 2)
        self.assertIn("date", result[0])
        self.assertIn("popularity", result[0])
        self.assertIn("difficulty", result[0])

    def test_not_found(self):
        result = get_keyword_trend(keyword_id=99999)
        self.assertIn("error", result[0])


class GetSearchHistoryToolTest(TestCase):
    def setUp(self):
        kw = Keyword.objects.create(keyword="running")
        for i in range(30):
            kw_i = Keyword.objects.create(keyword=f"keyword{i}")
            SearchResult.objects.create(
                keyword=kw_i, popularity_score=i, difficulty_score=i, country="us"
            )

    def test_pagination_default_page(self):
        result = get_search_history()
        self.assertEqual(len(result["results"]), 25)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["total_pages"], 2)

    def test_page_2(self):
        result = get_search_history(page=2)
        self.assertEqual(result["page"], 2)
        self.assertLessEqual(len(result["results"]), 25)

    def test_country_filter(self):
        kw = Keyword.objects.create(keyword="gb_only")
        SearchResult.objects.create(
            keyword=kw, popularity_score=50, difficulty_score=50, country="gb"
        )
        result = get_search_history(country="gb")
        self.assertEqual(result["total_count"], 1)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python manage.py test aso.tests.test_mcp_read_tools -v 2
```

Expected: Multiple import errors (`cannot import name 'list_apps' from 'aso.mcp_server'`).

- [ ] **Step 3: Implement all 5 read tools in aso/mcp_server.py**

Replace `aso/mcp_server.py` with (the bare instance from Task 2 is now expanded):

```python
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from .forms import COUNTRY_CHOICES  # module-level — required for test patching

mcp = FastMCP("RespectASO")


# ---------------------------------------------------------------------------
# Read Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_apps() -> list[dict]:
    """List all tracked iOS apps."""
    from .models import App
    return [
        {
            "id": a.id,
            "name": a.name,
            "bundle_id": a.bundle_id,
            "track_id": a.track_id,
            "store_url": a.store_url,
            "icon_url": a.icon_url,
            "seller_name": a.seller_name,
        }
        for a in App.objects.all()
    ]


@mcp.tool()
def list_keywords(app_id: int | None = None) -> list[dict]:
    """
    List tracked keywords with their latest score per country.

    If app_id is provided, returns only keywords linked to that app.
    """
    from django.db.models import Max
    from .models import Keyword, SearchResult

    qs = Keyword.objects.select_related("app").all()
    if app_id is not None:
        qs = qs.filter(app_id=app_id)

    results = []
    for kw in qs:
        # Latest result ID per country for this keyword
        latest_ids = (
            SearchResult.objects.filter(keyword=kw)
            .values("country")
            .annotate(latest_id=Max("id"))
            .values_list("latest_id", flat=True)
        )
        latest_results = SearchResult.objects.filter(id__in=latest_ids).order_by("country")
        results.append({
            "id": kw.id,
            "keyword": kw.keyword,
            "app_id": kw.app_id,
            "app_name": kw.app.name if kw.app else None,
            "results": [
                {
                    "country": r.country,
                    "popularity_score": r.popularity_score,
                    "difficulty_score": r.difficulty_score,
                    "difficulty_label": r.difficulty_label,
                    "searched_at": r.searched_at.isoformat(),
                }
                for r in latest_results
            ],
        })
    return results


@mcp.tool()
def get_keyword_scores(keyword_id: int, country: str | None = None) -> dict:
    """
    Get the latest scores for a keyword.

    Returns popularity, difficulty, targeting advice, competitor data, and app rank.
    If country is omitted, returns the most recently searched country's result.
    """
    from .models import Keyword, SearchResult

    try:
        kw = Keyword.objects.get(id=keyword_id)
    except Keyword.DoesNotExist:
        return {"error": f"Keyword {keyword_id} not found"}

    qs = SearchResult.objects.filter(keyword=kw)
    if country:
        qs = qs.filter(country=country)

    result = qs.order_by("-searched_at").first()
    if not result:
        return {"error": f"No results found for keyword {keyword_id}" + (f" in {country}" if country else "")}

    _, advice_label, _, advice_desc = result.targeting_advice
    return {
        "keyword": kw.keyword,
        "country": result.country,
        "popularity_score": result.popularity_score,
        "difficulty_score": result.difficulty_score,
        "difficulty_label": result.difficulty_label,
        "targeting_advice": {"label": advice_label, "description": advice_desc},
        "competitors_data": result.competitors_data,
        "app_rank": result.app_rank,
        "searched_at": result.searched_at.isoformat(),
    }


@mcp.tool()
def get_keyword_trend(keyword_id: int, country: str | None = None) -> list[dict]:
    """
    Get historical trend data for a keyword.

    Returns a list of data points ordered by date. Optionally filter by country.
    """
    from .models import Keyword, SearchResult

    try:
        kw = Keyword.objects.get(id=keyword_id)
    except Keyword.DoesNotExist:
        return [{"error": f"Keyword {keyword_id} not found"}]

    qs = SearchResult.objects.filter(keyword=kw).order_by("searched_at")
    if country:
        qs = qs.filter(country=country)

    return [
        {
            "date": r.searched_at.strftime("%Y-%m-%d"),
            "popularity": r.popularity_score,
            "difficulty": r.difficulty_score,
            "rank": r.app_rank,
            "country": r.country,
        }
        for r in qs
    ]


@mcp.tool()
def get_search_history(
    app_id: int | None = None,
    country: str | None = None,
    page: int = 1,
) -> dict:
    """
    Get paginated search history (latest result per keyword+country).

    Returns 25 results per page.
    """
    from django.db.models import Max
    from .models import SearchResult

    filter_kwargs: dict = {}
    if app_id is not None:
        filter_kwargs["keyword__app_id"] = app_id
    if country:
        filter_kwargs["country"] = country.lower()

    latest_ids = (
        SearchResult.objects.filter(**filter_kwargs)
        .values("keyword_id", "country")
        .annotate(latest_id=Max("id"))
        .values_list("latest_id", flat=True)
    )

    qs = (
        SearchResult.objects.filter(id__in=list(latest_ids))
        .select_related("keyword", "keyword__app")
        .order_by("-searched_at")
    )

    per_page = 25
    total_count = qs.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page

    results = [
        {
            "keyword": r.keyword.keyword,
            "app_name": r.keyword.app.name if r.keyword.app else None,
            "country": r.country,
            "popularity_score": r.popularity_score,
            "difficulty_score": r.difficulty_score,
            "difficulty_label": r.difficulty_label,
            "app_rank": r.app_rank,
            "searched_at": r.searched_at.isoformat(),
        }
        for r in qs[offset : offset + per_page]
    ]

    return {
        "results": results,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
```

- [ ] **Step 4: Run read tool tests**

```bash
python manage.py test aso.tests.test_mcp_read_tools -v 2
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add aso/mcp_server.py aso/tests/test_mcp_read_tools.py
git commit -m "feat: add 5 MCP read tools with tests"
```

---

## Task 4: Search Tools

**Files:**
- Modify: `aso/mcp_server.py`
- Create: `aso/tests/test_mcp_search_tools.py`

These tools call the iTunes API. All tests mock `ITunesSearchService` to avoid network calls.

- [ ] **Step 1: Write failing tests for search tools**

Create `aso/tests/test_mcp_search_tools.py`:
```python
from unittest.mock import patch, MagicMock
from django.test import TestCase

from aso.models import App, Keyword, SearchResult
from aso.mcp_server import search_keywords, opportunity_search, refresh_keyword, bulk_refresh_keywords

MOCK_COMPETITORS = [
    {
        "trackId": 111,
        "trackName": "Fitness App",
        "bundleId": "com.fitness.app",
        "artworkUrl100": "https://example.com/icon.png",
        "userRatingCount": 5000,
        "averageUserRating": 4.5,
        "sellerName": "Fitness Co",
        "primaryGenreName": "Health & Fitness",
        "version": "1.0",
        "releaseDate": "2023-01-01T00:00:00Z",
        "price": 0.0,
    }
]


def _mock_itunes():
    """Return a mock ITunesSearchService that returns MOCK_COMPETITORS."""
    svc = MagicMock()
    svc.search_apps.return_value = MOCK_COMPETITORS
    svc.find_app_rank.return_value = None
    svc.lookup_by_id.return_value = None
    return svc


class SearchKeywordsToolTest(TestCase):
    @patch("aso.mcp_server.ITunesSearchService", return_value=_mock_itunes())
    def test_creates_search_result(self, _mock):
        result = search_keywords(keywords="fitness", countries=["us"])
        self.assertIn("us", result["results_by_country"])
        self.assertEqual(len(result["results_by_country"]["us"]), 1)
        self.assertEqual(result["results_by_country"]["us"][0]["keyword"], "fitness")
        # Verify DB was written
        self.assertEqual(SearchResult.objects.count(), 1)

    @patch("aso.mcp_server.ITunesSearchService", return_value=_mock_itunes())
    def test_rejects_more_than_20_keywords(self, _mock):
        result = search_keywords(keywords=",".join(f"kw{i}" for i in range(25)), countries=["us"])
        # Should process exactly 20 (capped at 20, no prior results to skip in clean DB)
        self.assertEqual(
            sum(len(v) for v in result["results_by_country"].values()), 20
        )

    @patch("aso.mcp_server.ITunesSearchService", return_value=_mock_itunes())
    def test_multi_country_produces_opportunity_ranking(self, _mock):
        result = search_keywords(keywords="fitness", countries=["us", "gb"])
        self.assertIn("opportunity_ranking", result)
        self.assertEqual(len(result["opportunity_ranking"]), 1)


class OpportunitySearchToolTest(TestCase):
    @patch("aso.mcp_server.COUNTRY_CHOICES", [("us", "United States"), ("gb", "United Kingdom")])
    @patch("aso.mcp_server.ITunesSearchService", return_value=_mock_itunes())
    def test_returns_country_ranking(self, _mock, _mock_countries):
        result = opportunity_search(keyword="yoga")
        self.assertEqual(result["keyword"], "yoga")
        self.assertEqual(result["total_countries"], 2)
        self.assertIn("results", result)
        self.assertIn("opportunity", result["results"][0])


class RefreshKeywordToolTest(TestCase):
    def setUp(self):
        self.kw = Keyword.objects.create(keyword="running")

    @patch("aso.mcp_server.ITunesSearchService", return_value=_mock_itunes())
    def test_creates_new_result(self, _mock):
        result = refresh_keyword(keyword_id=self.kw.id)
        self.assertTrue(result["success"])
        self.assertEqual(result["result"]["keyword"], "running")
        self.assertEqual(SearchResult.objects.count(), 1)

    def test_not_found(self):
        result = refresh_keyword(keyword_id=99999)
        self.assertIn("error", result)


class BulkRefreshKeywordsToolTest(TestCase):
    def setUp(self):
        self.app = App.objects.create(name="My App")
        Keyword.objects.create(keyword="yoga", app=self.app)
        Keyword.objects.create(keyword="meditation", app=self.app)

    @patch("aso.mcp_server.ITunesSearchService", return_value=_mock_itunes())
    def test_refreshes_all_for_app(self, _mock):
        result = bulk_refresh_keywords(app_id=self.app.id)
        self.assertTrue(result["success"])
        self.assertEqual(result["refreshed"], 2)

    @patch("aso.mcp_server.ITunesSearchService", return_value=_mock_itunes())
    def test_unassigned_only_when_no_app_id(self, _mock):
        Keyword.objects.create(keyword="unassigned_kw")  # no app
        result = bulk_refresh_keywords()
        self.assertEqual(result["refreshed"], 1)  # only the unassigned one
```

- [ ] **Step 2: Run to confirm failures**

```bash
python manage.py test aso.tests.test_mcp_search_tools -v 2
```

Expected: ImportError — search tool functions not yet defined.

- [ ] **Step 3: Implement the 4 search tools in aso/mcp_server.py**

Append to `aso/mcp_server.py`:

```python
# ---------------------------------------------------------------------------
# Search Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def search_keywords(
    keywords: str,
    countries: list[str] | None = None,
    app_id: int | None = None,
) -> dict:
    """
    Search 1–20 keywords across 1–5 countries (comma-separated keywords string).

    Triggers iTunes API calls. Rate-limited at 2s per call.
    Worst case: 20 keywords × 5 countries ≈ 3 minutes. Connection stays open.
    Returns results grouped by country plus an opportunity ranking for multi-country searches.
    """
    import time
    from django.utils import timezone
    from .models import App, Keyword, SearchResult
    from .services import ITunesSearchService, DifficultyCalculator, PopularityEstimator, DownloadEstimator

    if countries is None:
        countries = ["us"]
    countries = countries[:5]

    kw_list = [k.strip() for k in keywords.split(",") if k.strip()][:20]
    if not kw_list:
        return {"error": "No keywords provided"}

    app = None
    if app_id:
        try:
            app = App.objects.get(id=app_id)
        except App.DoesNotExist:
            return {"error": f"App {app_id} not found"}

    itunes = ITunesSearchService()
    diff_calc = DifficultyCalculator()
    pop_est = PopularityEstimator()
    dl_est = DownloadEstimator()

    results_by_country: dict = {}
    skipped: list[str] = []
    call_count = 0

    for country in countries:
        country_results = []
        for kw_text in kw_list:
            kw_obj, created = Keyword.objects.get_or_create(
                keyword=kw_text.lower(), app=app
            )
            today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
            if not created and kw_obj.results.filter(
                country=country, searched_at__gte=today_start
            ).exists():
                skipped.append(f"{kw_text} ({country.upper()})")
                continue

            if call_count > 0:
                time.sleep(2)
            call_count += 1

            competitors = itunes.search_apps(kw_text, country=country, limit=25)
            difficulty_score, breakdown = diff_calc.calculate(competitors, keyword=kw_text)

            app_rank = None
            if app and app.track_id:
                app_rank = itunes.find_app_rank(kw_text, app.track_id, country=country)

            popularity = pop_est.estimate(competitors, kw_text)
            dl_estimates = dl_est.estimate(popularity or 0, country=country)
            breakdown["download_estimates"] = dl_estimates

            sr = SearchResult.upsert_today(
                keyword=kw_obj,
                popularity_score=popularity,
                difficulty_score=difficulty_score,
                difficulty_breakdown=breakdown,
                competitors_data=competitors,
                app_rank=app_rank,
                country=country,
            )
            _, advice_label, _, advice_desc = sr.targeting_advice
            country_results.append({
                "keyword": kw_text,
                "country": country,
                "popularity_score": popularity,
                "difficulty_score": difficulty_score,
                "difficulty_label": sr.difficulty_label,
                "targeting_advice": {"label": advice_label, "description": advice_desc},
                "app_rank": app_rank,
                "result_id": sr.id,
            })
        results_by_country[country] = country_results

    # Opportunity ranking for multi-country
    opportunity_ranking: list[dict] = []
    if len(countries) > 1:
        kw_map: dict = {}
        for country, cresults in results_by_country.items():
            for r in cresults:
                kw = r["keyword"]
                if kw not in kw_map:
                    kw_map[kw] = {}
                pop = r["popularity_score"] or 0
                diff = r["difficulty_score"]
                kw_map[kw][country] = {
                    "popularity": pop,
                    "difficulty": diff,
                    "opportunity": round(pop * (100 - diff) / 100),
                }
        for kw, country_data in kw_map.items():
            best = max(country_data, key=lambda c: country_data[c]["opportunity"])
            opportunity_ranking.append({
                "keyword": kw,
                "countries": country_data,
                "best_country": best,
                "best_score": country_data[best]["opportunity"],
            })
        opportunity_ranking.sort(key=lambda x: x["best_score"], reverse=True)

    response: dict = {"results_by_country": results_by_country, "opportunity_ranking": opportunity_ranking}
    if skipped:
        response["skipped"] = skipped
    return response


@mcp.tool()
def opportunity_search(keyword: str, app_id: int | None = None) -> dict:
    """
    Search a single keyword across all 30 countries.

    Returns a ranked list of countries by opportunity score.
    Runtime: ~60 seconds (30 iTunes calls at 2s each). Connection stays open.
    """
    import time
    from .models import App
    # COUNTRY_CHOICES is imported at module level for test patchability
    from .services import ITunesSearchService, DifficultyCalculator, PopularityEstimator, DownloadEstimator

    kw_text = keyword.strip().lower()
    if not kw_text:
        return {"error": "No keyword provided"}

    app = None
    if app_id:
        try:
            app = App.objects.get(id=app_id)
        except App.DoesNotExist:
            return {"error": f"App {app_id} not found"}

    itunes = ITunesSearchService()
    diff_calc = DifficultyCalculator()
    pop_est = PopularityEstimator()
    dl_est = DownloadEstimator()

    results = []
    for i, (country_code, _country_name) in enumerate(COUNTRY_CHOICES):
        if i > 0:
            time.sleep(2)

        competitors = itunes.search_apps(kw_text, country=country_code, limit=25)
        difficulty_score, breakdown = diff_calc.calculate(competitors, keyword=kw_text)
        popularity = pop_est.estimate(competitors, kw_text)
        dl_estimates = dl_est.estimate(popularity or 0, country=country_code)
        breakdown["download_estimates"] = dl_estimates

        app_rank = None
        if app and app.track_id:
            app_rank = itunes.find_app_rank(kw_text, app.track_id, country=country_code)

        opportunity = round(popularity * (100 - difficulty_score) / 100) if popularity else 0
        results.append({
            "country": country_code,
            "popularity": popularity,
            "difficulty": difficulty_score,
            "difficulty_label": (
                "Very Easy" if difficulty_score <= 15
                else "Easy" if difficulty_score <= 35
                else "Moderate" if difficulty_score <= 55
                else "Hard" if difficulty_score <= 75
                else "Very Hard" if difficulty_score <= 90
                else "Extreme"
            ),
            "opportunity": opportunity,
            "app_rank": app_rank,
            "competitor_count": len(competitors),
        })

    results.sort(key=lambda x: x["opportunity"], reverse=True)
    return {"keyword": kw_text, "results": results, "total_countries": len(results)}


@mcp.tool()
def refresh_keyword(keyword_id: int, country: str = "us") -> dict:
    """Re-run scoring for a single keyword+country combination."""
    import time
    from .models import Keyword, SearchResult
    from .services import ITunesSearchService, DifficultyCalculator, PopularityEstimator, DownloadEstimator

    try:
        kw = Keyword.objects.select_related("app").get(id=keyword_id)
    except Keyword.DoesNotExist:
        return {"error": f"Keyword {keyword_id} not found"}

    itunes = ITunesSearchService()
    diff_calc = DifficultyCalculator()
    pop_est = PopularityEstimator()
    dl_est = DownloadEstimator()

    competitors = itunes.search_apps(kw.keyword, country=country, limit=25)
    difficulty_score, breakdown = diff_calc.calculate(competitors, keyword=kw.keyword)

    app_rank = None
    if kw.app and kw.app.track_id:
        app_rank = itunes.find_app_rank(kw.keyword, kw.app.track_id, country=country)

    popularity = pop_est.estimate(competitors, kw.keyword)
    dl_estimates = dl_est.estimate(popularity or 0, country=country)
    breakdown["download_estimates"] = dl_estimates

    sr = SearchResult.upsert_today(
        keyword=kw,
        popularity_score=popularity,
        difficulty_score=difficulty_score,
        difficulty_breakdown=breakdown,
        competitors_data=competitors,
        app_rank=app_rank,
        country=country,
    )

    return {
        "success": True,
        "result": {
            "keyword": kw.keyword,
            "keyword_id": kw.id,
            "result_id": sr.id,
            "popularity_score": popularity,
            "difficulty_score": difficulty_score,
            "difficulty_label": sr.difficulty_label,
            "country": country,
            "searched_at": sr.searched_at.isoformat(),
            "app_rank": app_rank,
        },
    }


@mcp.tool()
def bulk_refresh_keywords(app_id: int | None = None, country: str = "us") -> dict:
    """
    Re-run scoring for all keywords under an app.

    If app_id is omitted, refreshes only keywords with no associated app.
    To refresh across all apps, call once per app.
    """
    import time
    from .models import Keyword, SearchResult
    from .services import ITunesSearchService, DifficultyCalculator, PopularityEstimator, DownloadEstimator

    if app_id is not None:
        keywords = Keyword.objects.filter(app_id=app_id).select_related("app")
    else:
        keywords = Keyword.objects.filter(app__isnull=True)

    if not keywords.exists():
        return {"success": True, "results": [], "refreshed": 0}

    itunes = ITunesSearchService()
    diff_calc = DifficultyCalculator()
    pop_est = PopularityEstimator()
    dl_est = DownloadEstimator()

    results = []
    for i, kw in enumerate(keywords):
        if i > 0:
            time.sleep(2)

        competitors = itunes.search_apps(kw.keyword, country=country, limit=25)
        difficulty_score, breakdown = diff_calc.calculate(competitors, keyword=kw.keyword)

        app_rank = None
        if kw.app and kw.app.track_id:
            app_rank = itunes.find_app_rank(kw.keyword, kw.app.track_id, country=country)

        popularity = pop_est.estimate(competitors, kw.keyword)
        dl_estimates = dl_est.estimate(popularity or 0, country=country)
        breakdown["download_estimates"] = dl_estimates

        sr = SearchResult.upsert_today(
            keyword=kw,
            popularity_score=popularity,
            difficulty_score=difficulty_score,
            difficulty_breakdown=breakdown,
            competitors_data=competitors,
            app_rank=app_rank,
            country=country,
        )
        results.append({
            "keyword": kw.keyword,
            "keyword_id": kw.id,
            "result_id": sr.id,
            "popularity_score": popularity,
            "difficulty_score": difficulty_score,
            "country": country,
        })

    return {"success": True, "results": results, "refreshed": len(results)}
```

- [ ] **Step 4: Run search tool tests**

```bash
python manage.py test aso.tests.test_mcp_search_tools -v 2
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add aso/mcp_server.py aso/tests/test_mcp_search_tools.py
git commit -m "feat: add 4 MCP search tools with tests"
```

---

## Task 5: Management Tools

**Files:**
- Modify: `aso/mcp_server.py`
- Create: `aso/tests/test_mcp_management_tools.py`

- [ ] **Step 1: Write failing tests**

Create `aso/tests/test_mcp_management_tools.py`:
```python
from django.test import TestCase

from aso.models import App, Keyword, SearchResult
from aso.mcp_server import add_app, delete_keyword, delete_app


class AddAppToolTest(TestCase):
    def test_creates_app(self):
        result = add_app(name="My App", bundle_id="com.test.app")
        self.assertIn("id", result)
        self.assertEqual(result["name"], "My App")
        self.assertEqual(App.objects.count(), 1)

    def test_duplicate_track_id_returns_error(self):
        App.objects.create(name="Existing", track_id=999)
        result = add_app(name="Duplicate", track_id=999)
        self.assertIn("error", result)
        self.assertEqual(App.objects.count(), 1)

    def test_no_track_id_is_fine(self):
        result = add_app(name="No Track ID")
        self.assertIn("id", result)


class DeleteKeywordToolTest(TestCase):
    def setUp(self):
        self.kw = Keyword.objects.create(keyword="yoga")
        SearchResult.objects.create(keyword=self.kw, difficulty_score=30, country="us")

    def test_deletes_keyword_and_results(self):
        result = delete_keyword(keyword_id=self.kw.id)
        self.assertTrue(result["success"])
        self.assertIn("yoga", result["deleted"])
        self.assertEqual(Keyword.objects.count(), 0)
        self.assertEqual(SearchResult.objects.count(), 0)

    def test_not_found(self):
        result = delete_keyword(keyword_id=99999)
        self.assertIn("error", result)


class DeleteAppToolTest(TestCase):
    def setUp(self):
        self.app = App.objects.create(name="My App")
        self.kw = Keyword.objects.create(keyword="yoga", app=self.app)

    def test_deletes_app_preserves_keywords(self):
        result = delete_app(app_id=self.app.id)
        self.assertTrue(result["success"])
        self.assertIn("My App", result["deleted"])
        self.assertEqual(App.objects.count(), 0)
        # Keyword is preserved, app set to null
        self.kw.refresh_from_db()
        self.assertIsNone(self.kw.app)

    def test_not_found(self):
        result = delete_app(app_id=99999)
        self.assertIn("error", result)
```

- [ ] **Step 2: Run to confirm failures**

```bash
python manage.py test aso.tests.test_mcp_management_tools -v 2
```

Expected: ImportError for `add_app`, `delete_keyword`, `delete_app`.

- [ ] **Step 3: Implement management tools in aso/mcp_server.py**

Append to `aso/mcp_server.py`:

```python
# ---------------------------------------------------------------------------
# Management Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def add_app(
    name: str,
    bundle_id: str = "",
    track_id: int | None = None,
    store_url: str = "",
    icon_url: str = "",
    seller_name: str = "",
) -> dict:
    """Add a new app for keyword tracking. track_id must be unique if provided."""
    from .models import App

    if track_id is not None and App.objects.filter(track_id=track_id).exists():
        return {"error": "An app with this track_id already exists"}

    app = App.objects.create(
        name=name,
        bundle_id=bundle_id,
        track_id=track_id,
        store_url=store_url,
        icon_url=icon_url,
        seller_name=seller_name,
    )
    return {"id": app.id, "name": app.name}


@mcp.tool()
def delete_keyword(keyword_id: int) -> dict:
    """Delete a keyword and all its search results."""
    from .models import Keyword

    try:
        kw = Keyword.objects.get(id=keyword_id)
    except Keyword.DoesNotExist:
        return {"error": f"Keyword {keyword_id} not found"}

    name = kw.keyword
    kw.delete()
    return {"success": True, "deleted": name}


@mcp.tool()
def delete_app(app_id: int) -> dict:
    """Delete an app. Keywords linked to this app are preserved (their app field is set to null)."""
    from .models import App

    try:
        app = App.objects.get(id=app_id)
    except App.DoesNotExist:
        return {"error": f"App {app_id} not found"}

    name = app.name
    app.delete()
    return {"success": True, "deleted": name}
```

- [ ] **Step 4: Run management tool tests**

```bash
python manage.py test aso.tests.test_mcp_management_tools -v 2
```

Expected: All tests PASS.

- [ ] **Step 5: Run the full test suite**

```bash
python manage.py test aso.tests -v 2
```

Expected: All tests across all three test modules PASS.

- [ ] **Step 6: Commit**

```bash
git add aso/mcp_server.py aso/tests/test_mcp_management_tools.py
git commit -m "feat: add 3 MCP management tools with tests"
```

---

## Task 6: End-to-End Smoke Test

**Files:** None (verification only)

- [ ] **Step 1: Build and start Docker**

```bash
docker compose build --no-cache && docker compose up -d
```

Expected: Container starts, logs show `RespectASO is ready!`

- [ ] **Step 2: Verify /mcp/ responds**

```bash
curl -s http://localhost/mcp/ | head -20
```

Expected: JSON response from FastMCP (server manifest or `{"jsonrpc":"2.0",...}`). Not a 404.

- [ ] **Step 3: Call the list_apps tool via MCP protocol**

```bash
curl -s -X POST http://localhost/mcp/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_apps","arguments":{}}}'
```

Expected: JSON response with `result` containing an empty array `[]` (no apps yet).

- [ ] **Step 4: Verify Django app still works**

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost/
```

Expected: `200` — Django dashboard still serves correctly alongside MCP.

- [ ] **Step 5: Commit (no code changes — this is verification only)**

If any issues were found and fixed, commit those fixes now.

---

## Task 7: Documentation

**Files:**
- Create: `docs/mcp.md`

- [ ] **Step 1: Create docs/mcp.md**

```markdown
# RespectASO MCP Server

The RespectASO MCP server exposes ASO keyword research as tools for LLM API calls
(Claude, OpenAI, etc.). Both projects run locally in Docker and communicate over
a shared Docker network.

---

## Prerequisites

RespectASO must be running before starting your consuming project:

```bash
cd respectaso/
docker compose up -d
```

This creates the `respectaso_net` Docker network. Your consuming project's containers
can then join it.

---

## Connecting Your Project

Add to your project's `docker-compose.yml`:

```yaml
networks:
  respectaso_net:
    external: true

services:
  your-service:
    networks:
      - respectaso_net
    environment:
      - RESPECTASO_MCP_URL=http://respectaso-web:8080/mcp/
```

**Endpoint URLs:**
- From another Docker container: `http://respectaso-web:8080/mcp/`
- From your host machine: `http://localhost/mcp/`
  (port 80 on the host maps to 8080 inside the container)

---

## Integration Examples

### Claude SDK (Python)

Requires `anthropic>=0.40` with MCP tool use support.

```python
import anthropic

client = anthropic.Anthropic()

response = client.beta.messages.create(
    model="claude-opus-4-6",
    max_tokens=1024,
    mcp_servers=[
        {
            "type": "url",
            "url": "http://localhost/mcp/",
            "name": "respectaso",
        }
    ],
    messages=[
        {
            "role": "user",
            "content": "Search for 'fitness tracker' in the US and tell me if it's worth targeting.",
        }
    ],
    betas=["mcp-client-2025-04-04"],
)
print(response.content)
```

### OpenAI SDK (Python)

Requires `openai>=1.x` with MCP support.

```python
from openai import OpenAI

client = OpenAI()

response = client.chat.completions.create(
    model="gpt-4o",
    tools=[
        {
            "type": "mcp",
            "server_url": "http://localhost/mcp/",
        }
    ],
    messages=[
        {
            "role": "user",
            "content": "Search for 'fitness tracker' in the US and tell me if it's worth targeting.",
        }
    ],
)
print(response.choices[0].message.content)
```

> **Note:** Pin SDK versions. Both the Anthropic and OpenAI SDKs have iterated
> rapidly on MCP support. Verify these examples against your installed SDK version.

---

## Tool Reference

### `list_apps`

List all tracked iOS apps.

**Inputs:** none

**Returns:**
```json
[
  {
    "id": 1,
    "name": "My App",
    "bundle_id": "com.example.myapp",
    "track_id": 123456789,
    "store_url": "https://apps.apple.com/...",
    "icon_url": "https://is1-ssl.mzstatic.com/...",
    "seller_name": "My Company"
  }
]
```

---

### `list_keywords`

List tracked keywords with their latest score per country.

**Inputs:**
| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `app_id` | integer | No | Filter by app. Omit to return all keywords. |

**Returns:**
```json
[
  {
    "id": 1,
    "keyword": "fitness tracker",
    "app_id": 1,
    "app_name": "My App",
    "results": [
      {
        "country": "us",
        "popularity_score": 65,
        "difficulty_score": 72,
        "difficulty_label": "Hard",
        "searched_at": "2026-03-25T10:00:00+00:00"
      }
    ]
  }
]
```

---

### `get_keyword_scores`

Get the latest scores for a specific keyword.

**Inputs:**
| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `keyword_id` | integer | Yes | |
| `country` | string | No | 2-letter code (e.g. `"us"`). Defaults to most recent. |

**Returns:**
```json
{
  "keyword": "fitness tracker",
  "country": "us",
  "popularity_score": 65,
  "difficulty_score": 72,
  "difficulty_label": "Hard",
  "targeting_advice": {
    "label": "Worth Competing",
    "description": "High demand but tough competition. Consider long-tail variants."
  },
  "competitors_data": [...],
  "app_rank": 14,
  "searched_at": "2026-03-25T10:00:00+00:00"
}
```

---

### `get_keyword_trend`

Get historical trend data for a keyword.

**Inputs:**
| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `keyword_id` | integer | Yes | |
| `country` | string | No | Filter to one country |

**Returns:** Array of data points ordered by date:
```json
[
  { "date": "2026-03-01", "popularity": 60, "difficulty": 68, "rank": 18, "country": "us" },
  { "date": "2026-03-08", "popularity": 63, "difficulty": 70, "rank": 15, "country": "us" }
]
```

---

### `get_search_history`

Get paginated search history (latest result per keyword+country).

**Inputs:**
| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `app_id` | integer | No | Filter by app |
| `country` | string | No | Filter by country code |
| `page` | integer | No | Default: 1. 25 results per page. |

**Returns:**
```json
{
  "results": [
    {
      "keyword": "fitness tracker",
      "app_name": "My App",
      "country": "us",
      "popularity_score": 65,
      "difficulty_score": 72,
      "difficulty_label": "Hard",
      "app_rank": 14,
      "searched_at": "2026-03-25T10:00:00+00:00"
    }
  ],
  "page": 1,
  "total_pages": 3,
  "total_count": 74
}
```

---

### `search_keywords`

Search 1–20 keywords across 1–5 countries. **Triggers iTunes API calls.**

**Inputs:**
| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `keywords` | string | Yes | Comma-separated, max 20. e.g. `"fitness,yoga,running"` |
| `countries` | array | No | Array of 2-letter codes, max 5. Default: `["us"]` |
| `app_id` | integer | No | Link results to an app for rank tracking |

**Returns:** Results grouped by country plus an opportunity ranking for multi-country searches.

**Notes:**
- Rate-limited: 2s between each iTunes call
- Worst case: 20 keywords × 5 countries ≈ 3 minutes
- Keywords already searched today are skipped (listed in `skipped` field)
- SQLite is single-writer — do not issue concurrent `search_keywords` calls

---

### `opportunity_search`

Search a single keyword across all 30 App Store countries.

**Inputs:**
| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `keyword` | string | Yes | |
| `app_id` | integer | No | For rank tracking |

**Returns:** Countries ranked by opportunity score (popularity × (100 - difficulty) / 100).

**Notes:** ~60 seconds runtime (30 iTunes calls at 2s each).

---

### `refresh_keyword`

Re-run scoring for a single keyword+country.

**Inputs:**
| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `keyword_id` | integer | Yes | |
| `country` | string | No | Default: `"us"` |

---

### `bulk_refresh_keywords`

Re-run scoring for all keywords under an app (or all unassigned keywords).

**Inputs:**
| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `app_id` | integer | No | If omitted, refreshes only keywords with no app |
| `country` | string | No | Default: `"us"` |

To refresh all keywords across all apps, call once per app.

---

### `add_app`

Add a new app for keyword tracking.

**Inputs:**
| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `name` | string | Yes | |
| `bundle_id` | string | No | e.g. `"com.example.myapp"` |
| `track_id` | integer | No | iTunes numeric ID. Must be unique. |
| `store_url` | string | No | App Store URL |
| `icon_url` | string | No | Icon image URL |
| `seller_name` | string | No | Developer name |

---

### `delete_keyword`

Delete a keyword and all its search history.

**Inputs:**
| Parameter | Type | Required |
|-----------|------|----------|
| `keyword_id` | integer | Yes |

---

### `delete_app`

Delete an app. Keywords linked to the app are preserved (their app field is set to null).

**Inputs:**
| Parameter | Type | Required |
|-----------|------|----------|
| `app_id` | integer | Yes |

---

## Error Reference

| Error message | Meaning |
|---------------|---------|
| `"Keyword {id} not found"` | No keyword with that ID exists |
| `"App {id} not found"` | No app with that ID exists |
| `"An app with this track_id already exists"` | `track_id` must be unique across all apps |
| `"No keywords provided"` | `keywords` string was empty or only whitespace |
| `"No results found for keyword {id}"` | Keyword exists but has no search results yet |
| `"iTunes API unavailable: {message}"` | iTunes Search API call failed |

---

## Limitations

- **Rate limiting:** 2s between iTunes API calls (same as the web UI). Do not issue concurrent search/refresh tool calls.
- **Single writer:** SQLite does not support concurrent writes. Concurrent calls to search or refresh tools will serialize or conflict. Keep one active search at a time.
- **No authentication:** The MCP endpoint is unauthenticated. Expose only on a trusted local Docker network.
- **Long-running tools:** `search_keywords` and `opportunity_search` can take 1–3 minutes. Ensure your LLM client is configured with a sufficient timeout.
```

- [ ] **Step 2: Commit docs**

```bash
git add docs/mcp.md
git commit -m "docs: add MCP server integration guide"
```

---

## Final Verification

- [ ] Run the full test suite one last time

```bash
python manage.py test aso.tests -v 2
```

Expected: All tests PASS, 0 failures.

- [ ] Rebuild Docker and verify all three endpoints work

```bash
docker compose build --no-cache && docker compose up -d
# Django UI
curl -s -o /dev/null -w "%{http_code}" http://localhost/
# MCP manifest
curl -s http://localhost/mcp/
# MCP tool call
curl -s -X POST http://localhost/mcp/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Expected: `200`, MCP JSON, tool list with all 12 tools.
