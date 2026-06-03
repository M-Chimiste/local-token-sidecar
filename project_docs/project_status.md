# Token Oracle — Project Status

> Memory Bank · `project_status.md`
> Update this file at the end of every coding session that changes Token
> Oracle code, config, tests, deployment behavior, or docs.

---

## Current Recommendation

**Phases 0–3 are complete on hardware: all three faces render live `/metrics`
data with calm motion, flicker-free. Next is Phase 4 (IMU interaction); Phase 5
polish items are catalogued below.**

Reason: The render path is proven — an LVGL `lv_canvas` per face is drawn by a
single per-frame C++ lambda (LVGL 9 layer API) from pure geometry tables in
`oracle_faces.h`, driven by a ~10fps interval that only re-renders the active
face. Night Sky (5 constellations + rotating/twinkling starfield), Pantheon
(tribute arcs + orbs), and Ephemeris (60-tick rim + moon + six readings + Roman
date) all bind real per-node data. Two hardware bugs were fixed this phase: a
"Cache error" crash (RGB bounce ISR vs flash cache → SPIRAM flags) and a global
red/blue swap (display `color_order` BGR→RGB; gold had been rendering as cyan).

Phase 3 deferrals (now Phase 4/5): the per-god colors currently override the
verdigris "alive" rule (no single live cue); the moon phase is an approximate
offset-disc, not a true terminator; sigils are absent; the Night Sky ascendant
drill-in (models as stars) and the Pantheon faint orbit rings are not built.

Deployment state:

- `com.athena.token-oracle-api` is loaded via
  `~/Library/LaunchAgents/com.athena.token-oracle-api.plist`.
- `GET http://localhost:8090/health` returns `{"status":"ok"}`.
- `GET http://Nyx.local:8090/metrics` returns the stable Oracle payload with
  `ok:true`.
- Phase 1 firmware lives in `token-oracle/firmware/` and defaults to polling
  `http://Nyx.local:8090/metrics`.
- Local `token-oracle/firmware/secrets.yaml` was generated from the `.env`
  Wi-Fi values and remains ignored.
- The board is reachable as `token-oracle.local` on the LAN; during bring-up it
  resolved to `192.168.50.123`.

---

## Phase Status

| Phase | Name | Status | Notes |
|---|---|---|---|
| 0 | Data plumbing | Deployed on nyx | aiohttp `/metrics` API, on-the-fly rollups, launchd support, targeted tests green |
| 1 | Board bring-up | Validated | USB/OTA, Wi-Fi, touch, `/metrics`, stable display; flicker chain fully resolved |
| 2 | Face framework | Validated on hardware | Palette tokens, Cinzel/Cormorant fonts, 3 navigable faces + dot pager, JSON→globals→refresh; fonts/paging/pager confirmed on glass; starfield deferred to P3 |
| 3 | Three faces | Validated on hardware | Canvas render path; Night Sky/Pantheon/Ephemeris bind live per-node data + calm motion; colors corrected (RGB) |
| 4 | Interaction | Touch done; IMU pending | Touch paging works (P2); remaining: QMI8658 motion-wake/auto-dim, tilt-to-scrub, Night Sky ascendant drill-in |
| 5 | Polish | Not started | Real-panel color tuning, night auto-dim, midnight reckoning, true moon terminator, sigils, verdigris live-cue, Pantheon ghost orbits |

---

## Implemented In Phase 0

- `api/token_oracle_api.py`: separate read-only aiohttp service with `/health`
  and `/metrics`.
- `pg_common.py`: shared probe filtering, JSON response, UTC ISO formatting,
  timezone validation, and simple env-file DSN lookup.
- `config_loader.py`: `OracleConfig` with defaults:
  `0.0.0.0:8090`, `America/New_York`, `ascendant_window_seconds=120`,
  `dsn_env=TOKEN_SIDECAR_QUERY_DSN`, and stable
  nodes `nyx`, `mnemosyne`, `athena`, `metis`.
- `setup_launchd.py --service oracle`: label `com.athena.token-oracle-api`,
  env-file sourcing, preflight checks, and logs under `~/.token_sidecar/`.
- `/metrics` behavior:
  - Exact implementation-plan §4 key shape.
  - Local-day aggregation from UTC timestamps.
  - Probe rows excluded.
  - `ascendant: null`, empty `models`, and all `nodes[].live=false` when idle.
  - Runtime DB/query failures return HTTP 200 with `ok:false` and parseable
    zero/default fields.
  - History fields are computed on the fly; no `daily_totals` view yet.

