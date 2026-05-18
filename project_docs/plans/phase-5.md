# Phase 5 — Summary Query CLI: Detailed Implementation Plan

**Project:** token-sidecar  
**Phase:** 5 of 7  
**Parent plan:** `../implementation_plan.md`  
**Requirements:** `../requirements.md`  
**Goal:** Allow easy inspection of collected data from the terminal.

---

## Context

The sidecar (Phases 2–3) writes token usage rows to SQLite. The database
schema supports daily and hourly aggregation natively via `get_daily_summary()`
and `get_hourly_summary()`. Phase 5 wraps these with a human-readable CLI.

**What is already available:**
- `db.py` → `get_daily_summary(date=None, model_name=None)` returns list of
  `(model_name, request_count, prompt_tokens, completion_tokens, total_tokens)`
- `db.py` → `get_hourly_summary(date)` returns dict of `{hour: (request_count,
  prompt_tokens, completion_tokens, total_tokens)}`
- Both functions use the already-opened connection pattern

**What this phase adds:**
- `queries/summary.py` — click-based CLI exposing all three views
- Pretty tabular output for interactive terminal sessions
- JSON output flag for scripting / piping into other tools
- `--model <name>` filter on daily view (already supported by db layer)
- Test suite with deterministic fixture data

---

## Step-by-Step Plan

### Step 1 — Design the CLI interface

**File:** `queries/summary.py`  
**Approach:** Use `click` for CLI framing (cleaner argparse, auto-help,
subcommands). Add it via `uv add click`.

Define three commands as click groups:

```
$ python -m queries.summary --help
Commands:
  daily     Daily summary — tokens per model, optionally filtered by date
  hourly    Hourly breakdown for a specific date
  by-model  All-time totals grouped by model name
```

Each command accepts `--format json|table` (default: `table` in tty,
`json` when stdout is not a tty).  
Default date is today (`datetime.date.today()`).

**Why click over argparse:** Subcommands map cleanly to the three query types;
auto-help and shell-completion are included; widely available via stdlib-equivalent
(`uv add click` → one dep, well-tested).

---

### Step 2 — Implement `daily` command

```python
@cli.command()
@click.option("--date", default=None, help="Date in YYYY-MM-DD format (default: today)")
@click.option("--model", "model_name", default=None, help="Filter to a specific model")
@click.option("--format", "output_format",
              type=click.Choice(["table", "json"]),
              default=None)   # None → auto-detect tty
def daily(date, model_name, output_format):
    """Daily token usage summary grouped by model."""
```

- Resolve `date`: if `None`, use `datetime.date.today()`. Parse from string if provided.
- Call `db.get_daily_summary(date=parsed_date, model_name=model_name)`
- If `output_format == "json"` or stdout is not a tty → print JSON
  ```json
  [{"date": "2026-05-17", "model": "minimax-m2.7",
    "requests": 12, "prompt_tokens": 3900,
    "completion_tokens": 847, "total_tokens": 4747}, ...]
  ```
- If `output_format == "table"` or stdout is a tty → print tabular text
  using `tabulate` (add via `uv add tabulate`)
  ```
  Date         Model           Requests   Prompt Tokens   Completion Tokens   Total Tokens
  ----------   ------------   --------   -------------   -----------------   -----------
  2026-05-17   minimax-m2.7          12          3,900               847          4,747
  ```

---

### Step 3 — Implement `hourly` command

```python
@cli.command()
@click.option("--date", required=True, help="Date in YYYY-MM-DD format")
@click.option("--format", "output_format",
              type=click.Choice(["table", "json"]),
              default=None)
def hourly(date, output_format):
    """Hourly breakdown of token usage for a specific date."""
```

- Parse and validate the `--date` argument (required).
- Call `db.get_hourly_summary(date)` → returns dict mapping hour (0–23) to
  `(request_count, prompt_tokens, completion_tokens, total_tokens)`
- JSON output: list of objects with `hour`, `requests`, `prompt_tokens`,
  `completion_tokens`, `total_tokens`
- Table output: rows for each hour, sorted by hour ascending; show `00–23` as `HH:00`
  ```
  Hour    Requests   Prompt Tokens   Completion Tokens   Total Tokens
  -----   --------   -------------   -----------------   -----------
  09:00          3             847               203        1,050
  10:00         11           3,053               644        3,697
  ```

---

### Step 4 — Implement `by-model` command

```python
@cli.command()
@click.option("--format", "output_format",
              type=click.Choice(["table", "json"]),
              default=None)
def by_model(output_format):
    """All-time totals grouped by model name."""
```

- Query all rows and aggregate in Python (no new db function needed;
  `get_daily_summary(model_name=None)` already returns per-model rows).
- If the DB is large, add a note that `--format json` is preferred.
- Table: sort descending by `total_tokens`
  ```
  Model           Requests   Prompt Tokens   Completion Tokens   Total Tokens
  ------------   --------   -------------   -----------------   -----------
  minimax-m2.7         142         48,230             11,847        60,077
  qwen3.6-27b-mlx       38         22,140              4,392        26,532
  ```

---

### Step 5 — Auto-detect output format

```python
def resolve_format(requested: str | None) -> str:
    if requested is not None:
        return requested
    # Default to table in tty, JSON otherwise (safer for piping/redirection)
    return "table" if sys.stdout.isatty() else "json"
```

This means `python -m queries.summary daily` in an interactive terminal shows
a table; piping to `jq` or redirecting to a file automatically gets JSON.

---

### Step 6 — Add dependencies

```bash
uv add click tabulate
```

- `click` — CLI framework (as planned above)
- `tabulate` — pretty table formatting (`uv add tabulate`)

