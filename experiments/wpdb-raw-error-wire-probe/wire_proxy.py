"""Observe four fixed queries on one transparent MySQL classic loopback stream."""

from hashlib import sha256
import re
import select
import socket
import threading
import time


LOOPBACK = '127.0.0.1'
DEADLINE_SECONDS = 30
POLL_SECONDS = 0.25
PACKET_LIMIT = 65536
PACKETS_PER_DIRECTION = 256
BYTES_PER_DIRECTION = 1024 * 1024

CLIENT_COMPRESS = 0x00000020
CLIENT_PROTOCOL_41 = 0x00000200
CLIENT_SSL = 0x00000800
CLIENT_ZSTD_COMPRESSION_ALGORITHM = 0x04000000

_QUERIES = (
    ('utf8_signal', b"SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO = 31001, MESSAGE_TEXT = @rp_err_message"),
    ('latin1_signal', b"SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO = 31002, MESSAGE_TEXT = @rp_err_message"),
    ('next_signal', b"SIGNAL SQLSTATE '45001' SET MYSQL_ERRNO = 31003, MESSAGE_TEXT = 'rp followup'"),
    ('recovery', b'SELECT 42 AS rp_recovered'),
)
QUERIES = dict(_QUERIES)


class ProxyError(ValueError):
    """A fixed diagnostic that never contains an endpoint, packet or query."""


def require(condition, code):
    if not condition:
        raise ProxyError(code)


def err_identity(payload):
    require(len(payload) >= 9 and payload[0] == 0xff and payload[3] == 0x23
        and re.fullmatch(rb'[0-9A-Z]{5}', payload[4:9]), 'wire_err_shape')
    return {'kind': 'err', 'code': int.from_bytes(payload[1:3], 'little'),
        'state_hex': payload[4:9].hex(), 'message_hex': payload[9:].hex(),
        'payload_bytes': len(payload), 'payload_sha256': sha256(payload).hexdigest()}


class Protocol:
    """Keep protocol state, counts and designated observations, never auth bytes."""

    def __init__(self):
        self.phase = 'greeting'
        self.pending = None
        self.quit = False
        self.mariadb_cache_metadata_offered = False
        self.mariadb_cache_metadata_negotiated = False
        self.observations = {}
        self.packets = {'client': 0, 'server': 0}
        self.wire_bytes = {'client': 0, 'server': 0}

    def packet(self, direction, sequence, payload):
        self.packets[direction] += 1
        self.wire_bytes[direction] += len(payload) + 4
        require(self.packets[direction] <= PACKETS_PER_DIRECTION
            and self.wire_bytes[direction] <= BYTES_PER_DIRECTION, 'wire_stream_limit')
        if self.phase == 'greeting':
            require(direction == 'server' and sequence == 0 and payload and payload[0] == 10,
                'wire_classic_greeting')
            version_end = payload.find(0, 1)
            extension = version_end + 1 + 27
            if version_end > 0 and b'MariaDB' in payload[1:version_end] and len(payload) >= extension + 4:
                self.mariadb_cache_metadata_offered = bool(int.from_bytes(payload[extension:extension + 4], 'little') & 0x10)
            self.phase = 'capabilities'
        elif self.phase == 'capabilities':
            require(direction == 'client' and sequence == 1 and len(payload) >= 4,
                'wire_classic_capabilities')
            flags = int.from_bytes(payload[:4], 'little')
            require(flags & CLIENT_PROTOCOL_41, 'wire_protocol_41_required')
            require(not flags & (CLIENT_COMPRESS | CLIENT_SSL | CLIENT_ZSTD_COMPRESSION_ALGORITHM),
                'wire_tls_or_compression_refused')
            require(len(payload) > 32, 'wire_classic_capabilities')
            self.mariadb_cache_metadata_negotiated = self.mariadb_cache_metadata_offered and bool(
                int.from_bytes(payload[28:32], 'little') & 0x10)
            self.phase = 'auth'
        elif self.phase == 'auth':
            if direction == 'server':
                require(payload and sequence >= 2 and payload[0] in (0, 1, 2, 0xfe), 'wire_auth_status')
                if payload[0] == 0:
                    self.phase = 'commands'
        elif direction == 'client':
            self.command(sequence, payload)
        elif self.pending is not None:
            self.first_reply(sequence, payload)

    def command(self, sequence, payload):
        require(sequence == 0 and payload and not self.quit and self.pending is None,
            'wire_command_sequence')
        if payload[0] == 1:
            require(payload == b'\x01', 'wire_quit_shape')
            self.quit = True
        elif payload[0] == 3:
            for index, (label, query) in enumerate(_QUERIES):
                if payload[1:] == query:
                    require(index == len(self.observations), 'wire_designated_query_order')
                    self.pending = label
                    return

    def first_reply(self, sequence, payload):
        require(sequence == 1 and payload, 'wire_first_reply_sequence')
        if self.pending == 'recovery':
            # MariaDB adds a one-byte metadata indicator only when both peers
            # negotiated the extension. This fresh SELECT must send definitions.
            expected = b'\x01\x01' if self.mariadb_cache_metadata_negotiated else b'\x01'
            require(payload == expected, 'wire_recovery_resultset')
            identity = {'kind': 'resultset', 'columns': 1}
        else:
            identity = err_identity(payload)
        self.observations[self.pending] = identity
        self.pending = None

    def finish(self, eof_direction):
        require(self.phase == 'commands' and self.pending is None
            and tuple(self.observations) == tuple(label for label, _ in _QUERIES), 'wire_observations_incomplete')
        require(eof_direction == 'client' or self.quit, 'wire_backend_closed_first')
        return {'connections': 1, 'authenticated': True, 'tls': False, 'compression': False,
            'mariadb_cache_metadata_negotiated': self.mariadb_cache_metadata_negotiated,
            'client_packets': self.packets['client'], 'server_packets': self.packets['server'],
            'quit_observed': self.quit, 'closed': True, 'observations': self.observations}