Verification from local development:

```bash
uv run python -m py_compile pg_common.py config_loader.py dashboard.py setup_launchd.py api/token_oracle_api.py
uv run python -m pytest tests/ -v
# 157 passed, 2 skipped
```

Skipped tests were expected: live launchd respawn and optional live Postgres
integration.

---

## Implemented In Phase 1

- `token-oracle/firmware/token-oracle.yaml`: minimal ESPHome bring-up firmware
  for the Waveshare ESP32-S3-Touch-LCD-2.8C.
- Hardware baseline:
  - ESP32-S3 ESP-IDF target with 16 MB flash and octal PSRAM at 80 MHz.
  - I2C on `GPIO15/GPIO07`, PCA9554 at `0x20`, SPI LCD on `GPIO02/GPIO01`.
  - ST7701S 480x480 panel using the official Waveshare 2.8C init table,
    flat RGB data pin order, GT911 touch with interrupt `GPIO16` and reset via
    PCA9554, and PWM backlight on `GPIO06`.
  - Current display A/B uses a local ESPHome `st7701s` external component copied
    from ESPHome 2026.5.2 and patched to avoid the stock continuous
    `esp_lcd_rgb_panel_restart()` loop and to use two RGB framebuffers.
- Data proof:
  - Substituted endpoint defaults:
    `http://${metrics_host}:${metrics_port}${metrics_path}` →
    `http://Nyx.local:8090/metrics`.
  - Polling uses ESPHome `http_request` with `capture_response: true`, parses
    JSON, stores Phase 1-only globals, and renders a disposable centered LVGL
    label for `today.total`.
  - Touch taps update a small diagnostic line.
- Firmware docs:
  - `token-oracle/firmware/README.md` documents local secrets, config/compile,
    USB flash, OTA, LAN endpoint checks, and hardware acceptance.
  - `token-oracle/firmware/secrets.example.yaml` documents required secret
    keys without committing real credentials.
  - `token-oracle/firmware/.gitignore` excludes `secrets.yaml`, ESPHome build
    state, and firmware binaries.

Verification from local development:

```bash
curl -s http://Nyx.local:8090/health
curl -s http://Nyx.local:8090/metrics
uvx --from esphome esphome config token-oracle/firmware/token-oracle.yaml
uvx --from esphome esphome compile token-oracle/firmware/token-oracle.yaml
```

Results: `/health` returned `{"status":"ok"}`, `/metrics` returned `ok:true`
with parseable zero/default same-day totals, ESPHome config was valid, and
ESPHome compile succeeded with ESPHome 2026.5.2.

Hardware validation:

- First USB flash succeeded on the Waveshare ESP32-S3-Touch-LCD-2.8C.
- OTA updates succeeded over `token-oracle.local`.
- Logs confirmed ESP32-S3 rev0.2, Wi-Fi/API connection, and repeated
  `metrics parsed=true http=200 total=0`.
- GT911 touch logs coordinates when tapped.
- Backlight and display are active; the throwaway Token Oracle label is visible.
- The display required vendor ST7701S init bytes, flat vendor RGB data order,
  and a lowered pixel clock compared with the vendor demo. The latest build
  uses a black LVGL background, panel inversion off, shorter diagnostics, skips
  unchanged label redraws to reduce poll-time flicker, keeps LVGL
  `byte_order: little_endian`, uses full-frame LVGL refresh with a 100% buffer,
  and is currently A/B testing an Argus-inspired local ST7701S driver at
  `18MHz` pclk after the readable `12MHz` and `16MHz` builds still showed
  stationary text shimmer.

---

## Open Risks / Watch Items

- Display timing/color remains the main Phase 1 watch item. The panel is alive
  and readable, but stationary glyph shimmer remains under investigation. The
  current A/B is intentionally Argus-inspired: local ST7701S component, no
  stock ESPHome panel restart loop, two RGB framebuffers, `18MHz` pclk, 100%
  LVGL buffer, and full refresh. If shimmer persists, the likely next step is a
  fuller Argus-style ESP-IDF/manual LVGL port or deeper ESPHome display-driver
  customization that uses the RGB framebuffers directly.
