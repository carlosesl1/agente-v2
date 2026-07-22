"""Phase 8 exact E2E allocation authority installation."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
import inspect
from pathlib import Path
import tempfile
import unittest

import reservation_boundary as boundary
from reservation_execution.sqlite_store import DataCorruption as ExecutionCorruption
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.sqlite_store import DataCorruption as FollowupCorruption
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork

UTC6 = datetime(1970, 1, 1, 0, 0, 6, tzinfo=timezone.utc)


class Phase8AuthorityContractTests(unittest.TestCase):
    def _types(self):
        names = (
            "ActivationParentKind",
            "AllocationEffectRole",
            "AllocationInitialState",
            "AllocationInstallationReceipt",
            "AllocationRowKind",
            "EffectAllocationRow",
            "EffectFamily",
            "EffectKind",
            "ExactEffectAllocationManifest",
            "InstallationHeaderState",
            "InstallationStatus",
            "InstallationTarget",
        )
        values = {name: getattr(boundary, name, None) for name in names}
        self.assertEqual(
            [name for name, value in values.items() if value is None],
            [],
            "authority wire owners must be public",
        )
        return values

    def _public_row(self):
        t = self._types()
        return t["EffectAllocationRow"](
            row_kind=t["AllocationRowKind"].ALLOCATION,
            installation_target=t["InstallationTarget"].BOUNDARY_DISPATCH_AUTHORITY,
            qualification_id="qualification-1",
            epoch=1,
            scenario_id="scenario-1",
            contract_hash="1" * 64,
            effect_authorization_binding_hash="2" * 64,
            generation_id="generation-1",
            allocation_id="allocation-1",
            allocation_ordinal=0,
            effect_family=t["EffectFamily"].PUBLIC_DELIVERY,
            effect_kind=t["EffectKind"].PUBLIC_CHUNK,
            effect_role=t["AllocationEffectRole"].NONE,
            effect_scope_hash="3" * 64,
            workflow_scope_hash=None,
            channel_scope_hash="4" * 64,
            target_binding_hash="5" * 64,
            message_ordinal=0,
            activation_parent_kind=t["ActivationParentKind"].NONE,
            activation_parent_id=None,
            activation_parent_hash=None,
            initial_state=t["AllocationInitialState"].AVAILABLE,
        )

    def _target_row(self, target: str, family: str, allocation: str):
        t = self._types()
        target_enum = t["InstallationTarget"](target)
        family_enum = t["EffectFamily"](family)
        kind = (
            t["EffectKind"].PROVIDER_PRIMARY
            if family in {"reservation", "payment"}
            else t["EffectKind"].EXTERNAL_MESSAGE
        )
        role = (
            t["AllocationEffectRole"].PRIMARY
            if family in {"reservation", "payment"}
            else t["AllocationEffectRole"].NONE
        )
        parent_kind = (
            t["ActivationParentKind"].INTERNAL_TARGET_OPERATION
            if family == "handoff_delivery"
            else t["ActivationParentKind"].NONE
        )
        return t["EffectAllocationRow"](
            row_kind=t["AllocationRowKind"].ALLOCATION,
            installation_target=target_enum,
            qualification_id="qualification-e2e-1",
            epoch=1,
            scenario_id="scenario-e2e-1",
            contract_hash="a" * 64,
            effect_authorization_binding_hash="b" * 64,
            generation_id="generation-e2e-1",
            allocation_id=allocation,
            allocation_ordinal=0,
            effect_family=family_enum,
            effect_kind=kind,
            effect_role=role,
            effect_scope_hash="c" * 64,
            workflow_scope_hash="d" * 64,
            channel_scope_hash="e" * 64 if "delivery" in family else None,
            target_binding_hash="f" * 64,
            message_ordinal=None,
            activation_parent_kind=parent_kind,
            activation_parent_id="target-operation-parent-1" if parent_kind.value != "none" else None,
            activation_parent_hash="9" * 64 if parent_kind.value != "none" else None,
            initial_state=t["AllocationInitialState"].AVAILABLE,
        )

    def _manifest(self, row):
        t = self._types()
        return t["ExactEffectAllocationManifest"](
            qualification_id=row.qualification_id,
            epoch=row.epoch,
            contract_hash=row.contract_hash,
            effect_authorization_binding_hash=row.effect_authorization_binding_hash,
            rows=(row,),
            allocation_count=1,
        )

    def test_known_answer_row_manifest_and_installation_receipt(self) -> None:
        t = self._types()
        row = self._public_row()
        self.assertEqual(
            row.canonical_hash(),
            "be1abe35b18453699f563cd4899488f05eb7a3a60d3caff8ad77e8157d9ad889",
        )
        manifest = self._manifest(row)
        self.assertEqual(
            manifest.canonical_hash(),
            "30d8018e09a7b8b492ec183c4cfd37956a8bdc57e6f6bb4527b45de50d6d6beb",
        )
        receipt = t["AllocationInstallationReceipt"](
            operation_id="6" * 64,
            installation_target=t["InstallationTarget"].BOUNDARY_DISPATCH_AUTHORITY,
            qualification_id="qualification-1",
            epoch=1,
            contract_hash="1" * 64,
            effect_authorization_binding_hash="2" * 64,
            manifest_hash=manifest.canonical_hash(),
            generation_ids=("generation-1",),
            installed_row_hashes=(row.canonical_hash(),),
            allocation_count=1,
            installed_allocation_aggregate_hash="7" * 64,
            header_state=t["InstallationHeaderState"].OPEN,
            status=t["InstallationStatus"].INSTALLED,
            installed_at=UTC6,
        )
        self.assertEqual(
            receipt.canonical_hash(),
            "5307aa611603062d5009072b84a207ed8c341697464460a555936a42eb76ff3f",
        )

    def test_closed_matrix_and_manifest_order_fail_closed(self) -> None:
        row = self._public_row()
        t = self._types()
        with self.assertRaises((TypeError, ValueError)):
            t["EffectAllocationRow"](
                **{
                    field.name: (
                        "8" * 64
                        if field.name == "workflow_scope_hash"
                        else getattr(row, field.name)
                    )
                    for field in fields(row)
                }
            )
        reservation = self._target_row(
            "reservation_e2e_effect_authority", "reservation", "allocation-z"
        )
        earlier = self._target_row(
            "reservation_e2e_effect_authority", "reservation", "allocation-a"
        )
        earlier = type(earlier)(
            **{
                name: (1 if name == "allocation_ordinal" else getattr(earlier, name))
                for field in fields(earlier)
                for name in (field.name,)
            }
        )
        with self.assertRaises(ValueError):
            t["ExactEffectAllocationManifest"](
                qualification_id=reservation.qualification_id,
                epoch=1,
                contract_hash=reservation.contract_hash,
                effect_authorization_binding_hash=reservation.effect_authorization_binding_hash,
                rows=(earlier, reservation),
                allocation_count=2,
            )

    def test_reservation_install_is_atomic_idempotent_and_tombstone_wins(self) -> None:
        row = self._target_row(
            "reservation_e2e_effect_authority", "reservation", "allocation-reservation-1"
        )
        manifest = self._manifest(row)
        with tempfile.TemporaryDirectory(prefix="phase8-reservation-authority-") as root:
            path = Path(root) / "execution.db"
            with SQLiteUnitOfWork.open_v6(path) as store:
                install = getattr(store, "install_e2e_reservation_allocations", None)
                close = getattr(store, "close_e2e_reservation_generation", None)
                self.assertIsNotNone(install)
                self.assertIsNotNone(close)
                first = install(operation_id="6" * 64, manifest=manifest, installed_at=UTC6)
                replay = install(operation_id="6" * 64, manifest=manifest, installed_at=UTC6)
                self.assertEqual(first.to_canonical_bytes(), replay.to_canonical_bytes())
                self.assertEqual(
                    store._connection.execute(
                        "SELECT row_kind, state FROM reservation_e2e_effect_authority "
                        "ORDER BY row_kind"
                    ).fetchall(),
                    [("allocation", "available"), ("generation_header", "open")],
                )
            other = Path(root) / "tombstone.db"
            with SQLiteUnitOfWork.open_v6(other) as store:
                store.close_e2e_reservation_generation(
                    qualification_id=row.qualification_id,
                    epoch=row.epoch,
                    scenario_id=row.scenario_id,
                    generation_id=row.generation_id,
                    contract_hash=row.contract_hash,
                    effect_authorization_binding_hash=row.effect_authorization_binding_hash,
                    manifest_hash=manifest.canonical_hash(),
                    closed_at=UTC6,
                )
                with self.assertRaises(ExecutionCorruption):
                    store.install_e2e_reservation_allocations(
                        operation_id="6" * 64,
                        manifest=manifest,
                        installed_at=UTC6,
                    )

    def test_followup_install_filters_exact_target_rows(self) -> None:
        row = self._target_row(
            "followup_e2e_effect_authority", "payment", "allocation-payment-1"
        )
        manifest = self._manifest(row)
        with tempfile.TemporaryDirectory(prefix="phase8-followup-authority-") as root:
            path = Path(root) / "followup.db"
            with SQLiteFollowupUnitOfWork.open_v2(path) as store:
                install = getattr(store, "install_e2e_followup_allocations", None)
                self.assertIsNotNone(install)
                receipt = install(operation_id="7" * 64, manifest=manifest, installed_at=UTC6)
                self.assertEqual(receipt.allocation_count, 1)
                self.assertEqual(
                    store._connection.execute(
                        "SELECT row_kind, effect_family, state "
                        "FROM followup_e2e_effect_authority ORDER BY row_kind"
                    ).fetchall(),
                    [
                        ("allocation", "payment", "available"),
                        ("generation_header", None, "open"),
                    ],
                )


if __name__ == "__main__":
    unittest.main()
