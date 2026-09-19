"""Frozen Phase 7 wheel contracts; current V2 ships through Dockerfile.v2.

Original assertions (including wheel install/import outside checkout) execute
unchanged from the authenticated closeout, not against later V2 metadata.
"""
import unittest

from tests.phase7_snapshot import assert_historical_phase7


class Phase7PackageTests(unittest.TestCase):
    def test_project_metadata_declares_closed_distribution(self) -> None:
        assert_historical_phase7(self.id())

    def test_two_builds_are_byte_identical_closed_and_self_hashing(self) -> None:
        assert_historical_phase7(self.id())

    def test_installed_wheel_imports_without_checkout_on_sys_path(self) -> None:
        assert_historical_phase7(self.id())


if __name__ == "__main__":
    unittest.main()
