"""Regression checks for the v0.5 and v0.6 binlog lock inputs."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import prepare_driver as driver


class DriverPlanTest(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(driver.PLAN.read_bytes())
        self.lock = driver.ROOT / self.plan['existing_reference']['cargo_lock']
        self.legacy = driver.LEGACY_SEED.read_bytes()

    def load_plan_with(self, lock, legacy):
        read_fixed = driver.read_fixed

        def read(path: Path, maximum):
            if path == self.lock:
                return lock
            if path == driver.LEGACY_SEED:
                if legacy is None:
                    self.fail('the pinned v0.5 lock should need no historical fixture')
                return legacy
            return read_fixed(path, maximum)

        with patch.object(driver, 'read_fixed', read):
            return driver.load_plan()

    def test_original_pinned_lock_is_accepted_without_fixture(self):
        self.assertEqual(self.load_plan_with(self.legacy, None), self.plan)

    def test_exact_version_delta_is_accepted(self):
        lock = self.lock.read_bytes()
        self.assertNotEqual(lock, self.legacy)
        self.assertEqual(self.load_plan_with(lock, self.legacy), self.plan)

    def test_other_changes_are_rejected(self):
        lock = self.lock.read_bytes()
        partial = lock.replace(
            b'name = "recastpress-db"\nversion = "0.6.0"',
            b'name = "recastpress-db"\nversion = "0.5.0"', 1)
        self.assertNotEqual(partial, lock)
        for current, legacy in ((partial, self.legacy), (lock + b'\n', self.legacy),
                                (lock, self.legacy + b'\n')):
            with self.subTest(current=current == lock, legacy=legacy == self.legacy):
                with self.assertRaises(ValueError):
                    self.load_plan_with(current, legacy)


if __name__ == '__main__':
    unittest.main()