- Backlight dimming is exposed as an ESPHome light and the backlight turns on,
  but API dimming has not been explicitly exercised.
- `today.total` polling is validated at `0`; a nonzero live-change acceptance
  check still needs a metrics value change.
- Validate real data shape: unknown `node_id` values append after configured
  gods; check whether that is acceptable before firmware layout is locked.
- Phase 0 intentionally omits ill-omen and today-vs-yesterday fields because
  they are not in the §4 `/metrics` wire contract.

---

## Session Log

### 2026-06-02 — Phase 3 The Three Faces

- Render path chosen + proven: an LVGL `lv_canvas` per face, drawn by a single
  per-frame C++ lambda using the LVGL 9 layer API (`lv_canvas_init_layer` →
  `lv_draw_rect/line/arc` → `lv_canvas_finish_layer`). Pure geometry/data lives in
  new `oracle_faces.h` (LVGL-free so it always compiles); drawing stays in the
  YAML lambdas where LVGL is in scope. A ~10fps `interval` re-renders only the
  active face (`current_face` dispatch). `token_dash_helpers.h` gained
  `oracle_roman()` + `oracle_ratio()`.
- Night Sky validated the path first (the gate): rotating ~96-star field
  (~280s/turn) + twinkle + anchor pulse; **five** constellations (Nyx, Mnemosyne,
  Athena, Metis, Theseus), grand total at center. Per user: 5 constellations
  (Nyx included) with a distinct color each — this overrides the verdigris rule
  for now. Confirmed flicker-free under continuous animation on glass.
- `/metrics` parse extended to all fields → globals: per-node totals (matched by
  name, unknown nodes ignored), ascendant→god code, today prompt/completion/
  requests, zenith, span, trend, high_water. Constellation/face labels bind to them.
- Pantheon: faint shared starfield + per-machine tribute arcs (sweep ∝ today's
  tokens) + orbs + center total; orb labels positioned at arc tips on poll.
  Dropped the full-circle faint orbit rings — 5 large per-frame AA arcs tanked
  the frame rate and made the starfield choppy (only on this face).
- Ephemeris: engraved 60-tick gold rim, moon glyph (approximate offset-disc
  phase), Roman date, day total, trend line, six readings (INVOCATIONS, ZENITH,
  BALANCE, REIGNING, DAWN-DUSK, HIGH-WATER). REIGNING = live ascendant else the
  day's most-tribute god. Per user: all Ephemeris text + ticks gold.
