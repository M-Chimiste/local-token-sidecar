// oracle_faces.h — pure data + math for the Token Oracle faces.
//
// Deliberately LVGL-free (no lvgl.h here) so it always compiles regardless of
// include paths; the actual lv_draw_* calls live in the YAML interval lambda,
// which already has LVGL in scope. This header just supplies the geometry the
// mockup (project_docs/oracle_final.html) encodes: the background starfield, the
// three constellations, and small math helpers. Center is C=(240,240).
#pragma once

#include <cstdint>
#include <cmath>
#include <cstring>

namespace oracle {

constexpr float CX = 240.0f;
constexpr float CY = 240.0f;
constexpr int BG_STARS = 96;

// God codes used across faces and globals: matches node_id names.
enum God { GOD_NONE = 0, GOD_MNEMOSYNE = 1, GOD_ATHENA = 2, GOD_METIS = 3,
           GOD_NYX = 4, GOD_THESEUS = 5 };

inline uint32_t god_color(int g) {
  switch (g) {
    case GOD_NYX: return 0xF3ECCF;
    case GOD_MNEMOSYNE: return 0x46C2A6;
    case GOD_ATHENA: return 0xC9A24B;
    case GOD_METIS: return 0xC678DD;
    case GOD_THESEUS: return 0x6FB7E0;
  }
  return 0xF3DA94;
}

inline const char *god_name(int g) {
  switch (g) {
    case GOD_NYX: return "NYX";
    case GOD_MNEMOSYNE: return "MNEMOSYNE";
    case GOD_ATHENA: return "ATHENA";
    case GOD_METIS: return "METIS";
    case GOD_THESEUS: return "THESEUS";
  }
  return "NONE";
}

// Ascendant drill-in: the live god's models (parsed from /metrics models[]).
struct ModelStar { char name[24]; uint64_t total; };
inline ModelStar g_models[8];
inline int g_model_count = 0;

// ---- background starfield (rotates as one field; ~280s/turn) ----
struct BgStar {
  float ang_deg;   // base angle; rotation is added at draw time
  float rad;       // distance from center (sqrt distribution, max ~224)
  float size;      // radius in px (>=1 so it renders)
  float tw_phase;  // twinkle phase offset
  float tw_speed;  // twinkle angular speed
  bool warm;       // warm (#f3eccf) vs cool (#cfe0ff)
  bool twinkles;   // only ~30% twinkle
};

inline uint32_t xs32(uint32_t &s) { s ^= s << 13; s ^= s >> 17; s ^= s << 5; return s; }
inline float frand(uint32_t &s) { return (xs32(s) & 0xFFFFFF) / (float) 0x1000000; }

// Deterministic field (fixed seed -> identical every boot).
inline int bg_init(BgStar *out, int cap) {
  uint32_t s = 0xA11CE5u;
  int n = cap < BG_STARS ? cap : BG_STARS;
  for (int i = 0; i < n; i++) {
    out[i].ang_deg = frand(s) * 360.0f;
    out[i].rad = sqrtf(frand(s)) * 224.0f;
    out[i].size = 0.6f + frand(s) * 1.1f;
    out[i].warm = frand(s) >= 0.2f;
    out[i].twinkles = frand(s) < 0.3f;
    out[i].tw_phase = frand(s) * 6.2831853f;
    out[i].tw_speed = 1.2f + frand(s) * 2.0f;
  }
  return n;
}

// ---- constellations (absolute coords; do NOT rotate) ----
struct CStar { float x, y, mag; bool anchor; };
struct Edge { uint8_t a, b; };

// Mnemosyne — "the River" (upper-left), usually ascendant.
static const CStar MNE[] = {
  {118, 96, 0.5f, false}, {150, 120, 0.6f, false}, {128, 154, 0.5f, false},
  {170, 150, 0.7f, false}, {188, 188, 0.6f, false}, {214, 178, 1.0f, true}};
static const Edge MNE_E[] = {{0, 1}, {1, 2}, {2, 3}, {3, 4}, {4, 5}};

// Athena — "the Aegis" (right).
static const CStar ATH[] = {
  {356, 158, 0.7f, false}, {392, 194, 0.6f, false}, {374, 236, 1.0f, true},
  {330, 246, 0.6f, false}, {316, 204, 0.7f, false}};
static const Edge ATH_E[] = {{0, 1}, {1, 2}, {2, 3}, {3, 4}, {4, 0}};

// Metis — "the Spiral" (lower-left).
static const CStar MET[] = {
  {118, 316, 0.6f, false}, {150, 330, 0.7f, false}, {146, 366, 1.0f, true},
  {108, 368, 0.5f, false}, {92, 336, 0.6f, false}};
static const Edge MET_E[] = {{0, 1}, {1, 2}, {2, 3}, {3, 4}};

// Nyx — "the Keeper" (top-center).
static const CStar NYX[] = {
  {236, 72, 0.7f, false}, {268, 100, 0.6f, false}, {246, 128, 1.0f, true},
  {210, 104, 0.6f, false}};
static const Edge NYX_E[] = {{0, 1}, {1, 2}, {2, 3}, {3, 0}};

// Theseus — "the Voyager" (lower-right).
static const CStar THE[] = {
  {312, 308, 0.6f, false}, {346, 322, 0.7f, false}, {376, 352, 1.0f, true},
  {350, 388, 0.6f, false}, {312, 366, 0.7f, false}};
static const Edge THE_E[] = {{0, 1}, {1, 2}, {2, 3}, {3, 4}, {4, 0}};

struct Constellation {
  int god;
  uint32_t color;  // per-god hue (distinct colors; verdigris rule revisited at data-binding)
  const CStar *stars; int nstars;
  const Edge *edges; int nedges;
};

static const Constellation CONSTELLATIONS[] = {
  {GOD_NYX,       0xF3ECCF, NYX, 4, NYX_E, 4},  // warm white — the keeper
  {GOD_MNEMOSYNE, 0x46C2A6, MNE, 6, MNE_E, 5},  // verdigris/teal — the river
  {GOD_ATHENA,    0xC9A24B, ATH, 5, ATH_E, 5},  // gold — the aegis
  {GOD_METIS,     0xC678DD, MET, 5, MET_E, 4},  // amethyst — the spiral
  {GOD_THESEUS,   0x6FB7E0, THE, 5, THE_E, 5},  // sky blue — the voyager
};
constexpr int N_CONSTELLATIONS = 5;

// ---- Pantheon rings (one orbit per machine; colors match the constellations) ----
struct Ring { int god; uint32_t color; float radius; };
static const Ring RINGS[] = {
  {GOD_MNEMOSYNE, 0x46C2A6, 210.0f},
  {GOD_ATHENA,    0xC9A24B, 178.0f},
  {GOD_METIS,     0xC678DD, 146.0f},
  {GOD_THESEUS,   0x6FB7E0, 114.0f},
  {GOD_NYX,       0xF3ECCF, 82.0f},
};
constexpr int N_RINGS = 5;
constexpr int ARC_START_DEG = 270;    // tribute arcs sweep clockwise from top
constexpr float ARC_MAX_DEG = 300.0f; // max sweep at the leading god

// Centroid of a god's constellation (for tap hit-testing on the Night Sky).
// Returns false if that god has no constellation.
inline bool constellation_centroid(int god, float &cx, float &cy) {
  for (int c = 0; c < N_CONSTELLATIONS; c++) {
    if (CONSTELLATIONS[c].god != god) continue;
    const Constellation &cn = CONSTELLATIONS[c];
    float sx = 0, sy = 0;
    for (int i = 0; i < cn.nstars; i++) { sx += cn.stars[i].x; sy += cn.stars[i].y; }
    cx = sx / cn.nstars;
    cy = sy / cn.nstars;
    return true;
  }
  return false;
}

// polar -> cartesian, 0deg = up/north (matches the mockup's pt()).
inline void pt(float r, float deg, float &x, float &y) {
  float a = (deg - 90.0f) * 0.0174532925f;
  x = CX + r * cosf(a);
  y = CY + r * sinf(a);
}

}  // namespace oracle
