# Central Postgres Setup

This is the recommended Mac mini setup for centralized token-sidecar reporting
over Tailscale.

## Install Postgres

```bash
brew install postgresql
brew services start postgresql
```

## Bootstrap Database, Roles, Schema, And Grants

From this repo on `nyx`, run:

```bash
scripts/setup_nyx_postgres.sh
```

That wrapper will start the Homebrew Postgres service and then run
`scripts/bootstrap_postgres.py`, which will:

- create the `token_sidecar` database if it does not exist
- create `token_sidecar_writer` and `token_sidecar_reader` roles if needed
- generate passwords for newly-created roles
- create the `token_usage` table, indexes, and reporting views
- grant writer insert access plus narrow `event_id` read access for idempotent
  uploads, and reader select access
- write known DSNs to `~/.token_sidecar/postgres.env` with mode `0600`

If you rerun it later, existing role passwords are left unchanged by default so
already-configured sidecars do not break. To intentionally rotate passwords:

```bash
uv run python scripts/bootstrap_postgres.py --report-host nyx --rotate-passwords
```

Or use the wrapper:

```bash
scripts/setup_nyx_postgres.sh --rotate-passwords
```

If your local admin connection is not the default Homebrew setup, pass an admin
DSN explicitly:

```bash
uv run python scripts/bootstrap_postgres.py \
  --admin-dsn 'postgresql://my_admin@localhost:5432/postgres' \
  --report-host nyx
```

The wrapper accepts the same admin DSN and report-host choices:

```bash
scripts/setup_nyx_postgres.sh \
  --admin-dsn 'postgresql://my_admin@localhost:5432/postgres' \
  --report-host nyx
```

Bind Postgres to the Mac mini's Tailscale IP/name, not the public internet. A
minimal `postgresql.conf` change is:

```conf
listen_addresses = 'localhost,<tailscale-ip>'
```

Add a narrow `pg_hba.conf` rule for the tailnet range:

```conf
host    token_sidecar    token_sidecar_writer    100.64.0.0/10    scram-sha-256
host    token_sidecar    token_sidecar_reader    100.64.0.0/10    scram-sha-256
```

Restart after config edits:

```bash
brew services restart postgresql
```

## Sidecar Machines

Set a stable `node.id` in each machine's `config.yaml`, then enable central
sync:

```yaml
node:
  id: "athena"

database:
  path: "~/.token_sidecar/tokens.db"
  central:
    enabled: true
    driver: "postgres"
    dsn_env: "TOKEN_SIDECAR_POSTGRES_DSN"
    flush_interval_seconds: 5
    batch_size: 100
```

Copy the `TOKEN_SIDECAR_POSTGRES_DSN` export from `nyx`'s
`~/.token_sidecar/postgres.env`, then export it before running manually or
installing the LaunchAgent:

```bash
source ~/.token_sidecar/postgres.env
uv run python setup_launchd.py install
```

The LaunchAgent plist will include the DSN environment variable and is written
with mode `0600` when central sync is enabled.

## Reporting

Use the reader DSN for CLI reporting and future dashboards:

```bash
source ~/.token_sidecar/postgres.env
uv run python -m queries.summary by-model --backend postgres --format table
```