- **Hardware bug 1 — Cache error crash.** Adding the canvas surfaced a latent
  fault: the RGB panel's bounce-buffer refill ISR faulting while the flash cache
  is disabled during a flash op (`lcd_rgb_panel_fill_bounce_buffer` ← gdma ISR ←
  `spi_flash_op_block_func`). Fixed with `CONFIG_SPIRAM_FETCH_INSTRUCTIONS` +
  `CONFIG_SPIRAM_RODATA` (Argus sets these; we'd omitted them) so code/rodata run
  from PSRAM through cache-disable windows. Would have eventually bitten P1/P2.
- **Hardware bug 2 — red/blue swap.** Whole UI rendered blue: gold `#C9A24B`
  showed as `#4BA2C9`. Display `color_order` was `BGR`; the working Argus stack
  uses RGB element order. Flipped to `color_order: RGB` — colors correct. (This
  also explained the earlier "Mnemosyne is yellow, the rest blue-ish" report.)
- Removed the bring-up diagnostic status line from the Night Sky.
- Verification: `esphome config`/`compile` green throughout (Flash ~16%, RAM
  ~19%); OTA flashes confirmed via `app:153`; each face accepted on glass.
- OTA note: OTA failed/rolled back while the device was crash-looping on the
  cache error; USB flash was the reliable fallback. OTA worked again once stable.

### 2026-06-02 — Phase 2 Face Framework

- Built the Phase 2 face framework on the stable Phase 1 base (all hardware/
  display/flicker work untouched). Plan approved from `oracle_final.html` +
  design.md; user chose gfonts auto-download and keeping the live total on the
  Night Sky face.
- `token-oracle/firmware/token_dash_helpers.h` (new): pure-C++ `oracle_fmt_compact()`
  pulled in via `esphome: includes:`; replaced the inline number-format lambda.
- Palette: added the locked "Mythos" colors as `substitutions` (`c_ink`,
  `c_gold`, `c_gold_bright`, `c_gold_dim`, `c_verdigris`, `c_verdigris_pale`,
  `c_text_dim`); referenced everywhere instead of inline hex.
- Fonts: new `font:` block — `gfonts://Cinzel` (28, 14) and `gfonts://Cormorant@600`
  (48), glyph-subset (Cinzel caps+digits+punct, Cormorant compact-number set).
  Built-in montserrat kept as `default_font` + diagnostic line. Build now needs
  network for gfonts.
- LVGL: `style_definitions` (style_title/caption/numeral, + reserved
  style_ascendant in verdigris); replaced the single bringup page with three
  faces (`night_sky`, `pantheon`, `ephemeris`); a 3-dot pager on `top_layer`
  persists across faces. Night Sky shows live total (Cormorant) + "TRIBUTE
  GATHERED" + diagnostic status; the other two show their Cinzel titles.
- Navigation: `touchscreen on_touch` edge-zones — left ~28% → `lvgl.page.previous`,
  else `lvgl.page.next` (both wrap, OVER_RIGHT/OVER_LEFT 250ms). Each page's
  `on_load` sets `current_face` and runs `set_pager` to light the active dot via
  `lv_obj_set_style_bg_color`.
- `text_letter_space` for the engraved Cinzel tracking compiled fine on ESPHome
  2026.5.2 (the noted risk did not materialize).
- Verification: `esphome config` valid; `esphome compile` succeeded (Flash 15.8%
  / 1.28MB, RAM 16% — subsetting kept fonts cheap). README + project_status
  updated.
- OTA gotcha: the first OTA flash reported success but the device kept running
  `version phase1` (compiled 18:20:58) — the new image silently rolled back
  (first boot not marked valid → fell back to the previous OTA slot). A USB
  flash (`--device /dev/cu.usbmodem...`, which rewrites otadata) booted phase2
  cleanly. **Always confirm the running build via the `app:153` log line after
  flashing, not just "OTA successful."**
- Hardware acceptance (user-confirmed on glass): Cinzel titles + Cormorant total
  render, edge-tap paging cycles all three faces, and the dot pager tracks the
  active face (set_pager runtime path is good). Phase 2 DoD met.

### 2026-06-02 — Phase 1 Redraw Flicker (double buffer restored)

- 14MHz fixed the static/contention case; remaining flicker correlated with
  redraw events: touch (updates touch_label) and changing token totals (poll).
  With a single framebuffer every repaint writes into the live, actively-scanned
  buffer -> a tear on each content change. Restored `config.num_fbs = 2`: LVGL
  full_refresh renders a full frame, draw_bitmap fills the back FB, driver swaps
  at VSYNC -> tear-free redraws. Cannot reintroduce static shimmer (full_refresh
  only flushes on invalidation, so static content triggers no swap). The earlier
  continuous shimmer with two FBs was bus/backlight contention (2kHz backlight,
  Wi-Fi modem-sleep, tiny bounce, non-IRAM ISR), since fixed — not the buffer.
- Fallback if static shimmer somehow returns with two FBs: revert to num_fbs=1
  and set lvgl `full_refresh: false` so redraws repaint only the dirty text
  region (shrinks, not eliminates, the flicker).

### 2026-06-02 — Phase 1 Poll Flicker (LCD bandwidth margin)

- `power_save_mode: none` removed the ~3-5s periodic flicker, leaving only the
  20s `/metrics` poll burst. Bounce buffer (*40) + IRAM-safe ISR reduced but
  didn't fully absorb it at 18MHz. Lowered `pclk_frequency` 18MHz→14MHz (~65Hz
  →~50Hz) to cut baseline LCD PSRAM-bandwidth demand ~22% so the poll's Wi-Fi
  burst can't outrun the refill. LCDs don't flicker at lower refresh (pixels
  hold state). Compiled + flashed OTA; pending inspection.
- Next targeted lever if a poll blip survives: the firmware resolves
  `Nyx.local` via mDNS every poll (a TX+RX multicast burst on top of the GET).
  Switching `metrics_host` to Nyx's static LAN IP skips per-poll mDNS. Needs the
  user to confirm Nyx's reserved IP.

### 2026-06-02 — Phase 1 Periodic Flicker (Wi-Fi modem sleep)

