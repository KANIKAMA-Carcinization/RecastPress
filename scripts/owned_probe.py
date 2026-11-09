"""Kill/reap only the fixture child just spawned here after its validated READY.

This is an experiment helper, not a process supervisor or database-server kill
API. The child must be a pending stage fixture after its single READY line.
Marker syntax, newline and owned-PID checks belong to parse_ready. Windows pipe
nonblocking mode requires Python 3.12+ (the development runtime is 3.13).
"""

from dataclasses import dataclass
import errno
import os
import queue
import subprocess
import time
import threading


MAX_READY_BYTES = 65536
_MYSQL_ENV = {"MYSQL_HOME", "MYSQL_TCP_PORT", "MYSQL_UNIX_PORT", "MYSQL_PWD", "MYSQL_TEST_LOGIN_FILE"}


CLEANUP_TIMEOUT = 5
IO_JOIN_TIMEOUT = 1
_OWNED_CHILDREN = {}
_OWNED_LOCK = threading.Lock()


def _remember(child):
    with _OWNED_LOCK:
        _OWNED_CHILDREN[id(child)] = child
    return child


def _kill_and_wait(child, timeout):
    errors = []
    try:
        if child.poll() is None:
            child.kill()
    except BaseException as error:
        errors.append(f"owned child {child.pid} kill: {type(error).__name__}: {error}")
    try:
        child.wait(timeout=timeout)
    except BaseException as error:
        errors.append(f"owned child {child.pid} wait: {type(error).__name__}: {error}")
    try:
        stopped = child.poll() is not None
    except BaseException as error:
        stopped = False
        errors.append(f"owned child {child.pid} poll: {type(error).__name__}: {error}")
    if stopped:
        with _OWNED_LOCK:
            _OWNED_CHILDREN.pop(id(child), None)
    else:
        errors.append(f"owned child {child.pid} is still alive or unconfirmed; captured handle retained")
    return errors


def _close_pipes(child):
    errors = []
    # All Popen pipes in this module are unbuffered. No BufferedReader lock or
    # communicate-created reader thread can make close wait for a live writer.
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(child, name, None)
        if stream is not None:
            try:
                stream.close()
            except BaseException as error:
                errors.append(f"owned child {child.pid} {name} close: {type(error).__name__}: {error}")
    return errors


def _cleanup_child(child, stop_io, original=None):
    errors = _kill_and_wait(child, CLEANUP_TIMEOUT)
    try:
        stop_io()
    except BaseException as error:
        errors.append(f"owned child {child.pid} IO stop: {type(error).__name__}: {error}")
    errors += _close_pipes(child)
    if errors:
        message = "owned probe cleanup failed; retain fixture: " + "; ".join(errors)
        if original is not None:
            original.add_note(message)
        else:
            raise RuntimeError(message)


def reap_owned_probes(timeout=CLEANUP_TIMEOUT):
    """Retry every captured unreaped child; never infer success from kill alone.

    Outer fixture cleanup calls this even after a kill failure, after stopping
    its captured servers. A still-live handle remains registered for inspection
    and another explicit retry. No PID lookup or foreign process is involved.
    """
    with _OWNED_LOCK:
        children = list(_OWNED_CHILDREN.values())
    errors = []
    for child in reversed(children):
        errors += _kill_and_wait(child, timeout)
        errors += _close_pipes(child)
    if errors:
        raise RuntimeError("owned probe retry cleanup failed; retain fixture: " + "; ".join(errors))


def _join_workers(workers, timeout):
    deadline = time.monotonic() + timeout
    for worker in workers:
        worker.join(timeout=max(0, deadline-time.monotonic()))
    if any(worker.is_alive() for worker in workers):
        raise TimeoutError("owned probe IO worker did not stop before deadline")


def _read_output(stream, output, cancelled, errors):
    try:
        while not cancelled.is_set():
            try:
                chunk = os.read(stream.fileno(), 65536)
            except BlockingIOError:
                cancelled.wait(0.01)
                continue
            if not chunk:
                return
            output.extend(chunk)
    except BaseException as error:
        errors.append(error)


def _write_input(stream, data, cancelled, errors):
    try:
        offset = 0
        while offset < len(data) and not cancelled.is_set():
            try:
                count = os.write(stream.fileno(), data[offset:offset+4096])
            except BlockingIOError:
                cancelled.wait(0.01)
                continue
            if count <= 0:
                raise OSError("owned probe stdin write made no progress")
            offset += count
    except BrokenPipeError:
        # As with communicate, a child that exits without consuming input is
        # judged by its actual exit/result; no further stdin delivery is needed.
        pass
    except OSError as error:
        # CPython's Popen._stdin_write handles this same Windows result when
        # the child exits or closes stdin before consuming the complete input.
        # Preserve its actual exit/output; unrelated IO errors still fail.
        if os.name != "nt" or error.errno != errno.EINVAL:
            errors.append(error)
    except BaseException as error:
        errors.append(error)
    finally:
        try:
            stream.close()
        except BaseException as error:
            errors.append(error)


