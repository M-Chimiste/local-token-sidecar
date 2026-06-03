// token_dash_helpers.h — shared C++ helpers for the Token Oracle firmware.
//
// Included into the ESPHome build via the `esphome: includes:` key. Keep this
// header free of ESPHome/LVGL types so it stays trivially testable and reusable
// across faces. Phase 2 only needs compact number formatting; future faces can
// add small pure helpers here (ratios, Roman numerals, etc.).
#pragma once

#include <cstdint>
#include <cstdio>
#include <string>

// Format a raw token count as a compact, glyph-frugal string for the Cormorant
// numeral readouts: 1_300_000 -> "1.30M", 412_000 -> "412K", 2_100_000_000 ->
// "2.10B", small values stay exact. Output charset is limited to digits plus
// '.', 'K', 'M', 'B' so the subset Cormorant font only has to ship those glyphs.
inline std::string oracle_fmt_compact(uint64_t value) {
  char out[32];
  if (value >= 1000000000ULL) {
    snprintf(out, sizeof(out), "%.2fB", value / 1000000000.0);
  } else if (value >= 1000000ULL) {
    snprintf(out, sizeof(out), "%.2fM", value / 1000000.0);
  } else if (value >= 1000ULL) {
    snprintf(out, sizeof(out), "%.0fK", value / 1000.0);
  } else {
    snprintf(out, sizeof(out), "%llu", (unsigned long long) value);
  }
  return std::string(out);
}

// Roman numerals for the Ephemeris date (day 1-31 -> "I".."XXXI"). Cinzel ships
// the uppercase letters already, so this needs no extra glyphs.
inline std::string oracle_roman(int n) {
  if (n <= 0) return "";
  static const int vals[] = {1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1};
  static const char *syms[] = {"M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"};
  std::string out;
  for (int i = 0; i < 13; i++) {
    while (n >= vals[i]) { out += syms[i]; n -= vals[i]; }
  }
  return out;
}

// Prompt:completion balance as "1.68 : 1" for the Ephemeris BALANCE reading.
// Glyph charset stays within the Cormorant subset (digits, '.', ':', space).
inline std::string oracle_ratio(uint64_t prompt, uint64_t completion) {
  if (completion == 0) return prompt == 0 ? "0 : 0" : "1 : 0";
  char out[24];
  snprintf(out, sizeof(out), "%.2f : 1", (double) prompt / (double) completion);
  return std::string(out);
}
