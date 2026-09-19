# Blocking-error handoff — minimal local correction

Carlos requires an actual human handoff when an error prevents service continuation.
Local base b8e974a; existing atendimento-simples worktree. No deploy or external sends.

1. RED: real inbox failure → terminal review → existing reconciliation projector →
   existing handoff outbox → ManyChat adapter/HTTP mock tag and flow. Prove restart,
   first-turn failure without boundary state, transient non-escalation and ACK replay.
2. GREEN: extend the existing ManualReviewHandoffProjector to consume unforwarded
   terminal inbox batches. Use HandoffCoordinator OPERATIONAL_REVIEW and persist the
   handoff ID in the inbox only after durable admission. No model dependency, new
   queue, table, agent or customer-language heuristic. The existing worker delivers.
   Resolve ownership from persisted inbound identity even if the model never ran.
3. Verify productive composition, failure between admission and linking, repeated
   ticks, same-lead grouping and isolation. No new business command or payment.
4. Run focal/full/static gates once candidate is stable; compare prior failure IDs.
   Record evidence and commit only local scoped files. Authority reverified.

Transport acceptance proves tag/flow execution, not that a person has already replied.
A broken ManyChat channel or stopped host cannot be promised as guaranteed delivery;
retain existing not-called retry and unknown-effect handling, without blind resend.
