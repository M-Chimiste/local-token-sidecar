# Phase 0 — Scaffold: Detailed Implementation Plan

**Project:** token-sidecar  
**Phase:** 0 of 7  
**Parent plan:** `../implementation_plan.md`  
**Goal:** Full project structure, development environment, and dependency setup.

---

## Step 1 — Create Directory Tree

Create the following directory structure under `~/Documents/hermes_projects/token_sidecar/`:

```
token_sidecar/
├── sidecar.py              # Main proxy (Phase 2+)
├── config.yaml             # Configuration file
├── setup_launchd.py        # LaunchAgent installer (Phase 4)
├── queries/
│   └── summary.py          # Summary CLI (Phase 5)
├── tests/
│   ├── test_proxy.py       # Proxy unit tests (Phase 2)
│   ├── test_db.py          # DB unit tests (Phase 1)
│   └── test_queries.py     # Query CLI tests (Phase 5)
├── project_docs/
│   ├── requirements.md
│   ├── implementation_plan.md
│   ├── project_status.md
│   └── plans/
│       └── phase-0.md      # This file
└── README.md               # (Phase 7)
```

**Commands:**
```bash
mkdir -p ~/Documents/hermes_projects/token_sidecar/{queries,tests,project_docs/plans}
touch ~/Documents/hermes_projects/token_sidecar/sidecar.py
touch ~/Documents/hermes_projects/token_sidecar/config.yaml
touch ~/Documents/hermes_projects/token_sidecar/setup_launchd.py
touch ~/Documents/hermes_projects/token_sidecar/queries/summary.py
touch ~/Documents/hermes_projects/token_sidecar/tests/test_proxy.py
touch ~/Documents/hermes_projects/token_sidecar/tests/test_db.py
touch ~/Documents/hermes_projects/token_sidecar/tests/test_queries.py
```

**Verification:**
```bash
find ~/Documents/hermes_projects/token_sidecar -type f | sort
# All 12 files listed above should appear (README.md excluded for now)
```

---

## Step 2 — Create `config.yaml`

Create the file at `~/Documents/hermes_projects/token_sidecar/config.yaml` with all default values:

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
  enabled: true
  label: "com.athena.token-sidecar"
```

**Verification:**
```bash
python3 -c "import yaml; print(yaml.safe_load(open('~/Documents/hermes_projects/token_sidecar/config.yaml')))"
# Should print parsed YAML dict without errors
```

---

## Step 3 — Install UV and Set Up Environment

Check if `uv` is already installed, then create a project environment.

**Commands:**
```bash
# Check if uv is available
uv --version

# If not installed, install via curl (macOS/Linux)
curl -LsSf https://astral.sh/uv/install.sh | sh
# Or via Homebrew: brew install uv

# Create a Python 3.11 project environment in the project root
cd ~/Documents/hermes_projects/token_sidecar
uv init --python 3.11 --no-readme
```

---

## Step 4 — Add Dependencies with UV

Create `pyproject.toml` (or use inline deps with `uv add`):

```bash
# Install all dependencies at once
uv add aiohttp pyyaml pytest pytest-asyncio httpx

# Verify:
uv pip list | grep -E "aiohttp|pyyaml|pytest|httpx"
```

Or create `requirements.txt` and lock from it:
```
aiohttp>=3.9.0
pyyaml>=6.0
pytest>=7.0
pytest-asyncio>=0.23.0
httpx>=0.26.0
```

```bash
uv pip install -r requirements.txt --system   # or per-project
```

**Note:** `uv` uses its own lockfile (`uv.lock`) — commit it to git for reproducible builds.

---

## Step 6 — Create `.gitignore`

Create `~/Documents/hermes_projects/token_sidecar/.gitignore`:

```
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python

# UV (package manager)
.venv/
uv.lock       # unless you want to commit lockfile — recommended: keep it committed
*/.venv/

# Testing
.pytest_cache/
.coverage
htmlcov/

# Project-specific
*.db
*.log
.token_sidecar/

# Editor
.vscode/
.idea/
*.swp
*.swo

# macOS
.DS_Store
```

**Note:** `uv.lock` is recommended for reproducible installs — keep it committed.

---

## Step 7 — Verify LM Studio API Availability

Before proceeding, confirm LM Studio is running and its API is accessible.

```bash
curl -s http://localhost:8080/v1/models | python3 -m json.tool | head -30
# Expected: JSON listing available models (e.g. {"data": [...model names...]})
```

**If this fails:** Check that:
1. LM Studio is running on the Mac
2. "Enable API" is checked in LM Studio settings
3. The default port (8080) matches `config.yaml`'s `upstream_url`

---

## Step 8 — Initial Git Commit

Stage and commit all scaffold files.

```bash
cd ~/Documents/hermes_projects/token_sidecar
git add -A
git status   # review what will be committed
git commit -m "Phase 0: project scaffold, virtual environment, dependencies"
```

**Verification:**
```bash
git log --oneline
# Should show exactly one commit: "Phase 0: project scaffold..."
```

---

## Exit Criteria Checklist

- [ ] All directories and placeholder files created (Step 1)
- [ ] `config.yaml` exists with correct structure and parses without error (Steps 2, 7)
- [ ] `uv` installed; project environment initialized with Python >= 3.11 confirmed (Step 3)
- [ ] Dependencies installed via `uv add` or `uv pip install -r requirements.txt`; all packages verified in `uv pip list` (Step 4)
- [ ] `.gitignore` covers UV lockfile, `__pycache__`, `*.db`, `*.log`, `.DS_Store` (Step 6)
- [ ] LM Studio API responds at `localhost:8080/v1/models` (Step 7)
- [ ] All files committed to git with a single clean commit message (Step 8)

---

## Notes

- **Do not write code yet** — Phase 1 handles the database layer. These placeholders will be replaced in later phases.
- If LM Studio is not running or the API port differs, update `config.yaml` accordingly before Phase 1.
- Use `uv sync` to install/lock dependencies from `uv.lock` on fresh clones. Run `uv add <package>` when adding new deps — it auto-updates `pyproject.toml` and `uv.lock`.
- Activate the UV environment with `source .venv/bin/activate` (same as venv) or use `uv run` to execute commands directly without activating.

---

*Last updated: 2026-05-17*