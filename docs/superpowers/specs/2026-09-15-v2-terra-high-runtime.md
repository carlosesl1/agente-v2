# GPT 5.6 Terra High Runtime Design

**Date:** 2026-09-15
**Authorization:** Carlos explicitly requested changing the WhatsApp-answering V2 agent to GPT 5.6 Terra at reasoning effort `high` and executing a test.

## Scope

Change the semantic Maya child used by Chapada Leads V2 from `openai-codex/gpt-5.6-luna` to `openai-codex/gpt-5.6-terra`, with `reasoning_config={"enabled": true, "effort": "high"}` on every child invocation. Apply the same immutable successor to GA and the isolated authorized test contact.

## Authority and ownership

- `v2_host/settings.py` owns the controlled model identity accepted by productive workers.
- `v2_host/hermes_child.py` owns construction of the tool-free `AIAgent` and therefore owns the effective reasoning configuration.
- `compose.v2.yaml` owns the worker command/model/prompt projection.
- `config/v2_terra_system_prompt.txt` is the versioned copy of the existing V8 commercial contract; this change does not alter its business semantics.
- `Dockerfile.v2` owns inclusion of that prompt in the immutable image.

## Invariants

1. Provider remains `openai-codex`.
2. Model is exactly `gpt-5.6-terra`.
3. Reasoning effort is exactly `high`; no provider default is accepted as evidence.
4. The child remains one-turn, tool-free, without memory, SOUL or context files.
5. No business rule, provider adapter, reservation/payment path, V3, legacy or Maya Ops behavior changes.
6. Rollout is immutable and reversible: isolated contact first, then GA only after green health and a real model smoke.
7. The final WhatsApp smoke uses a neutral message beginning with `>>>`, one unique operation ID, no blind retry after an unknown result, and no commercial effects.

## Verification

- Causal unit test captures `AIAgent` arguments and requires Terra plus `reasoning_config={"enabled": True, "effort": "high"}`.
- Settings and Compose artifact tests require the exact Terra identity and command flag.
- Candidate image inspection proves OCI revision, prompt file and runtime contract.
- Isolated test runtime proves `/readyz`, queue health and a direct tool-free model call before GA.
- WhatsApp evidence proves one inbound, one processed turn, exact provider/model, high reasoning configuration, one delivered outbox response and read-back, with unchanged commercial-effect ledgers.
- Final `runtime_authority.py verify` must return `runtime authority: OK`.
