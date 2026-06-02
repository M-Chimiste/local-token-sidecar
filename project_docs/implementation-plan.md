# Token Oracle — Implementation Plan

> Memory Bank · `implementation-plan.md`
> Read alongside `requirements.md` and `design.md`.

---

## 1. Architecture

```
 sidecar(s) ──writes──▶  Postgres on nyx  ──read──▶  metrics API (nyx)  ──HTTP/LAN──▶  ESP32 (ESPHome + LVGL)
 (per node)              token_usage (+         FastAPI, read-only,        polls /metrics       renders 3 faces
                          daily_totals view)     aggregates server-side      every 15–30s
```

- The ESP32 is **not** on the tailnet → it polls `nyx`'s **LAN IP** over plain HTTP.
- All aggregation happens on `nyx`. The device parses one small JSON document.

## 2. Technology Choices

| Layer | Choice | Why |
|---|---|---|
| Firmware | **ESPHome + LVGL** | Verified hardware block exists for the 2.8C; OTA; minimal boilerplate |
| Heavy visuals | LVGL **canvas** *or* display-layer lambda (TBD) | Starfield/constellations/spiral need custom draw; validate path early |
| Standard widgets | LVGL `arc`, `label`, `bar`, `meter` | Rings, counts, gauges map directly |
| IMU | **custom/external component** for QMI8658 | Not a first-class ESPHome component (confirm) |
| Metrics API | **FastAPI + psycopg (v3)** on nyx | Co-located with Postgres; trivial read service |
| Process mgmt | launchd plist on nyx | Same pattern the sidecar uses |

## 3. Hardware Reference (verified ESPHome block for ESP32-S3-Touch-LCD-2.8C)

```
i2c:        sda=GPIO15  scl=GPIO07
pca9554:    address 0x20  (LCD CS=#2, LCD RESET=#0, TOUCH RESET=#1)
spi (lcd):  clk=GPIO02  mosi=GPIO01  interface=spi3
backlight:  GPIO06 (LEDC, monochromatic light)
display st7701s:
  spi_mode MODE1, color_order BGR, 480x480
  de=GPIO40  hsync=GPIO38  vsync=GPIO39  pclk=GPIO41
  data_pins: red=[46,3,8,18,17] green=[14,13,12,11,10,9] blue=[5,45,48,47,21]
  (full ST7701S init_sequence is in the prior ESPHome draft / community config)
touchscreen gt911:
  reset_pin = pca9554 #1   interrupt_pin = GPIO16
esp32: board esp32-s3-devkitc-1, flash 8MB, framework esp-idf, psram octal@80MHz
```

> Only `substitutions` (nyx LAN IP, budget, accent) and Wi-Fi `secrets` should
> need editing. Flash once over USB, then OTA.

## 4. API Contract

`GET /metrics` → JSON (aggregated server-side; keys kept short for the MCU):

```jsonc
{
  "ok": true,
  "ts": "2026-06-02T14:31:00Z",
  "budget": 2000000,
  "today":   { "total": 1295000, "prompt": 812000, "completion": 483000, "requests": 1284 },
  "rate_per_min": 412,
  "ascendant": "mnemosyne",                 // node with activity in the recency window
  "zenith":  { "hour": 14, "tokens": 248000 },
  "span":    { "first": "06:42", "last": "14:31" },   // dawn → dusk (local)
  "models":  [ { "name": "minimax-m2.7", "total": 372000 }, ... ],  // for ascendant drill-in
  "nodes":   [ { "name": "mnemosyne", "total": 690000, "live": true },
               { "name": "athena", "total": 318000 },
               { "name": "metis",  "total": 212000 },
               { "name": "nyx",    "total": 75000 } ],
  "hourly":  [ /* 24 ints, local-day token totals */ ],
  "trend":   { "mean": 1098000, "delta_pct": 18, "phase": 0.72 },   // moon
  "high_water": 1840000,
  "streak_days": 14
}
```

- `GET /health` → `{"status":"ok"}`.
- Node column assumed `node_id` (configurable). Timestamps UTC; "today" computed in a
  configured local TZ.

## 5. Postgres Rollups

