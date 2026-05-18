# Central Postgres Setup

This is the recommended Mac mini setup for centralized token-sidecar reporting
over Tailscale.

## Install Postgres

```bash
brew install postgresql
brew services start postgresql
createdb token_sidecar
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

## Roles

Run as a local Postgres admin on the Mac mini and replace the passwords:

```sql
CREATE ROLE token_sidecar_writer LOGIN PASSWORD 'replace-writer-password';
CREATE ROLE token_sidecar_reader LOGIN PASSWORD 'replace-reader-password';

GRANT CONNECT ON DATABASE token_sidecar TO token_sidecar_writer;
GRANT CONNECT ON DATABASE token_sidecar TO token_sidecar_reader;
```

Initialise the table, indexes, and views:

```bash
TOKEN_SIDECAR_ADMIN_DSN='postgresql://<admin>@localhost:5432/token_sidecar' \
  uv run python scripts/init_postgres.py
```

Then grant writer/read-only access:

```sql
GRANT USAGE ON SCHEMA public TO token_sidecar_writer, token_sidecar_reader;
GRANT INSERT ON token_usage TO token_sidecar_writer;
GRANT SELECT ON token_usage TO token_sidecar_reader;
GRANT SELECT ON token_usage_daily TO token_sidecar_reader;
GRANT SELECT ON token_usage_hourly TO token_sidecar_reader;
GRANT SELECT ON token_usage_by_model TO token_sidecar_reader;
GRANT SELECT ON token_usage_by_node TO token_sidecar_reader;
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

Export the writer DSN before running manually or installing the LaunchAgent:

```bash
export TOKEN_SIDECAR_POSTGRES_DSN='postgresql://token_sidecar_writer:replace-writer-password@mac-mini.tailnet-name.ts.net:5432/token_sidecar'
uv run python setup_launchd.py install
```

The LaunchAgent plist will include the DSN environment variable and is written
with mode `0600` when central sync is enabled.

## Reporting

Use the reader DSN for CLI reporting and future dashboards:

```bash
TOKEN_SIDECAR_QUERY_DSN='postgresql://token_sidecar_reader:replace-reader-password@mac-mini.tailnet-name.ts.net:5432/token_sidecar' \
  uv run python -m queries.summary by-model --backend postgres --format table
```