Both are pure-Python, no C extensions. After adding, run `uv sync`.

---

### Step 7 — Write `tests/test_queries.py`

**Framework:** pytest with tmp_path + in-memory URI fixtures.

Test file structure:
```python
import sys, pathlib, subprocess
# Set up path to import from project root (same pattern as test_launchd.py)

from queries.summary import resolve_format

def test_resolve_format_prefers_explicit_over_tty_auto():
    assert resolve_format("json") == "json"

def test_resolve_format_defaults_to_table_in_tty(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert resolve_format(None) == "table"

def test_resolve_format_defaults_to_json_when_not_tty(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert resolve_format(None) == "json"
```

**CLI integration tests** — invoke the module as subprocess and check output:

```python
def test_daily_command_shows_table_for_fake_db(tmp_path, sample_db):
    result = subprocess.run(
        [sys.executable, "-m", "queries.summary", "daily",
         "--date", "2026-05-17"],
        capture_output=True, text=True,
        env={**os.environ, "TOKEN_SIDECAR_DB": str(sample_db)},
    )
    assert result.returncode == 0
    # Check model name from sample data appears in stdout
    assert "minimax-m2.7" in result.stdout

def test_daily_command_json_output(tmp_path, sample_db):
    result = subprocess.run(
        [sys.executable, "-m", "queries.summary", "daily",
         "--date", "2026-05-17", "--format", "json"],
        capture_output=True, text=True,
        env={**os.environ, "TOKEN_SIDECAR_DB": str(sample_db)},
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert data[0]["model"] == "minimax-m2.7"

def test_hourly_command_requires_date(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "queries.summary", "hourly"],
        capture_output=True, text=True,
    )
    # Should fail with usage error (date is required)
    assert result.returncode != 0

def test_by_model_sorts_by_total_tokens_desc(sample_db):
    result = subprocess.run(
        [sys.executable, "-m", "queries.summary", "by-model",
         "--format", "json"],
        capture_output=True, text=True,
        env={**os.environ, "TOKEN_SIDECAR_DB": str(sample_db)},
    )
    data = json.loads(result.stdout)
    totals = [row["total_tokens"] for row in data]
    assert totals == sorted(totals, reverse=True)   # descending
```

Use the same `sample_db` fixture pattern as `test_proxy.py`:
```python
@pytest.fixture
def sample_db(tmp_path: pathlib.Path) -> pathlib.Path:
    db_path = tmp_path / "tokens.db"
    import db as _db
    _db.init_db(str(db_path))
    # Insert two models, multiple days
    ...
    return db_path
```

---

### Step 8 — Run full test suite

**Target:** All existing tests (39 from Phase 4) + new query tests must pass.

Run:
```bash
uv run python -m pytest tests/ -v --tb=short
```

Expected: **≥ 45 tests passing**.

If a subprocess test fails with `FILE NOT FOUND` for the module,
ensure `queries/summary.py` has `if __name__ == "__main__": cli()` guard.

---

### Step 9 — Manual verification

Run each command manually against a real (or fixture) database:

```bash
# Daily summary (today)
uv run python -m queries.summary daily

# Daily for specific model (if data exists)
TOKEN_SIDECAR_DB=~/.token_sidecar/tokens.db \
  uv run python -m queries.summary daily --model minimax-m2.7

# Hourly breakdown
TOKEN_SIDECAR_DB=~/.token_sidecar/tokens.db \
  uv run python -m queries.summary hourly --date 2026-05-17

# By model (JSON for scripting)
uv run python -m queries.summary by-model --format json | jq '.[] | .model'

# Pipe to other tools
TOKEN_SIDECAR_DB=~/.token_sidecar/tokens.db \
  uv run python -m queries.summary daily --date 2026-05-17 --format json \
  | python -c "import sys,json; data=json.load(sys.stdin); print(sum(r['total_tokens'] for r in data))"
```

Verify: table output is aligned and readable, JSON output is valid and parseable.

---

### Step 10 — Git commit

```bash
git add queries/ tests/test_queries.py pyproject.toml    # uv.lock updated by uv sync
git commit -m "Phase 5: query CLI with daily/hourly/by-model commands"
```

Also commit the Phase 5 plan document:
```bash
git add project_docs/plans/phase-5.md
git commit -m "Add Phase 5 plan: Summary Query CLI"
```

---

## Exit Criteria

1. `python -m queries.summary --help` shows three subcommands with correct options
2. `--format table` and `--format json` both work on all three commands
3. Auto-detect (tty → table, pipe → json) works correctly
4. `--model <name>` filter narrows daily output to that model only
5. All new tests pass under `pytest`
6. Full suite: **≥ 45 tests passing** with no regressions

---

## File Structure After Phase 5

```
token_sidecar/
├── queries/
│   ├── __init__.py           # empty — makes it a package for -m invocation
│   └── summary.py            ← NEW (Phase 5) — click CLI, 3 subcommands
├── tests/
│   ├── test_db.py            ← from Phase 1 (11 passing)
│   ├── test_proxy.py         ← from Phases 2 & 3 (11 passing)
│   ├── test_launchd.py       ← from Phase 4 (17 passing)
│   └── test_queries.py       ← NEW (Phase 5) — query CLI tests
├── db.py                     ← from Phase 1
├── sidecar.py                ← from Phases 2 & 3
├── config_loader.py          ← from Phase 3
└── setup_launchd.py          ← from Phase 4
```

---

## Out of Scope

- **Web UI / dashboard** — not in scope per requirements.md; terminal-only for now
- **Prometheus / Grafana export** — not requested
- **Real-time streaming updates** — future work if needed
- **Export to CSV** — could be added trivially but not planned here