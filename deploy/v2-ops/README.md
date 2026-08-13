# V2 Ops dashboard deployment

This Compose project runs only the authenticated, read-only operations UI/API at
`/ops`. It has no provider, reservation, payment, delivery, Docker-socket or
business-ledger capability.

## Data contract

The sole bind mount is a dedicated directory containing
`v2-ops-trace.sqlite3` and the associated `-wal`/`-shm` files. The producer or
a host-side read-only SQLite open must materialize both sidecars before the web
container starts; the container mount is intentionally unable to create them.
Do not mount `ga-state`, the V2 deploy directory, private customer stores or
other operational ledgers.

## Render

```bash
cp deploy/v2-ops/env.example /restricted/path/v2-ops.env
chmod 600 /restricted/path/v2-ops.env
docker compose --env-file /restricted/path/v2-ops.env \
  -f deploy/v2-ops/compose.ops.yaml config
```

## Start and verify

```bash
docker compose --env-file /restricted/path/v2-ops.env \
  -f deploy/v2-ops/compose.ops.yaml up -d
curl -fsS https://hermes.chapadabackpackers.com/ops/healthz
```

The root WebUI route is not owned by this project. Rollback removes only this
Compose project and its Traefik router:

```bash
docker compose --env-file /restricted/path/v2-ops.env \
  -f deploy/v2-ops/compose.ops.yaml down
```
