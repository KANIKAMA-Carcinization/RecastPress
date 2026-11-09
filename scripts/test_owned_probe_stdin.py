"""Regress early-closed stdin without hiding unrelated pipe errors."""

import errno
import os
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import owned_probe


class OwnedProbeStdinTests(unittest.TestCase):
    def simulate(self, platform, failure):
        errors = []
        stream = SimpleNamespace(fileno=lambda: 123, close=Mock())
        with patch.object(owned_probe.os, "name", platform), patch.object(
            owned_probe.os, "write", side_effect=failure
        ):
            owned_probe._write_input(stream, b"payload", threading.Event(), errors)
        stream.close.assert_called_once()
        return errors

    def test_windows_early_closed_pipe_preserves_the_child_result(self):
        self.assertEqual(self.simulate("nt", OSError(errno.EINVAL, "closed pipe")), [])

    def test_other_windows_io_failures_remain_errors(self):
        failure = OSError(errno.EBADF, "invalid descriptor")
        self.assertEqual(self.simulate("nt", failure), [failure])

    def test_einval_on_other_platforms_remains_an_error(self):
        failure = OSError(errno.EINVAL, "invalid argument")
        self.assertEqual(self.simulate("posix", failure), [failure])

    def test_a_real_early_exit_is_reaped_and_returns_its_output(self):
        self.addCleanup(owned_probe.reap_owned_probes)
        command = [sys.executable, "-c", "import sys; sys.stdin.close(); print('closed'); sys.exit(3)"]
        _, result = owned_probe.run_captured_probe(
            command, os.environ.copy(), timeout=10, input_bytes=b"x" * (1024 * 1024)
        )
        self.assertIsInstance(result, subprocess.CompletedProcess)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout.strip(), b"closed")
        self.assertEqual(result.stderr, b"")


if __name__ == "__main__":
    unittest.main()