- Same-day fields: aggregate from `token_usage` filtered to the local day (UTC bounds).
- History fields (`trend`, `high_water`, `streak_days`): back with a rollup, e.g.

```sql
CREATE MATERIALIZED VIEW daily_totals AS
SELECT (timestamp AT TIME ZONE 'America/New_York')::date AS day,
       SUM(total_tokens) AS total,
       COUNT(*)          AS requests
FROM token_usage
GROUP BY 1;
-- refresh on a schedule (or compute on the fly while volumes are small)
```

- `mean` = avg of last 7 `daily_totals.total`; `phase` = clamp(today/mean scaled).
- `high_water` = max(daily_totals.total); `streak_days` = consecutive days with total > 0.

## 6. Phased Build

### Phase 0 — Data plumbing
- Stand up the metrics API on nyx; implement same-day aggregation + the rollups in §5.
- Validate `GET /metrics` shape from a LAN box (`curl | jq`).
- **DoD:** stable JSON matching §4 over the LAN.

### Phase 1 — Board bring-up
- Flash the verified ESPHome hardware block; confirm panel + touch + Wi-Fi + OTA.
- Add `http_request` polling; render a single throwaway label with `today.total` to
  prove the end-to-end data path.
- **DoD:** live token total on glass, updating on the poll interval.

### Phase 2 — Face framework
- Establish theme tokens (palette, Cinzel/Cormorant fonts), shared helpers, page/nav
  system (evaluate `tileview` vs tap-zones), and the JSON→globals→refresh pattern.
- **DoD:** three blank themed pages, navigable, fonts rendering.

### Phase 3 — The three faces
- **Validate the render path with The Night Sky FIRST** (highest risk: starfield,
  twinkle, drift, constellation draw). Decide canvas vs widgets vs display-lambda here.
- Then **The Pantheon** (arcs + orb + sigils + rays) and **The Ephemeris** (moon glyph
  + readings). Bind each element to the API fields per `design.md`.
- **DoD:** all three faces render real data with their core animations.

### Phase 4 — Interaction
- Touch paging + long-press menu/brightness.
- QMI8658 custom component → **motion-wake / auto-dim**; then **tilt-to-scrub**.
- **DoD:** picking the device up wakes/brightens it; tilt scrubs within a face.

### Phase 5 — Polish
- Tune gold on the real panel; smooth animation timing for the refresh rate; night
  auto-dim; the midnight **reckoning** (RTC-triggered held Ephemeris).
- **DoD:** sits on the desk for a full day behaving correctly through idle/active/midnight.

## 7. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| ST7701S RGB timing / tearing | Start from the verified 2.8C block; if tearing, add explicit RGB porch timing + lower pclk |
| Custom-draw perf (starfield/constellations) | Prototype Night Sky in Phase 3 before committing; cap star count; throttle twinkle/drift; consider static field + few animated accents |
| `json::parse_json` API drift across ESPHome versions | Pin ESPHome version; `JsonObjectConst` vs `JsonObject` is the usual delta |
| QMI8658 not first-class in ESPHome | Build a small external component (I²C reads) or fall back to touch-only interaction for v1 |
| GT911 edge non-linearity | Large/zone touch targets; avoid precise hits on thin arcs |
| Schema/column drift (`node_id`) | Make column configurable in the API; degrade gracefully |

## 8. Suggested Repo Layout

```
token-oracle/
  firmware/
    token-oracle.yaml          # ESPHome config (hw block + faces)
    token_dash_helpers.h       # C++ helpers (compact number fmt, etc.)
    components/qmi8658/         # custom IMU component (Phase 4)
  api/
    token_dashboard_api.py      # FastAPI metrics service for nyx
    deploy/launchd.plist
  memory-bank/
    requirements.md
    implementation-plan.md
    design.md
    oracle_final.html           # locked visual mockup (source of truth)
  CLAUDE.md
```

## 9. Notes for Claude Code

- The **mockup `oracle_final.html` is the visual source of truth** — match layout,
  proportions, palette, and motion from it. It is pure SVG/JS and maps cleanly to LVGL.
- Build order is deliberately **data → bring-up → hardest face first**. Don't build all
  three faces before proving the Night Sky render path.
- Keep aggregation on nyx; never make the MCU do heavy JSON or math.
