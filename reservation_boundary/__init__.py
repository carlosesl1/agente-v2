"""Phase 7 runtime boundary contracts."""

from reservation_boundary.types import *  # noqa: F403
from reservation_boundary.types import __all__ as _types_all
from reservation_boundary.serialization import (
    from_wire_json,
    semantic_hash,
    to_wire_json,
)
from reservation_boundary.legacy_state import import_legacy_state
from reservation_boundary.schema import (
    BOUNDARY_V8_TABLES,
    SCHEMA_VERSION,
    SCHEMA_VERSION_V8,
    TABLE_NAMES,
    expected_sqlite_v8_schema_fingerprint,
    render_postgresql,
    render_sqlite,
    render_sqlite_v8,
    schema_hash,
    sqlite_v8_schema_fingerprint,
)
from reservation_boundary.sqlite_store import (
    BoundaryStoreError,
    ConcurrencyConflict,
    DataCorruption,
    IdentityConflict,
    LegacyStateReadPort,
    SQLiteBoundaryStore,
    StateNotFound,
    TurnReceipt,
)
from reservation_boundary.coordinator import (
    CoordinationError,
    InvalidIntent,
    InvalidKernelDecision,
    TurnCoordinator,
    TurnDeadlineExceeded,
    TurnEventConflict,
    TurnImportRejected,
)
from reservation_boundary.dispatch import (
    ALIASES,
    AuthorizedDispatch,
    CATALOG,
    DispatchRejected,
    DispatchResult,
    ToolContract,
    ToolDispatch,
    command_migration_counts,
)
from reservation_boundary.shadow import (
    CRITICAL_FIELDS,
    NONCRITICAL_FIELDS,
    DecisionComparison,
    DecisionComparisonSummary,
    DecisionObservation,
    compare,
)
from reservation_boundary.properties import (
    PROPERTY_CASES,
    PROPERTY_SEED,
    PropertyReport,
    PropertyRow,
    run_property_sequences,
)
from reservation_boundary.faults import (
    CONTENTION_DOMAINS,
    CONTENTION_ROUNDS_PER_DOMAIN,
    MUTANT_COUNT,
    RESTART_SCHEDULES,
    FaultReport,
    FaultRow,
    run_fault_matrix,
)
from reservation_boundary.mutations import (
    MUTANTS,
    Mutant,
    MutationReport,
    MutationRow,
    run_mutations,
)
from reservation_boundary.conversation import *  # noqa: F403
from reservation_boundary.conversation import __all__ as _conversation_all
from reservation_boundary.reads import *  # noqa: F403
from reservation_boundary.reads import __all__ as _reads_all
from reservation_boundary.effects import *  # noqa: F403
from reservation_boundary.effects import __all__ as _effects_all
from reservation_boundary.qualification import *  # noqa: F403
from reservation_boundary.qualification import __all__ as _qualification_all

__version__ = "0.8.0"
__all__ = (
    "__version__",
    *_types_all,
    *_conversation_all,
    *_reads_all,
    *_effects_all,
    *_qualification_all,
    "from_wire_json",
    "import_legacy_state",
    "BoundaryStoreError",
    "ConcurrencyConflict",
    "DataCorruption",
    "IdentityConflict",
    "LegacyStateReadPort",
    "BOUNDARY_V8_TABLES",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_V8",
    "SQLiteBoundaryStore",
    "StateNotFound",
    "TurnReceipt",
    "TABLE_NAMES",
    "expected_sqlite_v8_schema_fingerprint",
    "render_postgresql",
    "render_sqlite",
    "render_sqlite_v8",
    "schema_hash",
    "sqlite_v8_schema_fingerprint",
    "CoordinationError",
    "InvalidIntent",
    "InvalidKernelDecision",
    "TurnCoordinator",
    "TurnDeadlineExceeded",
    "TurnEventConflict",
    "TurnImportRejected",
    "ALIASES",
    "AuthorizedDispatch",
    "CATALOG",
    "DispatchRejected",
    "DispatchResult",
    "ToolContract",
    "ToolDispatch",
    "command_migration_counts",
    "CRITICAL_FIELDS",
    "NONCRITICAL_FIELDS",
    "DecisionComparison",
    "DecisionComparisonSummary",
    "DecisionObservation",
    "compare",
    "PROPERTY_CASES",
    "PROPERTY_SEED",
    "PropertyReport",
    "PropertyRow",
    "run_property_sequences",
    "CONTENTION_DOMAINS",
    "CONTENTION_ROUNDS_PER_DOMAIN",
    "MUTANT_COUNT",
    "RESTART_SCHEDULES",
    "FaultReport",
    "FaultRow",
    "run_fault_matrix",
    "MUTANTS",
    "Mutant",
    "MutationReport",
    "MutationRow",
    "run_mutations",
    "semantic_hash",
    "to_wire_json",
)