- After the bounce-buffer/IRAM changes the residual flicker settled into a
  periodic ~3-5s cadence (not the 20s poll). That cadence is the Wi-Fi DTIM/
  beacon wake under default light modem-sleep: each radio wake is a bus/CPU
  burst that underruns one LCD frame. Set `wifi: power_save_mode: none` to keep
  the radio steady (Oracle is mains-powered, so idle draw is irrelevant).
  Compiled + flashed OTA; pending user inspection.
- If a periodic flicker still survives, next levers: lower pclk to 16MHz (~57Hz,
  less PSRAM bandwidth demand), or investigate the ESPHome API keepalive.

### 2026-06-02 — Phase 1 Poll-Time Flicker (bus contention)

- After the single-FB + 20kHz fixes, continuous shimmer was gone but a single
  one-frame flicker remained, correlated with the 20s `/metrics` poll. Ruled out
  an LVGL redraw: `refresh_oracle_labels` is guarded to run only when
  connected/http/total change, and `total` stays 0, so steady-state polls do not
  repaint. The flicker is transient memory-bus contention — the Wi-Fi DMA burst
  during the HTTP GET briefly starves the single framebuffer's bounce-buffer
  refill and underruns one frame, then re-settles.
- Fixes (compiled + flashed OTA): raised `bounce_buffer_size_px` from
  `width*10` to `width*40` (~38KB internal RAM) for headroom to ride out the
  burst, and added `CONFIG_LCD_RGB_ISR_IRAM_SAFE=y` +
  `CONFIG_GDMA_CTRL_FUNC_IN_IRAM=y` so the refill ISR/GDMA control stay in IRAM
  and aren't delayed by the network spike.
- Pending user inspection across poll cycles.

### 2026-06-02 — Phase 1 Flicker Root-Cause vs Argus

- Compared Codex's ESPHome display stack against the proven-stable Argus
  native ESP-IDF firmware (`project_docs/argus/`). Codex had copied Argus's
  surface knobs (18MHz pclk, bounce buffer, `num_fbs=2`, no-op `loop()`) but
  not the load-bearing difference: **Argus renders LVGL directly into the two
  RGB framebuffers (zero-copy); the ESPHome component is copy-flush** (lvgl owns
  its own buffer, memcpy into the panel via `esp_lcd_panel_draw_bitmap`).
- Identified `num_fbs=2` as unsound for the copy-flush path: every full-frame
  `draw_bitmap` flips the scanned framebuffer behind LVGL's back, so the two
  framebuffers drift apart and the panel alternates between them — the
  stationary-glyph shimmer. Reverted to `config.num_fbs = 1` (single PSRAM
  framebuffer + bounce buffer = Espressif's recommended anti-shimmer mode for a
  copy-flush driver, and the coherent single-buffer model ESPHome's lvgl
  manages). The stock component's earlier shimmer came from its continuous
  `esp_lcd_rgb_panel_restart()` loop (already removed), not from single-FB; the
  single-FB-without-restart-loop case had never actually been tested.
- Second, independent flicker contributor: backlight PWM was at `2000Hz`
  (Argus uses `5000Hz`). Raised to `20000Hz`, above the flicker-perception
  range, in `token-oracle.yaml`.
- `esphome config` valid; full `esphome compile` re-run after the changes.
- Hardware re-flash + visual inspection still pending. If shimmer persists with
  single-FB + 20kHz backlight, the remaining gap from Argus is true zero-copy
  direct-into-framebuffer rendering, which ESPHome's copy-flush lvgl can't do
  without patching the `lvgl` component (or switching to ESPHome's maintained
  `mipi_rgb`/direct-render RGB path).

### 2026-06-02 — Phase 1 Firmware Scaffold

- Added the Phase 1 ESPHome firmware scaffold under `token-oracle/firmware/`.
- Kept firmware tooling isolated from the Python sidecar; root `pyproject.toml`
  was not modified.
- Added local-only ESPHome secrets handling and generated ignored
  `token-oracle/firmware/secrets.yaml` from `.env` Wi-Fi values.
- Verified `Nyx.local:8090` from a non-ESP client: `/health` returned ok and
  `/metrics` returned `ok:true`.
- Verified firmware statically with ESPHome 2026.5.2:
  `uvx --from esphome esphome config token-oracle/firmware/token-oracle.yaml`
  and
  `uvx --from esphome esphome compile token-oracle/firmware/token-oracle.yaml`
  both passed.
