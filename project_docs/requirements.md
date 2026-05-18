# Token Counter Sidecar — Requirements Document

**Project:** Token Counter Sidecar for LM Studio  
**Author:** Christian Merrill  
**Date:** 2026-05-17  
**Status:** Draft  

---

## 1. Overview

### Project Name
`token-sidecar`

### Type
Local token usage monitoring tool (no Docker, no containers)

### Core Functionality
A lightweight Python service that tracks token volumes across Athena and Metis Mac Studios by parsing LM Studio's OpenAI-compatible API responses. Stores aggregates in a local SQLite database for historical analysis.

### Motivation
- Fun to know token volumes — not about cost (electricity is trivial)
- Unified visibility across both Mac Studios
- Potential integration with Mnemosyne control plane

---

## 2. Architecture

### Approach: HTTP Proxy (Recommended)

A small Python proxy runs on port `1234` and:
1. Forwards requests to LM Studio (default `localhost:1234` → `localhost:8080`)
2. Intercepts responses, extracts token usage from OpenAI-compatible `/completions` and `/chat/completions` payloads
3. Logs usage to SQLite with timestamps

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│  Client     │ ──▶ │  token-sidecar   │ ──▶ │ LM Studio   │
│ (Athena/    │     │  :1234           │     │  :8080      │
│  Metis)     │ ◀── │                  │ ◀── │             │
└─────────────┘     └──────────────────┘     └─────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   SQLite    │
                    │ tokens.db   │
                    └─────────────┘
```

### Components

| Component | Technology | Purpose |
|-----------|------------|---------|
| HTTP Proxy | Python + `http.server` / `aiohttp` | Intercept and forward LLM API calls |
| Database | SQLite | Store token usage records |
| Scheduler | `launchd` (macOS) | Auto-start on boot, keep alive |

---

## 3. Functionality Specification

### 3.1 Proxy Server

- **Listen:** `localhost:1234`
- **Forward target:** `localhost:8080` (configurable)
- **Endpoints to intercept:**
  - `POST /v1/chat/completions`
  - `POST /v1/completions`
- **Behavior:**
  - Pass all request headers and body through unchanged
  - On response, extract `usage` object if present:
    ```json
    {
      "prompt_tokens": <int>,
      "completion_tokens": <int>,
      "total_tokens": <int>
    }
    ```
  - Log to SQLite: timestamp, model name (from request), prompt/completion/total tokens
- **Error handling:** If LM Studio is unavailable, return 502 Bad Gateway with a clear error message. Do not crash.
- **Logging:** Write proxy-level logs (request/response) to stdout for debugging.

### 3.2 Database Schema

**Table: `token_usage`**

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY | Auto-increment ID |
| `timestamp` | TEXT | ISO 8601 datetime (UTC) |
| `model` | TEXT | Model name from request body |
| `prompt_tokens` | INTEGER | Input tokens |
| `completion_tokens` | INTEGER | Output tokens |
| `total_tokens` | INTEGER | Total tokens for this call |
| `response_ms` | REAL | LM Studio response time in ms |

**Table: `daily_summaries`** (optional, computed)

| Column | Type | Description |
|--------|------|-------------|
| `date` | TEXT | Date (YYYY-MM-DD) |
| `model` | TEXT | Model name |
| `request_count` | INTEGER | Number of requests |
| `total_prompt_tokens` | INTEGER | Sum of prompt tokens |
| `total_completion_tokens` | INTEGER | Sum of completion tokens |
| `total_tokens` | INTEGER | Sum of total tokens |

### 3.3 Configuration

All configuration via `config.yaml`:

```yaml
proxy:
  listen_host: "localhost"
  listen_port: 1234
  upstream_url: "http://localhost:8080"

database:
  path: "~/.token_sidecar/tokens.db"

logging:
  level: "INFO"   # DEBUG, INFO, WARNING, ERROR

launchd:
  enabled: true   # generate ~/Library/LaunchAgents/com.athena.token-sidecar.plist
```

### 3.4 LaunchAgent Setup (macOS)

- Generate a `plist` at `~/Library/LaunchAgents/com.athena.token-sidecar.plist`
- Start automatically on login (`RunAtLoad: true`)
- Restart on crash (`KeepAlive: true`)
- Log output to `~/.token_sidecar/sidecar.log`

---

## 4. Non-Functional Requirements

### Performance
- Proxy overhead < 5ms per request (in-process response interception)
- SQLite writes are fast; no batching required at this scale
- Support for both Mac Studios concurrently (two independent sidecars)

### Portability
- Python 3.10+ with only stdlib + `aiohttp` / `httpx`
- No Docker, no containers, no external services
- Single-file main proxy (`sidecar.py`) < 150 lines

### Observability
- Structured stdout logging (JSON Lines format in production)
- Query script to print daily/hourly summaries from SQLite

---

## 5. File Structure

```
token_sidecar/
├── sidecar.py              # Main proxy + DB writer
├── config.yaml             # Configuration file
├── setup_launchd.py        # Generate and install LaunchAgent plist
├── queries/
│   └── summary.py          # CLI for daily/hourly summaries
├── tests/
│   ├── test_proxy.py       # Unit tests for proxy logic
│   └── test_db.py          # DB insertion / query tests
├── project_docs/
│   └── requirements.md     # This document
└── README.md               # Setup and usage instructions
```

---

## 6. Out of Scope

- Cost calculation (electricity/cost per token)
- Real-time WebSocket dashboard (nice-to-have, not this phase)
- Mnemosyne control plane integration (future work)
- Multi-user authentication on the proxy
- Tokenization accuracy (trust LM Studio's numbers)

---

## 7. Acceptance Criteria

1. **AC1:** Proxy starts on `localhost:1234` and forwards requests to LM Studio on port `8080`
2. **AC2:** Every `/v1/chat/completions` or `/v1/completions` response containing a `usage` object is written to SQLite
3. **AC3:** Database schema matches the specification above
4. **AC4:** LaunchAgent plist is generated and installs correctly; sidecar starts on login
5. **AC5:** Summary query script returns daily token totals by model from the CLI
6. **AC6:** No external dependencies beyond Python stdlib + one HTTP library (`aiohttp` or `httpx`)
7. **AC7:** Sidecar handles LM Studio downtime gracefully (502, no crash)

---

## 8. Future Work

- Push aggregates to Mnemosyne control plane for cross-Mac-Studio unified view
- Web-based dashboard with near-real-time token charts
- Per-model per-day alerts if token volume exceeds a threshold
- Export to CSV / JSON for external analysis