def run_captured_probe(command, environment, timeout=30, input_bytes=None):
    """Run one captured child with bounded waits and cancellable raw pipe IO.

    No Popen context or communicate background thread may block unwinding after
    an OS kill failure. Outputs retain the old binary CompletedProcess shape.
    """
    if input_bytes is not None and type(input_bytes) is not bytes:
        raise TypeError("owned probe stdin must be bytes")
    isolated = {key: value for key, value in environment.items() if key.upper() not in _MYSQL_ENV}
    child = _remember(subprocess.Popen(command, env=isolated,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
    cancelled = threading.Event()
    workers = []
    output, stderr, errors = bytearray(), bytearray(), []

    def stop_io():
        cancelled.set()
        _join_workers(workers, IO_JOIN_TIMEOUT)

    original_error = None
    try:
        for stream in (child.stdout, child.stderr, child.stdin):
            if stream is not None:
                os.set_blocking(stream.fileno(), False)
        for stream, buffer in ((child.stdout, output), (child.stderr, stderr)):
            worker = threading.Thread(target=_read_output, args=(stream, buffer, cancelled, errors),
                name=f"owned-probe-output-{child.pid}", daemon=True)
            worker.start()
            workers.append(worker)
        if child.stdin is not None:
            worker = threading.Thread(target=_write_input, args=(child.stdin, input_bytes, cancelled, errors),
                name=f"owned-probe-input-{child.pid}", daemon=True)
            worker.start()
            workers.append(worker)
        deadline = time.monotonic()+timeout
        while True:
            if errors:
                raise errors[0]
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout, output=bytes(output), stderr=bytes(stderr))
            try:
                child.wait(timeout=min(0.05, remaining))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout, output=bytes(output), stderr=bytes(stderr)) from None
        # Only after process exit do we wait for EOF. Even inherited pipe writers
        # cannot turn this into an unbounded drain or a successful partial result.
        _join_workers(workers, CLEANUP_TIMEOUT)
        if errors:
            raise errors[0]
        return child.pid, subprocess.CompletedProcess(command, child.returncode, bytes(output), bytes(stderr))
    except BaseException as error:
        original_error = error
        raise
    finally:
        _cleanup_child(child, stop_io, original_error)


def _drain_exited(child, timeout=CLEANUP_TIMEOUT):
    """Bounded raw EOF drain, only after the owned child has been reaped."""
    if child.poll() is None:
        raise RuntimeError("refusing to drain pipes from a live owned child")
    streams = {child.stdout: bytearray(), child.stderr: bytearray()}
    pending = set(streams)
    deadline = time.monotonic()+timeout
    while pending:
        for stream in tuple(pending):
            try:
                chunk = os.read(stream.fileno(), 65536)
            except BlockingIOError:
                continue
            if chunk:
                streams[stream].extend(chunk)
            else:
                pending.remove(stream)
        if pending:
            if time.monotonic() >= deadline:
                raise TimeoutError("owned probe exited but pipe EOF did not arrive")
            time.sleep(0.01)
    return bytes(streams[child.stdout]), bytes(streams[child.stderr])


@dataclass(frozen=True)
class KilledProbe:
    process_pid: int
    exit_code: int
    ready: object
    ready_line: str


@dataclass(frozen=True)
class KilledServerProbe:
    child: KilledProbe
    server_pid: int
    server_exit_code: int


def require_success_output(completed, label):
    """Require one raw UTF-8 protocol line, clean stderr and a successful exit."""
    if type(completed.returncode) is not int or completed.returncode != 0:
        diagnostic = completed.stderr[:8192].decode("utf-8", errors="replace") if isinstance(completed.stderr, bytes) else "uncaptured stderr"
        raise ValueError(f"{label} failed: {diagnostic}")
    if completed.stderr != b"" or not isinstance(completed.stdout, bytes):
        raise ValueError(f"{label} did not emit a clean binary protocol result")
    wire = completed.stdout.decode("utf-8", errors="strict")
    body = wire.removesuffix("\n").removesuffix("\r")
    if len(completed.stdout) > MAX_READY_BYTES or not wire.endswith("\n") or "\n" in body or "\r" in body:
        raise ValueError(f"{label} must emit exactly one bounded result line")
    return wire


