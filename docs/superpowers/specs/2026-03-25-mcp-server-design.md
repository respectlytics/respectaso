# MCP Server for RespectASO — Design Spec

**Date:** 2026-03-25
**Status:** Approved

---

## Overview

Add a Model Context Protocol (MCP) server directly into the existing RespectASO Django application. This allows other locally-hosted projects to provide RespectASO as a tool to LLM API calls (OpenAI, Claude, etc.), enabling AI agents to perform ASO keyword research, access scores and history, and manage apps/keywords programmatically.

Both RespectASO and the consuming project run in Docker locally and communicate over a shared Docker network.

---

## Architecture

The MCP layer is built into the `aso` Django app. No new containers or process supervisors are needed.

### New files

| File | Purpose |
|------|---------|
| `aso/mcp_server.py` | MCP server instance + all 12 tool definitions, calls Django ORM and `services.py` directly |
| `aso/mcp_views.py` | Thin Django view that bridges the async MCP SDK into Gunicorn's sync worker via `asgiref.sync.async_to_sync` |

### Modified files

| File | Change |
|------|--------|
| `core/urls.py` | Mount `POST /mcp/` → `aso.mcp_views.mcp_handler` |
| `requirements.txt` | Add `mcp[cli]` |
| `docker-compose.yml` | Add named attachable network `respectaso_net`; keep existing port 80 mapping |

### Dependency

```
mcp[cli]>=1.0
```

The `mcp` SDK is the official Python MCP implementation. It handles JSON-RPC framing, SSE transport, tool schema generation, and protocol negotiation.

---

## Transport

- **Protocol:** MCP over Streamable HTTP (HTTP + SSE)
- **Endpoint:** `POST /mcp/`
- **Discovery:** `GET /mcp/` returns the MCP server manifest (handled by SDK)
- **Port:** Same as the main app — `8080` inside the container, `80` on the host

---

## MCP Tools

### Read tools (no iTunes API calls)

| Tool | Inputs | Returns |
|------|--------|---------|
| `list_apps` | *(none)* | Array of `{id, name, bundle_id, track_id, store_url, icon_url, seller_name}` |
| `list_keywords` | `app_id?` | Array of keywords with their latest score per country |
| `get_keyword_scores` | `keyword_id`, `country?` | Latest `{popularity_score, difficulty_score, difficulty_label, targeting_advice, competitors_data, app_rank, searched_at}` |
| `get_keyword_trend` | `keyword_id`, `country?` | Array of `{date, popularity, difficulty, rank, country}` data points |
| `get_search_history` | `app_id?`, `country?`, `page?` | Paginated array of latest results per keyword+country |

### Search tools (trigger iTunes API, rate-limited)

| Tool | Inputs | Returns |
|------|--------|---------|
| `search_keywords` | `keywords` (string, comma-sep, max 20), `countries` (array, max 5), `app_id?` | Results grouped by country with full scores, competitors, download estimates |
| `opportunity_search` | `keyword` (string), `app_id?` | 30-country opportunity ranking sorted by opportunity score |
| `refresh_keyword` | `keyword_id`, `country?` | Updated scores for that keyword+country |
| `bulk_refresh_keywords` | `app_id?`, `country?` | Updated scores for all keywords under an app (or unassigned keywords) |

### Management tools

| Tool | Inputs | Returns |
|------|--------|---------|
| `add_app` | `name`, `bundle_id?`, `track_id?`, `store_url?`, `icon_url?`, `seller_name?` | Created `{id, name}` |
| `delete_keyword` | `keyword_id` | `{success: true, deleted: "<keyword>"}` |
| `delete_app` | `app_id` | `{success: true, deleted: "<app name>"}` |

---

## Data Flow

```
LLM API call (tools/call JSON-RPC)
  → POST /mcp/ (Django view)
    → async_to_sync bridges Gunicorn sync worker → async MCP SDK
      → mcp_server.py tool handler
        → services.py / Django ORM (sync, direct — no HTTP hop)
          → iTunes API (search/refresh tools only)
            → JSON result → SSE stream → LLM caller
```

**Async bridging:** `asgiref.sync.async_to_sync` (already a Django dependency) wraps the MCP SDK's async handler for each request. Gunicorn runs sync workers — this shim is the standard Django pattern and requires no ASGI server switch.

**Long-running tools:** `search_keywords` (up to 20 keywords × 5 countries = 100 iTunes calls at 2s apart ≈ ~3 min) keeps the SSE connection alive. The LLM caller receives a single complete response when done. No timeout risk on the transport layer.

---

## Docker Networking

RespectASO's `docker-compose.yml` gets a named attachable network:

```yaml
networks:
  respectaso_net:
    name: respectaso_net
    driver: bridge
    attachable: true

services:
  web:
    networks:
      - respectaso_net
    # existing config unchanged
```

The consuming project's `docker-compose.yml` joins the same network:

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

The MCP endpoint is reachable at `http://respectaso-web:8080/mcp/` from any container on `respectaso_net`.

---

## Error Handling

All errors are returned as structured MCP error responses so the LLM can reason about them — not raw HTTP 500s.

| Scenario | Response |
|----------|----------|
| iTunes API timeout / failure | `{"error": "iTunes API unavailable: <message>"}` |
| Invalid keyword or app ID | `{"error": "Keyword 42 not found"}` |
| Validation failure (too many keywords, bad country code) | Descriptive error string |
| Malformed JSON-RPC / missing fields | HTTP 400 (handled by MCP SDK) |

Rate limiting (2s between iTunes calls) is handled inside the tool handlers, same as existing views.

---

## Authentication

No authentication for the initial implementation — consistent with the rest of the app (local-only, trusted Docker network). A simple `MCP_API_KEY` environment variable can be added as an opt-in check in `mcp_views.py` in a future iteration.

---

## Out of Scope

- ASGI server migration (Gunicorn sync workers are sufficient)
- MCP tool for CSV export (use the existing HTTP endpoint directly)
- Streaming partial results during long searches (full result returned on completion)
- Authentication / API keys (future opt-in)
