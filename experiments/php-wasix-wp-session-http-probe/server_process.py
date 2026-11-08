"""Own the loopback adapter and its worker process group during the Linux run."""

import os
from pathlib import Path
import signal
import subprocess
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'target/php-wasix-wp-session-http-probe/server-runs'
READY_TIMEOUT = 310
SHUTDOWN_TIMEOUT = 40
ABORT_TIMEOUT = 340
STDOUT_LIMIT = 4 * 1024 * 1024
STDERR_LIMIT = 65536
DIAGNOSTICS = frozenset({
    'worker_startup_preflight', 'worker_spawn', 'worker_ready_timeout', 'worker_ready_transport',
    'worker_ready', 'worker_request_timeout', 'worker_request_invalid', 'worker_finish_timeout',
    'worker_finish_invalid', 'worker_identity_changed', 'parent_closed', 'http_drain_timeout',
    'fixed_http_probe_failed',
})
_OWNED_SERVERS = set()


def reap_owned_servers():
    for server in tuple(_OWNED_SERVERS):
        server.abort()


class Server:
    def __init__(self, binary, environment, payload, report, canonical, identity, parse_json):
        self.identity = identity
        self.parse_json = parse_json
        self.child = None
        self.finished = False
        self.reaped = False
        self.ready = None
        self.port = None
        if binary.resolve(strict=True) != binary or not binary.is_file():
            raise ValueError('owned_session_http_binary')
        wire = canonical(payload) + b'\n'
        if not 0 < len(wire) <= 4096 or wire.count(b'\n') != 1:
            raise ValueError('session_http_input_frame')
        WORK.mkdir(parents=True, exist_ok=True)
        if WORK.resolve() != WORK:
            raise ValueError('owned_session_http_capture_root')
        directory = WORK / ('server-' + uuid.uuid4().hex)
        directory.mkdir(mode=0o700)
        self.stdout, self.stderr = directory / 'stdout.jsonl', directory / 'stderr.bin'
        self.row = report['http_server_execution'] = {
            'owned_reaped': False, 'forced_cleanup': False, 'process_group_owned': True,
            'capture_kind': 'owned_regular_files', 'private_startup_input': identity(wire),
            'parent_shutdown_input_closed': False, 'captured_output_complete_after_reap': False,
        }
        try:
            with self.open_private(self.stdout) as out, self.open_private(self.stderr) as err:
                self.child = subprocess.Popen([str(binary)], cwd=ROOT, env=environment,
                    stdin=subprocess.PIPE, stdout=out, stderr=err, bufsize=0, start_new_session=True)
            _OWNED_SERVERS.add(self)
            self.row['owned_pid'] = self.child.pid
            if self.child.stdin.write(wire) != len(wire):
                raise ValueError('session_http_input_write')
            self.child.stdin.flush()
        except BaseException:
            self.abort()
            raise

    @staticmethod
    def open_private(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        return os.fdopen(fd, 'wb')

    def check_captures(self):
        if self.stdout.stat().st_size > STDOUT_LIMIT or self.stderr.stat().st_size > STDERR_LIMIT:
            raise ValueError('session_http_capture_quota')

    def __enter__(self):
        try:
            deadline = time.monotonic() + READY_TIMEOUT
            while time.monotonic() < deadline:
                self.check_captures()
                with self.stdout.open('rb') as stream:
                    line = stream.readline(513)
                if len(line) > 512:
                    raise ValueError('session_http_ready_size')
                if line.endswith(b'\n'):
                    value = self.parse_json(line)
                    if (type(value) is not dict or set(value) != {'schema', 'port', 'session_ready'}
                            or value['schema'] != 'recastpress.php-wasix-wp-session-http-ready.v0.5'
                            or type(value['port']) is not int or not 1 <= value['port'] <= 65535):
                        raise ValueError('session_http_ready_schema')
                    self.ready, self.port = value['session_ready'], value['port']
                    self.row.update(ready_line=self.identity(line), bound_port=self.port)
                    return self
                if self.exit_status() is not None:
                    break
                time.sleep(0.02)
            raise ValueError('session_http_ready_deadline_or_exit')
        except BaseException:
            self.abort()
            raise

    def close_input(self):
        if self.child is not None and not self.child.stdin.closed:
            try:
                self.child.stdin.close()
            except BrokenPipeError:
                pass
        self.row['parent_shutdown_input_closed'] = True

    def exit_status(self):
        # Preserve the PID until the owned group has been cleaned up or the
        # adapter has attested to the worker's normal reap.
        return os.waitid(os.P_PID, self.child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)

    def wait_for_exit(self, timeout):
        self.close_input()
        deadline = time.monotonic() + timeout
        while True:
            self.check_captures()
            status = self.exit_status()
            if status is not None:
                return status
            if time.monotonic() >= deadline:
                raise ValueError('session_http_shutdown_deadline')
            time.sleep(0.02)

    def reap(self):
        self.row.update(owned_reaped=True, exit_code=self.child.wait(timeout=5))
        self.reaped = True
        _OWNED_SERVERS.discard(self)

    def collect(self):
        self.check_captures()
        with self.stdout.open('rb') as out, self.stderr.open('rb') as err:
            stdout, stderr = out.read(STDOUT_LIMIT + 1), err.read(STDERR_LIMIT + 1)
        if len(stdout) > STDOUT_LIMIT or len(stderr) > STDERR_LIMIT:
            raise ValueError('session_http_capture_quota')
        self.row.update(stdout=self.identity(stdout), stderr=self.identity(stderr))
        return stdout, stderr

    def record_diagnostic(self):
        try:
            stdout, _ = self.collect()
            lines = stdout.splitlines()
            if not 1 <= len(lines) <= 2:
                return
            value = self.parse_json(lines[-1])
            if (type(value) is not dict or value.get('passed') is not False
                    or value.get('schema') != 'recastpress.php-wasix-wp-session-http-observation.v0.5'):
                return
            diagnostic = value.get('diagnostic')
            rows = value.get('requests')
            if diagnostic is None and type(rows) is list and 1 <= len(rows) <= 3 and type(rows[-1]) is dict:
                diagnostic = rows[-1].get('diagnostic')
            if type(diagnostic) is str and diagnostic in DIAGNOSTICS:
                self.row['diagnostic'] = diagnostic
        except (OSError, ValueError, RecursionError):
            pass

    def finish(self):
        status = self.wait_for_exit(SHUTDOWN_TIMEOUT)
        stdout, stderr = self.collect()
        lines = stdout.splitlines(keepends=True)
        if (len(stdout) > STDOUT_LIMIT or status.si_code != os.CLD_EXITED or status.si_status != 0
                or stderr or len(lines) != 2
                or not all(line.endswith(b'\n') for line in lines)
                or self.identity(lines[0]) != self.row['ready_line']):
            raise ValueError('session_http_server_completed_output')
        value = self.parse_json(lines[1])
        if (type(value) is not dict or value.get('passed') is not True
                or type(value.get('session_process')) is not dict
                or value['session_process'].get('reaped') is not True):
            raise ValueError('session_http_worker_reap_unverified')
        self.reap()
        self.row.update(final_line=self.identity(lines[1]), captured_output_complete_after_reap=True)
        self.finished = True
        return value

    def abort(self):
        if self.child is None or self.reaped:
            return
        try:
            self.wait_for_exit(ABORT_TIMEOUT)
        except ValueError:
            # Deadline and quota both require stopping the owned group now.
            pass
        finally:
            # A private group contains only the adapter and descendants it started.
            # Kill the group on unverified shutdown even if its leader has exited.
            try:
                os.killpg(self.child.pid, signal.SIGKILL)
                self.row['forced_cleanup'] = True
            except ProcessLookupError:
                pass
            self.reap()
            self.finished = True
            self.record_diagnostic()

    def __exit__(self, kind, error, traceback):
        if not self.finished:
            self.abort()
