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
      - RESPECTASO_MCP_URL=http://respectaso-web:8080/mcp
```

**Endpoint URLs:**
- From another Docker container: `http://respectaso-web:8080/mcp`
- From your host machine: `http://localhost/mcp`
  (port 80 on the host maps to 8080 inside the container)

> **Note:** Both `/mcp` and `/mcp/` are handled by the server. For best compatibility
> with HTTP clients that do not follow redirects on POST, use `/mcp` (no trailing slash).

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
            "url": "http://localhost/mcp",
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
            "server_url": "http://localhost/mcp",
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

**Notes:**
- ~60 seconds runtime (30 iTunes calls at 2s each).
- Results are **not persisted** to the database. Use `search_keywords` if you want results saved to history.

---

### `refresh_keyword`

Re-run scoring for a single keyword+country.

**Inputs:**
| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `keyword_id` | integer | Yes | |
| `country` | string | No | Default: `"us"` |

**Returns:**
```json
{
  "success": true,
  "result": {
    "keyword": "fitness tracker",
    "keyword_id": 1,
    "result_id": 42,
    "popularity_score": 65,
    "difficulty_score": 72,
    "difficulty_label": "Hard",
    "country": "us",
    "searched_at": "2026-03-25T10:00:00+00:00",
    "app_rank": 14
  }
}
```

---

### `bulk_refresh_keywords`

Re-run scoring for all keywords under an app (or all unassigned keywords).

**Inputs:**
| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `app_id` | integer | No | If omitted, refreshes only keywords with no app |
| `country` | string | No | Default: `"us"` |

To refresh all keywords across all apps, call once per app.

**Returns:**
```json
{
  "success": true,
  "results": [
    {
      "keyword": "fitness tracker",
      "keyword_id": 1,
      "result_id": 42,
      "popularity_score": 65,
      "difficulty_score": 72,
      "country": "us"
    }
  ],
  "refreshed": 1
}
```

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
