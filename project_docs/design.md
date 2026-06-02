# Token Oracle — Design Specification

> Memory Bank · `design.md`
> Visual source of truth: **`oracle_final.html`** (interactive mockup in this folder).
> This file is the written spec; the mockup is the canonical look, proportions, and motion.

---

## 1. Concept

The device is an **observatory** trained on your local ML fleet. Machines are the
**gods of a pantheon**; tokens are **tribute** offered to them; the machine generating
right now is **ascendant**. The instrument has three faces — a star chart, an orrery,
and an ephemeris — the classic instruments of an astronomer.

One architectural truth anchors the whole composition: **`nyx`** (the Mac mini running
the central Postgres) is the **keeper of the record**, so Nyx sits at the *center* of
every face and the working gods orbit/surround her.

## 2. The Pantheon (mapping)

| God | Machine | Role | Domain | Sigil / Constellation |
|---|---|---|---|---|
| **Nyx** (night) | Mac mini | the **Keeper** — holds the record (Postgres), the center/pole | record | crescent + star |
| **Mnemosyne** (memory) | GPU workstation (dual Blackwell) | the deep generator; usually **ascendant** | memory | three waves → "the River" |
| **Athena** (wisdom) | M3 Ultra Mac Studio | strategy/inference | wisdom | aegis/shield → "the Aegis" |
| **Metis** (counsel) | M3 Ultra Mac Studio | deep counsel | counsel | spiral → "the Spiral of Counsel" |

**Second tier:** the **models** a machine runs are the **lesser stars** within that
god's constellation (e.g., minimax, qwen, glm, gemma as Mnemosyne's stars). Surfaced in
the Night Sky "Ascendant" drill-in.

## 3. Visual System ("Mythos")

**Palette**

| Token | Hex | Use |
|---|---|---|
| ink-0 / ground | `#04050a` → `#0a0c1a` | background (radial vignette) |
| gold | `#c9a24b` | divine default — lines, labels |
| gold-bright | `#f3da94` | numerals, highlights, lit stars |
| gold-dim | `#6f5a2e` | secondary/dim text, faint ticks |
| **verdigris** | `#46c2a6` (pale `#a8ecdb`) | **RESERVED for ascendant / alive only** |
| text-dim | `#9a8f6b` | captions |

> The verdigris rule is the most important constraint: gold is the whole world;
> verdigris means *exactly one thing* — "this is happening right now." Never use it
> decoratively.

**Typography**
- **Cinzel** — engraved Roman caps for labels, titles, god names, hour marks (letter-spaced).
- **Cormorant** — all numerals and the large readouts (elegant, high-contrast).

**Composition**
- Round-native: concentric rings, radial placement, strong center, generous negative space.
- Engraved instrument bezel: fine tick rings, hairlines, sigils-as-engravings.
- One shared world: same ink ground, same drifting starfield behind every face.

**Motion principles** (tasteful, low frequency)
- Draw-in sweeps for arcs; count-up for hero numbers; slow starfield drift; gentle
  twinkle; the ascendant element **breathes** and **radiates**. Nothing flashy; the
  device should feel calm and alive, not busy. Tune frequencies for the panel refresh.

## 4. Faces

### 4.1 The Night Sky (constellations)
- **Background:** faint starfield (~90 stars), subtle Milky Way band, slow rotation
  (~280 s/turn), occasional shooting star. A few stars twinkle.
- **Center:** **Nyx** as the pole star (bright, sparkle, crescent beside it), the
  **grand total** counting up below ("TRIBUTE GATHERED"). The sky turns around her.
- **Constellations:** one per machine, drawn as connected stars in its own sector:
  Mnemosyne the River (upper), Athena the Aegis (right), Metis the Spiral (lower-left).
  Each labeled with name + token count. **Star size/brightness ∝ tribute.**
- **Ascendant:** the live machine's constellation is rendered in **verdigris**, its
  anchor star pulsing, lines brighter.
- **Drill-in ("The Ascendant"):** zoom into the reigning god's constellation where each
  star is a **model** she's channeling, sized by that model's tokens (verdigris theme).

### 4.2 The Pantheon (orbital rings)
- **Rings:** one concentric orbit per machine (outer = larger tribute). Faint full orbit
  + a **tribute arc** whose sweep ∝ tokens (scaled so the max ≈ 300°).
- **Orb + label:** an orb rides the arc tip; beside it the god's **sigil, name, token
  count, and domain**. Ascendant orb is verdigris, **breathes**, and emits slowly
  **rotating rays**.
- **Center:** **Nyx** keeper — sigil (twinkling), the **grand total** ("TRIBUTE
  GATHERED"), and a quiet note "NYX KEEPS THE RECORD · 75K".
- **Entrance:** arcs draw in, staggered.

### 4.3 The Ephemeris (daily reckoning — tokens only)
- **Title:** "EPHEMERIS" + Roman date (e.g., `II · JUNE · MMXXVI`); engraved 60-tick rim.
- **Moon (hero glyph):** phase encodes **today vs. rolling mean** — waxing gibbous when
  above, waning crescent when below. Center holds the day's **tribute total** + a line
  like "WAXING · +18% ABOVE THE MEAN" (verdigris when above).
- **Six readings around the dial** (all token-derived; star-marker + Cinzel label +
  Cormorant value):
  1. **INVOCATIONS** — request count
  2. **ZENITH** — peak hour + its volume (`14·00 — 248K`)
  3. **BALANCE** — prompt:completion ratio (`1.68 : 1`)
  4. **REIGNING** — the ascendant/most-tribute god (verdigris)
  5. **DAWN → DUSK** — first → last activity time
  6. **HIGH-WATER** — record day total
- **No cost / currency anywhere.**

## 5. Data → Visual Bindings

| Visual | API field |
|---|---|
| grand total (all faces) | `today.total` |
| constellation/orb size & arc sweep | `nodes[].total` |
| ascendant (verdigris) | `ascendant` / `nodes[].live` |
| Ascendant drill-in stars | `models[]` |
| moon phase + trend line | `trend.phase`, `trend.delta_pct` |
| zenith | `zenith.hour`, `zenith.tokens` |
| dawn → dusk | `span.first`, `span.last` |
| balance | `today.prompt` : `today.completion` |
| invocations | `today.requests` |
| high-water | `high_water` |

## 6. Interaction (proposed)

- **Default day:** at rest the device dims to a calm at-rest view; on motion/pickup it
  brightens and shows the live (ascendant-forward) face. At local midnight it holds the
  finished **Ephemeris** ("the reckoning") until next interaction.
- **Browse:** tap to advance, tap left edge to go back (or `tileview` swipe).
- **Signature gesture:** **tilt-to-scrub** through hours/days within a face.
- **Verdigris = live** carries through every interaction: the eye is always drawn to
  whatever is ascendant.

## 7. Assets & Notes

- **Sigils** are simple engraved line-glyphs (crescent+star, three waves, aegis, spiral)
  defined procedurally in the mockup — reproduce as LVGL canvas paths or small image
  assets.
- Fonts: Cinzel + Cormorant (subset the needed weights/glyphs for the MCU).
- The mockup uses placeholder/representative data (today ≈ 1.30M, budget 2.0M,
  Mnemosyne ascendant). Real values come from `/metrics`.
- Feasibility honesty: the Night Sky is the most custom-draw face — prototype it first
  (see `implementation-plan.md §6 Phase 3`). The Pantheon and Ephemeris map largely to
  stock LVGL arcs/labels/meter plus a moon glyph.