class WireProxy:
    """One owned loopback client and backend socket, joined on context exit."""

    def __init__(self, backend_port, queries=None):
        require(type(backend_port) is int and 1 <= backend_port <= 65535, 'wire_backend_port')
        require(queries is None or (type(queries) is dict and tuple(queries.items()) == _QUERIES),
            'wire_fixed_queries')
        self._backend_port = backend_port
        self._listener = None
        self._thread = None
        self._stop = threading.Event()
        self._socket_lock = threading.Lock()
        self._sockets = []
        self._result = None
        self._error = None
        self.port = None

    def __enter__(self):
        require(self._thread is None, 'wire_proxy_single_use')
        try:
            self._listener = self._own(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            self._listener.bind((LOOPBACK, 0))
            self._listener.listen(1)
            self.port = self._listener.getsockname()[1]
            self._deadline = time.monotonic() + DEADLINE_SECONDS
            self._thread = threading.Thread(target=self._run, name='rp-owned-mysql-wire-proxy', daemon=False)
            self._thread.start()
        except (OSError, RuntimeError):
            self._close_sockets()
            raise ProxyError('wire_proxy_start') from None
        return self

    def _own(self, stream):
        with self._socket_lock:
            if self._stop.is_set():
                stream.close()
                raise ProxyError('wire_proxy_stopped')
            self._sockets.append(stream)
        return stream

    def _close_sockets(self):
        with self._socket_lock:
            for stream in self._sockets:
                try:
                    stream.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                stream.close()
            self._sockets.clear()

    def _remaining(self):
        remaining = self._deadline - time.monotonic()
        require(not self._stop.is_set(), 'wire_proxy_stopped')
        require(remaining > 0, 'wire_proxy_deadline')
        return remaining

    def _accept(self):
        while True:
            self._listener.settimeout(min(self._remaining(), POLL_SECONDS))
            try:
                client, address = self._listener.accept()
            except TimeoutError:
                continue
            self._own(client)
            require(address[0] == LOOPBACK, 'wire_client_not_loopback')
            self._listener.close()
            return client

    def _read_exact(self, stream, length, eof_allowed=False):
        packet = bytearray(length)
        offset = 0
        while offset < length:
            stream.settimeout(min(self._remaining(), POLL_SECONDS))
            try:
                count = stream.recv_into(memoryview(packet)[offset:])
            except TimeoutError:
                continue
            if count == 0:
                require(eof_allowed and offset == 0, 'wire_packet_truncated')
                return None
            offset += count
        return packet

    def _relay(self, client, server):
        protocol = Protocol()
        while True:
            readable, _, _ = select.select([server, client], [], [], min(self._remaining(), POLL_SECONDS))
            for source in readable:
                direction, destination = ('server', client) if source is server else ('client', server)
                header = self._read_exact(source, 4, eof_allowed=True)
                if header is None:
                    return protocol.finish(direction)
                length = int.from_bytes(header[:3], 'little')
                require(length <= PACKET_LIMIT, 'wire_packet_limit')
                payload = self._read_exact(source, length)
                protocol.packet(direction, header[3], payload)
                destination.settimeout(self._remaining())
                destination.sendall(header)
                destination.sendall(payload)
                # No passthrough payload survives this iteration.
                del payload, header

    def _run(self):
        try:
            client = self._accept()
            server = self._own(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            server.settimeout(self._remaining())
            server.connect((LOOPBACK, self._backend_port))
            result = self._relay(client, server)
        except ProxyError as error:
            self._error = str(error)
        except (OSError, ValueError):
            self._error = 'wire_proxy_socket_failure'
        else:
            self._result = result
        finally:
            self._close_sockets()

    def _join(self, timeout):
        self._thread.join(timeout)
        if self._thread.is_alive():
            self._stop.set()
            self._close_sockets()
            self._thread.join(2)
        require(not self._thread.is_alive(), 'wire_proxy_join')

    def result(self):
        require(self._thread is not None, 'wire_proxy_not_started')
        self._join(max(0, self._deadline - time.monotonic()) + 1)
        require(self._error is None and self._result is not None, self._error or 'wire_proxy_no_result')
        return self._result

    def __exit__(self, kind, error, traceback):
        if kind is None:
            self.result()
        else:
            self._stop.set()
            self._close_sockets()
            self._join(2)
