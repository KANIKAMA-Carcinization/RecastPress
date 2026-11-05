"""Own one loopback experiment server, private stdin and bounded file captures."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'target/php-wasix-wp-private-http-probe/server-runs'
STDOUT_LIMIT = 4*1024*1024
STDERR_LIMIT = 65536


def require(value, code):
    if not value: raise ValueError(code)


def identity(wire): return {'bytes': len(wire), 'sha256': hashlib.sha256(wire).hexdigest()}


def parse_json(wire):
    def pairs(rows):
        result = {}
        for name, value in rows:
            require(name not in result, 'http_server_duplicate_json_key'); result[name] = value
        return result
    def bad(_): raise ValueError('http_server_nonfinite_json')
    return json.loads(wire.decode('utf-8'), object_pairs_hook=pairs, parse_constant=bad)


def owned(path):
    require(WORK.resolve() == WORK and path.resolve() == path and path.is_relative_to(WORK), 'owned_http_server_path')
    return path


class Server:
    def __init__(self, binary, environment, payload, report):
        require(binary.resolve(strict=True) == binary and binary.is_file(), 'owned_http_server_binary')
        wire = (json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')
        require(0 < len(wire) <= 4096 and wire.count(b'\n') == 1, 'bounded_http_private_input_line')
        WORK.mkdir(parents=True, exist_ok=True)
        self.directory = owned(WORK / ('server-' + uuid.uuid4().hex)); self.directory.mkdir(mode=0o700)
        self.stdout = owned(self.directory / 'stdout.jsonl'); self.stderr = owned(self.directory / 'stderr.bin')
        self.out = self.stdout.open('xb'); self.err = self.stderr.open('xb')
        self.stdout.chmod(0o600); self.stderr.chmod(0o600)
        self.report = report; self.child = None; self.port = None; self.value = None; self.finished = False
        self.row = report['http_server_execution'] = {'owned_reaped': False, 'forced_cleanup': False,
            'capture_kind': 'owned_regular_files', 'private_startup_input': identity(wire),
            'private_input_on_http': False, 'run': self.directory.relative_to(ROOT).as_posix(), 'run_retained': True,
            'parent_shutdown_input_closed': False, 'captured_output_complete_after_reap': False}
        try:
            self.child = subprocess.Popen([str(binary)], cwd=ROOT, env=environment, stdin=subprocess.PIPE,
                stdout=self.out, stderr=self.err, bufsize=0,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.row['owned_pid'] = self.child.pid
            require(self.child.stdin.write(wire) == len(wire), 'http_private_input_delivery')
            self.child.stdin.flush()
        except BaseException:
            self.abort(); raise

    def check_captures(self):
        require(self.stdout.stat().st_size <= STDOUT_LIMIT and self.stderr.stat().st_size <= STDERR_LIMIT,
            'http_server_live_capture_quota')

    def __enter__(self):
        try:
            deadline = time.monotonic() + 60
            while True:
                self.check_captures()
                with self.stdout.open('rb') as stream: prefix = stream.read(513)
                if b'\n' in prefix:
                    line, _, _ = prefix.partition(b'\n')
                    value = parse_json(line)
                    require(type(value) is dict and set(value) == {'schema', 'port'}
                        and value['schema'] == 'recastpress.php-wasix-wp-private-http-ready.v0.5'
                        and type(value['port']) is int and 1 <= value['port'] <= 65535, 'fixed_http_server_ready')
                    self.port = value['port']; self.row['ready_line'] = identity(line + b'\n')
                    self.row['bound_port'] = self.port
                    return self
                require(len(prefix) <= 512 and self.child.poll() is None and time.monotonic() < deadline,
                    'http_server_ready_deadline_or_exit')
                time.sleep(0.01)
        except BaseException:
            self.abort(); raise

    def close_input(self):
        if self.child is not None and self.child.stdin is not None and not self.child.stdin.closed:
            try: self.child.stdin.close()
            except BrokenPipeError: pass
        self.row['parent_shutdown_input_closed'] = True

    def reap(self, timeout):
        self.close_input()
        deadline = time.monotonic() + timeout
        quota_failed = False
        while self.child.poll() is None and time.monotonic() < deadline:
            try: self.check_captures()
            except ValueError:
                quota_failed = True; break
            time.sleep(0.02)
        if self.child.poll() is None:
            self.row['forced_cleanup'] = True
            self.child.kill(); self.child.wait(timeout=5)
        else: self.child.wait(timeout=5)
        self.row.update(owned_reaped=True, exit_code=self.child.returncode)
        require(not quota_failed, 'http_server_live_capture_quota')

    def collect(self):
        self.out.close(); self.err.close(); self.check_captures()
        stdout, stderr = self.stdout.read_bytes(), self.stderr.read_bytes()
        self.row.update(stdout=identity(stdout), stderr=identity(stderr), captured_output_complete_after_reap=True)
        return stdout, stderr

    def finish(self):
        if self.finished: return self.value
        self.reap(30)
        stdout, stderr = self.collect()
        require(self.child.returncode == 0 and not self.row['forced_cleanup'] and not stderr, 'normal_http_server_exit')
        lines = stdout.splitlines(keepends=True)
        require(len(lines) == 2 and all(line.endswith(b'\n') for line in lines)
            and identity(lines[0]) == self.row['ready_line'], 'http_server_two_json_lines')
        self.value = parse_json(lines[1])
        require(type(self.value) is dict and self.value['schema'] == 'recastpress.php-wasix-wp-private-http-observation.v0.5',
            'http_server_observation_schema')
        self.row['final_line'] = identity(lines[1]); self.finished = True
        return self.value

    def abort(self):
        try:
            if self.child is not None:
                # Closing the private input first lets the Rust supervisor wait
                # for or cancel/reap its own in-flight worker before it exits.
                self.reap(310)
                self.collect()
                self.row['descendant_cleanup_verified'] = False
        finally:
            self.out.close(); self.err.close()

    def __exit__(self, kind, error, traceback):
        if kind is None:
            try: self.finish()
            except BaseException:
                self.abort(); raise
        elif not self.finished:
            self.abort()
