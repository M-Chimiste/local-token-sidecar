# Token Oracle — Requirements

> Memory Bank · `requirements.md`
> Project codename: **Token Oracle** · Theme: **The Pantheon**
> Status: design locked, pre-implementation

---

## 1. Vision

A desk instrument that renders **local LLM token usage** as a living **pantheon**.
Each machine in the home ML fleet is a Greek deity; tokens are **tribute**; the
machine that is generating right now is **ascendant**. The device is styled as a
Greek astronomical instrument (astrolabe / orrery / ephemeris) and runs on a
round 480×480 touch display so it earns a place on the desk whether glanced at
or studied.

It is an **ambient display first, a dashboard second** — beautiful at rest,
informative on inspection.

## 2. Goals

- Show today's token activity at a glance, themed as a coherent "observatory."
- Three rotating faces (see §6) sharing one visual world.
- Pull from the existing `local-token-sidecar` data with no changes to the sidecar.
- Aggregate **server-side** so the microcontroller only parses a small JSON blob.
- Run entirely on the **local network** — read-only, no cloud, no auth surface beyond LAN trust.
- **Tokens only.** No cost / dollar analysis anywhere (explicit decision).

## 3. Non-Goals

- No cost or cloud-equivalent-spend metrics.
- No control plane (the device never *commands* the fleet; it only observes).
- No public/internet exposure of the metrics endpoint.
- No per-request log browsing on the device (it is a summary instrument).

## 4. Hardware

**Board:** Waveshare **ESP32-S3-Touch-LCD-2.8C** (round).

| Subsystem | Detail |
|---|---|
| MCU | ESP32-S3N16R8 — dual-core LX7, Wi-Fi + BT, **16 MB flash, 8 MB PSRAM** |
| Display | 2.8" round IPS, **480×480**, **ST7701S** driver, RGB interface |
| Touch | **GT911** capacitive, I²C, 5-point |
| IMU | **QMI8658** 6-axis (accel + gyro), I²C |
| RTC | on-board (real-time clock for time-of-day / midnight events) |
| IO expander | **PCA9554** (drives LCD CS/RESET + touch RESET) |
| Backlight | PWM (GPIO6 / LEDC) — supports dimming |
| Storage | TF card slot, battery management (unused for v1) |

A community-verified ESPHome hardware block exists for this exact board
(ST7701S init + GT911). The canonical pin map lives in `implementation-plan.md §Hardware`.

## 5. Data Source

**`local-token-sidecar`** (github.com/M-Chimiste/local-token-sidecar): an HTTP proxy
in front of LM Studio that records per-request token usage.

- Per-node cache in SQLite (`~/.token_sidecar/tokens.db`), flushed to a **central
  Postgres on `nyx`** (the Mac mini), which is the system of record.
- Table `token_usage`, columns observed from the sidecar's own SQL examples:
  `timestamp, model, prompt_tokens, completion_tokens, total_tokens, node_id, endpoint, status, event_id`.
- The sidecar's HTTP server exposes only `/health` and the proxy passthrough — **no
  dashboard/read endpoint**. We add one (see §8 and the implementation plan).

`nyx` already hosts the Postgres and is on the LAN + tailnet; it is the natural
home for the new read-only metrics API.

## 6. Functional Requirements

### 6.1 Faces (locked)

1. **The Night Sky** — the pantheon as a rotating star chart. Nyx is the pole star
   (keeper of the record, holds the grand total). Each machine is a constellation;
   star size/brightness encodes that machine's tribute. The **ascendant** (currently
   generating) constellation glows verdigris and animates. Optional drill-in
   ("The Ascendant") shows the reigning god's **models as individual stars**.
2. **The Pantheon** — orbital / armillary rings, one per machine. Each ring's arc is
   swept by that machine's tribute share; an orb rides the arc tip with the god's
   sigil, name, token count, and domain. Nyx sits at center as keeper with the total.
3. **The Ephemeris** — the day's reckoning as an engraved almanac. A **moon-phase
   glyph** encodes today vs. the rolling mean (waxing = above, waning = below).
   Six token readings around the dial: invocations, zenith (peak hour), balance
   (prompt:completion), reigning god, dawn→dusk (first/last activity), high-water
   (record day).

### 6.2 Data display

- Device polls the metrics API every ~15–30 s and re-renders.
- All counts are **tokens** (and token-derived: requests, ratios, hours).
- "Ascendant" = the node with a request within the last N seconds (recency window).
- Numbers are formatted compactly (e.g., `1.30M`, `248K`).

### 6.3 Interaction (proposed — to confirm during build)

- **Touch (GT911):** tap to advance face; tap left edge to go back; long-press for a
  face menu or brightness. Use large/zone targets (edge non-linearity caveat).
  Evaluate LVGL `tileview` for momentum-swipe paging.
- **IMU (QMI8658):**
  - **Motion-wake / auto-dim** (priority): still for a while → dim to a calm at-rest
    state; pick up / nudge → full brightness + jump to the live face.
  - **Tilt-to-scrub** (signature): tilt to walk hours/days within a face.
  - Knock / shake (optional).
- **RTC:** support an end-of-day "reckoning" — at local midnight, compose and hold
  the finished Ephemeris for the day just ended.

## 7. Metrics / Daily-Stats Requirements (token-only)

**Same-day (today's rows only):**
total tribute, invocations (request count), zenith (peak hour + volume),
dawn→dusk (first/last activity time), balance (prompt:completion ratio),
reigning node (most tribute), per-node shares, per-model shares, ill-omen rate
(non-200 statuses).

**History-dependent (needs daily rollups):**
today vs rolling mean (drives the moon), today vs yesterday, active-day streak,
high-water mark (record day). Requires a `daily_totals` rollup in Postgres.

## 8. Connectivity & Security

- ESP32 is **not** a tailnet node; it reaches `nyx` via **LAN IP** over plain HTTP.
- Metrics API is **read-only** and intended for a **trusted LAN** only; do not expose publicly.
- No secrets on the device beyond Wi-Fi credentials (ESPHome `secrets.yaml`).

## 9. Aesthetic Requirements (locked) — "Mythos"

- **Ink** ground (deep indigo-black), **antique gold** as the divine default,
  **verdigris bronze** reserved *exclusively* for "ascendant / alive."
- Type: **Cinzel** (engraved Roman caps for labels/titles) + **Cormorant** (numerals).
- Round-native, concentric/radial composition; generous negative space; engraved
  ticks/rings; subtle, tasteful motion (drift, twinkle, breathing, draw-in sweeps).
- One coherent world across all three faces (shared ground, palette, starfield).

## 10. Constraints & Assumptions

- `node_id` is the node column name (confirm against the live schema; configurable).
- Timestamps are UTC; "today" is computed in a configurable local timezone.
- Daily budget for any progress framing is configurable (default 2,000,000 tokens).
- ESPHome ≥ 2024.6 assumed (http_request `on_response` + `json::parse_json` shape).

## 11. Open Questions (carry into build)

- Exact interaction model (tap vs tileview-swipe; how much weight on IMU).
- QMI8658 ESPHome support — likely needs a custom/external component (see plan §Risks).
- Render path for star/constellation work (LVGL canvas vs many widgets vs display-layer lambda).
- Whether to keep a distinct dimmed "at-rest" face or simply dim the Ephemeris.
- Final gold values after seeing them on the actual ST7701S panel.