- Hardware validation remains pending: first USB flash, panel/touch/backlight
  acceptance, `today.total` live update on-device, and OTA update.

### 2026-06-02 — Phase 1 Hardware Bring-Up

- Flashed the Waveshare ESP32-S3-Touch-LCD-2.8C over USB, then validated OTA
  updates over `token-oracle.local`.
- Corrected the actual board target to 16 MB flash with octal PSRAM; the first
  draft's 8 MB assumption was wrong for this hardware.
- Fixed local ignored ESPHome secrets generated from `.env` by stripping
  embedded quote characters before flashing.
- Verified LAN behavior from the board: ESPHome API connects, `/metrics` polls
  return HTTP 200, and the displayed `today.total` value parses as `0`.
- Confirmed GT911 touch by logging tap coordinates over the ESPHome API.
- Brought the ST7701S panel from black screen to visible output by using the
  official Waveshare 2.8C init sequence, flat vendor RGB data order, and a
  lowered `12MHz` pixel clock.
- Tested panel color inversion after user feedback showed readable but cyan/ugly
  output; it inverted the intended dark UI to white, so the latest build leaves
  panel inversion off and uses a black LVGL background instead.
- Added conditional label redraws so unchanged `/metrics` polls do not refresh
  the throwaway labels every 20 seconds.
- Flashed an LVGL `byte_order: little_endian` A/B build to test whether RGB565
  buffer endianness is responsible for colored glyph fringing; user reported
  the display works but text pixels still appear to shimmer in place.
- Flashed a `16MHz` pclk A/B build over OTA to raise refresh above the
  conservative `12MHz` test while keeping byte order, init bytes, data pin
  order, redraw behavior, and UI unchanged. ESPHome config passed, OTA
  succeeded, the device reconnected at `192.168.50.123`, and logs showed
  repeated `metrics parsed=true http=200 total=0`. Visual result is pending
  user inspection.
- After the Argus reference firmware was added under `project_docs/argus/`,
  compared its stable display stack with ESPHome. The important differences
  were manual LVGL 8.3 flush, two RGB framebuffers, a 10-line bounce buffer,
  `18MHz` pclk, and no repeated RGB panel restart from the render loop.
- Added a local ESPHome `st7701s` external component under
  `token-oracle/firmware/components/st7701s/` as a focused A/B. The local copy
  keeps the ESPHome API but changes `num_fbs` from `1` to `2` and makes
  `ST7701S::loop()` a no-op instead of calling
  `esp_lcd_rgb_panel_restart()` continuously.
- Updated the bring-up YAML to load the local component, set `pclk_frequency:
  18MHz`, use `buffer_size: 100%`, and enable `full_refresh: true`. ESPHome
  config and compile passed, OTA succeeded, the device reconnected at
  `192.168.50.123`, and logs showed repeated
  `metrics parsed=true http=200 total=0`. Visual result is pending user
  inspection.

### 2026-06-02 — Phase 0 Nyx Deployment

- Deployed `api/token_oracle_api.py` on `Nyx.local` as launchd service
  `com.athena.token-oracle-api`.
- Preserved local nyx runtime config, including `oracle.enabled: true`,
  `0.0.0.0:8090`, and node order with `theseus`.
- Fixed the live Postgres node-totals query to order by the aggregate alias
  instead of the raw `total_tokens` column.
- Verified `GET /health` and `GET /metrics` from `localhost` and `Nyx.local`;
  `/metrics` returns `ok:true`.
- Targeted verification passed:
  `uv run python -m pytest tests/test_token_oracle_api.py tests/test_launchd.py -q`
  → **36 passed**.

### 2026-06-02 — Phase 0 Local Implementation

- Implemented Token Oracle data plumbing in the current repo.
- Chose aiohttp + psycopg_pool instead of FastAPI/uvicorn to avoid new runtime
  dependencies and mirror `dashboard.py`.
- Added launchd support for `com.athena.token-oracle-api`.
- Added config, API, shared-helper, dashboard, and launchd tests.
- Removed the stale Oracle progress-limit config/API field; `/metrics` is
  token-only aggregate data with no quota value.
- Updated README, AGENTS/CLAUDE, implementation plan, requirements, and root
  project status.
- Full test suite passed: **157 passed, 2 skipped**.

Next session should continue real-panel display tuning, then exercise backlight
dimming and a nonzero `today.total` update before moving into Phase 2 visuals.
