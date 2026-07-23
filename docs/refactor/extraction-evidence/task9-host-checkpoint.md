# Task 9 checkpoint — host, fake E2E and image

Date: 2026-07-23
Status: PARTIAL; Task 9 remains `NEXT`

## Implemented

- Fail-closed `V2Settings` real-effect gates with exact operational acknowledgment.
- Role-aware `V2Container` as the only host lifecycle owner.
  - API role owns only the per-operation inbox port.
  - Worker role owns one instance each of boundary, execution, followup, payment-initiation and public-outbox stores.
  - Every SQLite owner uses a distinct file under the same state directory.
- Executable API role with `/healthz` and `/readyz`.
- One non-root image (`Dockerfile.v2`) and hardened compose API service.
- Executable fake-provider qualification runner for:
  1. lodging + Stripe;
  2. activity + Pix;
  3. package + Wise.

## Real execution evidence

Host qualification:

```text
3 passed in 0.97s
```

Runner output:

```json
{"exit_code":0,"providers":"fake_only","real_effects":false,"scenarios":["lodging_stripe","activity_pix","package_wise"],"status":"passed"}
```

Final candidate image built locally:

```text
sha256:c2c9d31787d9cb37ff4224a63aad1f5f6ae85b31297dbff638a1bd9084aca76e
```

The same runner executed inside that image as UID 10001, with a read-only root filesystem, no capabilities and `no-new-privileges`:

```text
3 passed in 0.51s
```

Container hardening inspection:

```text
10001:10001|readonly=true|capdrop=["ALL"]|security=["no-new-privileges"]
```

API process evidence from inside the running container:

```json
{
  "health": {"role": "api", "status": "alive"},
  "ready": {
    "real_effect_gates": {
      "bokun_writes": false,
      "cloudbeds_writes": false,
      "manychat_delivery": false,
      "stripe_links": false
    },
    "status": "ready"
  }
}
```

The test container was stopped after verification.

## Qualification semantics

The three scenarios use real V2 SQLite stores, reservation execution worker, payment initiation worker, public outbox worker, provider-specific adapters and counted local transports. Each effect is enqueued twice or each worker is run through idle to prove one external fake call per idempotency key. No real credentials, network providers, booking, charge or ManyChat delivery are used.

Settlement and reconciliation are not mocked by this runner; their mature stores/workers remain covered by the Phase 6 proportional regression. Package completion is derived from the two reservation outcomes, two isolated obligations, settlement receipts and the public-delivery receipt.

## Remaining before Task 9 can be DONE

- Implement or extract a concrete V2 `KernelPort`; only test doubles exist today.
- Compose the durable inbox worker with the real turn service and tool-free Hermes model transport.
- Compose all seven worker queues without noop/fallback workers.
- Compose authenticated financial evidence endpoints and receipt correlation in the host.
- Add the worker role to `compose.v2.yaml` only after the above composition is concrete.
- Run the repository-wide final suite and final image qualification again.

Provider writes and public ManyChat remain blocked.
