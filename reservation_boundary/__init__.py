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
    SCHEMA_VERSION,
    TABLE_NAMES,
    render_postgresql,
    render_sqlite,
    schema_hash,
)
from reservation_boundary.sqlite_store import (
    BoundaryStoreError,
    ConcurrencyConflict,
    DataCorruption,
    IdentityConflict,
    LegacyStateReadPort,
    SQLiteBoundaryStore,
    StateNotFound,
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

__version__ = "0.7.0"
__all__ = (
    "__version__",
    *_types_all,
    "from_wire_json",
    "import_legacy_state",
    "BoundaryStoreError",
    "ConcurrencyConflict",
    "DataCorruption",
    "IdentityConflict",
    "LegacyStateReadPort",
    "SCHEMA_VERSION",
    "SQLiteBoundaryStore",
    "StateNotFound",
    "TABLE_NAMES",
    "render_postgresql",
    "render_sqlite",
    "schema_hash",
    "CoordinationError",
    "InvalidIntent",
    "InvalidKernelDecision",
    "TurnCoordinator",
    "TurnDeadlineExceeded",
    "TurnEventConflict",
    "TurnImportRejected",
    "ALIASES",
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