def _read_ready(descriptor, results, cancelled):
    """A cancellable single reader; never reads more than limit + one byte."""
    buffered = bytearray()
    try:
        while not cancelled.is_set():
            try:
                chunk = os.read(descriptor, min(4096, MAX_READY_BYTES + 1 - len(buffered)))
            except BlockingIOError:
                cancelled.wait(0.01)
                continue
            buffered.extend(chunk)
            newline = buffered.find(b"\n")
            if newline >= 0:
                results.put((bytes(buffered[:newline + 1]), bytes(buffered[newline + 1:]), None))
                return
            if not chunk or len(buffered) > MAX_READY_BYTES:
                results.put((bytes(buffered), b"", None))
                return
    except BaseException as error:
        results.put((b"", b"", error))


def run_until_killed(command, environment, parse_ready, timeout=30):
    """Validate READY, force-stop this owned pending child and verify its exit.

    parse_ready(wire, expected_pid=actual_pid) must reject malformed, unterminated
    or wrong-PID markers. Returning does not validate DB durability or recovery.
    All failure paths attempt bounded kill/reap and stop the reader before pipe
    closure. Failed live handles remain registered for outer cleanup retry; no
    PID supplied by output is ever killed.
    """
    return _run_pending(command, environment, parse_ready, timeout)


def run_until_server_killed(command, environment, parse_ready, server, guard, timeout=30):
    """Kill only a captured owned server handle before reaping its pending child.

    guard(ready) must verify the dedicated server identity before this operation.
    Invalid READY or a failed guard never requests a server kill. This operation
    proves process ordering only; SQL recovery must be checked separately.
    """
    observed = []

    def terminate_server(child, ready):
        if server.poll() is not None:
            raise ValueError("owned server exited before the requested crash")
        guard(ready)
        if child.poll() is not None or server.poll() is not None:
            raise ValueError("owned server or pending child exited before the requested crash")
        server.kill()
        code = server.wait(timeout=10)
        if type(code) is not int or code == 0:
            raise ValueError("owned server did not exit abnormally after kill")
        if child.poll() is not None:
            raise ValueError("pending child exited before ordered cleanup")
        observed.append(code)

    killed = _run_pending(command, environment, parse_ready, timeout, terminate_server)
    return KilledServerProbe(killed, server.pid, observed[0])


def _run_pending(command, environment, parse_ready, timeout, before_child_kill=None):
    isolated = {key: value for key, value in environment.items() if key.upper() not in _MYSQL_ENV}
    cancelled = threading.Event()
    results = queue.Queue(maxsize=1)
    child = _remember(subprocess.Popen(command, env=isolated, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
    reader = None
    reader_started = False

    def finish_reader():
        cancelled.set()
        if reader_started:
            _join_workers([reader], IO_JOIN_TIMEOUT)

    original_error = None
    try:
        os.set_blocking(child.stdout.fileno(), False)
        os.set_blocking(child.stderr.fileno(), False)
        reader = threading.Thread(target=_read_ready,
            args=(child.stdout.fileno(), results, cancelled),
            name=f"owned-probe-ready-{child.pid}", daemon=True)
        reader.start()
        reader_started = True
        try:
            line, buffered_extra, read_error = results.get(timeout=timeout)
        except queue.Empty as error:
            raise TimeoutError("owned probe did not produce READY before timeout") from error
        finish_reader()
        if read_error is not None:
            raise read_error
        if not line:
            child.wait(timeout=CLEANUP_TIMEOUT)
            _, stderr = _drain_exited(child)
            diagnostic = stderr[:8192].decode("utf-8", errors="replace").strip()
            raise ValueError("owned probe exited without READY" + (": " + diagnostic if diagnostic else ""))
        if len(line) > MAX_READY_BYTES:
            raise ValueError("owned probe READY line exceeds 65536 bytes")
        wire = line.decode("utf-8", errors="strict")
        ready = parse_ready(wire, expected_pid=child.pid)
        if buffered_extra:
            raise ValueError("owned probe emitted extra stdout after READY")
        if child.poll() is not None:
            raise ValueError("owned probe exited before the requested kill")
        if before_child_kill is not None:
            before_child_kill(child, ready)
        child.kill()
        child.wait(timeout=CLEANUP_TIMEOUT)
        remaining, stderr = _drain_exited(child)
        if child.returncode is None or child.returncode == 0:
            raise ValueError("owned probe did not exit abnormally after kill")
        if remaining:
            raise ValueError("owned probe emitted extra stdout after READY")
        if stderr:
            raise ValueError("owned probe emitted stderr")
        return KilledProbe(child.pid, child.returncode, ready, wire)
    except BaseException as error:
        original_error = error
        raise
    finally:
        # No context-manager __exit__/communicate may wait indefinitely. Preserve
        # the original failure and keep any still-live captured handle registered.
        _cleanup_child(child, finish_reader, original_error)
