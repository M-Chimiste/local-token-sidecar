# Token Oracle Firmware

The Waveshare ESP32-S3-Touch-LCD-2.8C renders fleet token usage as three faces —
**Night Sky** (constellations), **Pantheon** (orbital tribute arcs), and
**Ephemeris** (daily reckoning) — navigated by edge-tap with a dot pager. Each
face is an LVGL `lv_canvas` drawn by a per-frame C++ lambda from the geometry in
`oracle_faces.h`, bound to live `/metrics` data via the JSON→globals→refresh
pattern. Phases 0–4 are done (faces + IMU interaction); Phase 5 (polish) remains.

Interaction: the QMI8658 IMU drives **motion-wake / auto-dim** (pick it up →
full brightness; idle ~90s → calm dim). Tapping any **constellation** on the
Night Sky **drills into that god's** today model breakdown; tap to return. At
local **midnight** the device holds the finished Ephemeris.

## Files

- `token-oracle.yaml` - ESPHome firmware (hardware block + the three faces + IMU).
- `oracle_faces.h` - pure (LVGL-free) data/math: background starfield, the five
  constellations + per-god colors, Pantheon rings, polar/centroid helpers, and the
  per-god model tables for the drill-in.
- `token_dash_helpers.h` - pure C++ helpers (compact number, Roman numerals,
  prompt:completion ratio), pulled in via `esphome: includes:`.
- `components/st7701s/` - local ESPHome display-driver override (double
  framebuffer + bounce buffer; the Argus-derived panel-stability fixes).
- `components/qmi8658/` - local ESPHome IMU component (accel poll → `moving` /
  `face_down` / `accel_magnitude`), ported from Argus `argus_input.c`. Pure I2C
  at 0x6B; no INT/reset pins.
- `secrets.example.yaml` - copy to `secrets.yaml` and fill in local values.
- `.gitignore` - keeps ESPHome build output and secrets local.

## Display gotchas (hard-won — see project_status.md session logs)

- **`color_order: RGB`** is required (not BGR) or red/blue swap and gold renders
  as cyan. Matches the Argus stack on this panel.
- **`CONFIG_SPIRAM_FETCH_INSTRUCTIONS` + `CONFIG_SPIRAM_RODATA`** (in
  `sdkconfig_options`) prevent a "Cache error" crash where the RGB bounce-buffer
  ISR faults while the flash cache is disabled during a flash op.
- After flashing, **verify the running build via the `app:153` log line** — OTA
  can silently roll back; USB (`--device /dev/cu.usbmodem...`) is the fallback.
- Animation is a ~10fps `interval` that re-renders only the active face. Keep
  per-frame work light (no large full-circle `lv_draw_arc`); lower fps first if
  flicker appears.

## Fonts (network needed at build time)

Cinzel and Cormorant are pulled from Google Fonts via `gfonts://` in the
`font:` block and glyph-subset to keep flash small, so **`esphome compile`
needs network access**. If you must build offline, download the TTFs into a
local `fonts/` dir and switch the `file:` keys to those paths.

## Navigation

Tap the **left ~28%** of the round face to go back, anywhere else to advance;
both wrap (Night Sky → Pantheon → Ephemeris → Night Sky). The lit dot at the
bottom tracks the current face. Large zones are deliberate — they avoid GT911
edge non-linearity.

The firmware is intentionally isolated from the Python sidecar project. Install
and run ESPHome separately; do not add ESPHome to the root Python dependencies.

## Configure

```bash
cd token-oracle/firmware
cp secrets.example.yaml secrets.yaml
esphome noise-key
```

Paste the generated key into `api_encryption_key`, then set Wi-Fi and OTA
password values in `secrets.yaml`.

The default metrics endpoint is:

```text
http://Nyx.local:8090/metrics
```

If mDNS is unreliable from the ESP32, edit the `metrics_host` substitution in
`token-oracle.yaml` to `nyx`'s numeric LAN IP. Keep `metrics_port` and
`metrics_path` unchanged unless the API deployment moves.

## Pre-Flash Checks

From a non-ESP client on the same LAN:

```bash
curl -s http://Nyx.local:8090/health
curl -s http://Nyx.local:8090/metrics | jq '.ok, .today.total'
```

Then validate the firmware config:

```bash
esphome config token-oracle.yaml
esphome compile token-oracle.yaml
```

## First Flash

Connect the board over USB and run:

```bash
esphome run token-oracle.yaml
```

Watch the serial logs for:

- Wi-Fi connection and IP address.
- PSRAM initialization.
- PCA9554, GT911, and ST7701S setup.
- HTTP 200 responses from `/metrics`.
- Touch coordinate logs when the panel is tapped.

## Acceptance

Phases 0–3 are complete when:

- All three faces render live `/metrics` data: Night Sky's five constellations
  with per-god counts, Pantheon's tribute arcs, Ephemeris's six readings + date.
- Colors are correct (gold reads gold, not cyan) and the starfield drifts/
  twinkles smoothly with no flicker or tearing on any face.
- Edge-tap paging cycles the three faces (and wraps); the dot pager tracks the
  active face; the grand total updates after each poll interval.

If the panel is blank or tears, tune the ST7701S init sequence, porch timings,
pixel clock, or color order in the display block.
