"""Pure ownership/cleanup boundaries: no Docker command or DB is executed."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rp_owned_container_test_target',
    ROOT / 'experiments/php-wasix-wpdb-probe/linux_database.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OwnershipTests(unittest.TestCase):
    def fixture(self, *, label=None, image=None, missing=False, denied=False, stop_stuck=False):
        db = object.__new__(module.Database)
        db.cid = None
        db.name = 'rp-wpdb-mysql-' + 'b' * 32
        db.owner = 'b' * 32
        db.pin = {'image_config_digest': 'sha256:' + 'd' * 64}
        db.report = {'container_normal_exit_verified': False}
        self.calls = []
        state = {'running': True}

        def run(stage, command, **kwargs):
            self.calls.append(command)
            value = {'Id': 'a' * 64, 'Name': '/' + db.name,
                'Image': image or db.pin['image_config_digest'],
                'Config': {'Labels': {'recastpress.wpdb.owner': label or db.owner}},
                'State': {'Running': state['running'], 'Pid': 1 if state['running'] else 0,
                    'ExitCode': 0, 'OOMKilled': False}}
            if stage == 'recover_own_creation_identity':
                if missing: return subprocess.CompletedProcess(command, 1, b'', b'Error: No such object: owned')
                if denied: return subprocess.CompletedProcess(command, 1, b'', b'permission denied')
            if command[1] == 'stop' and not stop_stuck: state['running'] = False
            if command[1] == 'kill': state['running'] = False
            return subprocess.CompletedProcess(command, 0, json.dumps([value]).encode(), b'')
        db.run = run
        return db

    def mutations(self):
        return [call for call in self.calls if call[1] in ('stop', 'kill', 'rm')]

    @patch.object(module.owned_probe, 'reap_owned_probes')
    def test_created_but_cli_id_missing_is_recovered_by_exact_identity(self, _):
        db = self.fixture()
        db.close()
        self.assertEqual(db.cid, 'a' * 64)
        self.assertTrue(db.report['container_id_recovered_after_cli_failure'])
        self.assertTrue(db.report['container_normal_exit_verified'])
        self.assertTrue(db.report['container_removed'])
        self.assertEqual([c[1] for c in self.mutations()], ['stop', 'rm'])
        self.assertTrue(all(c[-1] == db.cid for c in self.mutations()))

    def test_same_name_with_another_owner_is_never_stopped(self):
        db = self.fixture(label='foreign-owner')
        with self.assertRaisesRegex(ValueError, 'identity_mismatch'): db.close()
        self.assertIsNone(db.cid)
        self.assertEqual(self.mutations(), [])

    def test_same_owner_with_another_image_is_never_stopped(self):
        db = self.fixture(image='sha256:' + 'e' * 64)
        with self.assertRaisesRegex(ValueError, 'identity_mismatch'): db.close()
        self.assertEqual(self.mutations(), [])

    def test_absent_resource_does_not_issue_mutation(self):
        db = self.fixture(missing=True)
        db.close()
        self.assertTrue(db.report['creation_no_container_observed'])
        self.assertEqual(self.mutations(), [])

    def test_inaccessible_daemon_is_not_treated_as_absent_resource(self):
        db = self.fixture(denied=True)
        with self.assertRaisesRegex(ValueError, 'cleanup_unconfirmed'): db.close()
        self.assertNotIn('creation_no_container_observed', db.report)
        self.assertEqual(self.mutations(), [])

    @patch.object(module.owned_probe, 'reap_owned_probes')
    def test_stuck_stop_is_recovered_but_never_claimed_normal(self, _):
        db = self.fixture(stop_stuck=True)
        with self.assertRaisesRegex(ValueError, 'still_running'): db.close()
        self.assertTrue(db.report['forced_owned_container_cleanup'])
        self.assertTrue(db.report['container_removed'])
        self.assertFalse(db.report['container_normal_exit_verified'])
        self.assertTrue(all(c[-1] == db.cid for c in self.mutations()))


if __name__ == '__main__': unittest.main()
