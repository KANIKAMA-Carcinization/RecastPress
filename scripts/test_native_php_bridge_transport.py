"""Exercise real owned pipe readers, including cancellation while reads block."""
import importlib.util
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import unittest


HERE = Path(__file__).resolve().parents[1] / "experiments/php-compat-v06-probe"
spec = importlib.util.spec_from_file_location("native_php_bridge_transport", HERE / "run_check.py")
bridge = importlib.util.module_from_spec(spec)
previous_path = sys.path[:]
try:
    sys.path.insert(0, str(HERE))
    spec.loader.exec_module(bridge)
finally:
    sys.path[:] = previous_path


class NativePhpBridgeTransportTests(unittest.TestCase):
    def owned_readers(self, source):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        child = subprocess.Popen([sys.executable, "-u", "-c", source], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 bufsize=0, creationflags=flags)
        cancelled = threading.Event()
        events = queue.Queue(maxsize=16)
        readers = []
        def cleanup():
            cancelled.set()
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            for reader in readers:
                reader.join(timeout=2)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()
            self.assertTrue(all(not reader.is_alive() for reader in readers))
        self.addCleanup(cleanup)
        for label, stream in (("stdout", child.stdout), ("stderr", child.stderr)):
            os.set_blocking(stream.fileno(), True)
            reader = threading.Thread(target=bridge._reader, args=(stream, label, events, cancelled), daemon=True)
            readers.append(reader)
            reader.start()
        return child, cancelled, events, readers

    def test_small_frame_is_forwarded_before_child_exits_and_eof_is_drained(self):
        child, _, events, readers = self.owned_readers(
            "import os,sys; os.write(1,b'ready\\n'); sys.stdin.buffer.read(1); "
            "os.write(1,b'done\\n'); os.write(2,b'diagnostic\\n')")
        self.assertEqual(events.get(timeout=5), ("stdout", b"ready\n"))
        self.assertIsNone(child.poll())
        child.stdin.write(b"x")
        child.stdin.flush()
        received = {"stdout": bytearray(), "stderr": bytearray()}
        ended = set()
        while len(ended) != 2:
            label, data = events.get(timeout=5)
            if data is None:
                ended.add(label)
            else:
                self.assertIsInstance(data, bytes)
                received[label].extend(data)
        self.assertEqual(child.wait(timeout=5), 0)
        for reader in readers:
            reader.join(timeout=2)
        self.assertTrue(all(not reader.is_alive() for reader in readers))
        self.assertEqual(bytes(received["stdout"]), b"done\n")
        self.assertEqual(bytes(received["stderr"]), b"diagnostic\n")

    def test_owned_child_kill_unblocks_both_idle_readers(self):
        child, cancelled, events, readers = self.owned_readers(
            "import os,sys; os.write(1,b'ready\\n'); sys.stdin.buffer.read()")
        self.assertEqual(events.get(timeout=5), ("stdout", b"ready\n"))
        self.assertIsNone(child.poll())
        cancelled.set()
        child.kill()
        self.assertIsNotNone(child.wait(timeout=5))
        for reader in readers:
            reader.join(timeout=2)
        self.assertTrue(all(not reader.is_alive() for reader in readers))


if __name__ == "__main__":
    unittest.main()
