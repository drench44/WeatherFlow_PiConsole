"""Bounded persistent IPv4/SNI transport, shared across radar passes."""
import http.client
import io
import re
import ssl
import socket
import time
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit


TRANSPORT_ERRORS = (http.client.RemoteDisconnected, http.client.BadStatusLine,
                    ConnectionError, ssl.SSLEOFError, ssl.SSLZeroReturnError,
                    socket.timeout)


def is_transport_error(error):
    return isinstance(error, TRANSPORT_ERRORS)


class _StaleFirstByteTimeout(socket.timeout):
    """A reused socket accepted a request but returned no HTTP response byte."""


def _tcp_keepalive(sock):
    # Mesh paths can disappear without FIN/RST. Best effort on non-Linux kernels.
    options = [(socket.SOL_SOCKET, getattr(socket, 'SO_KEEPALIVE', None), 1)]
    options += [(socket.IPPROTO_TCP, getattr(socket, name, None), value)
                for name, value in (('TCP_KEEPIDLE', 2), ('TCP_KEEPINTVL', 2), ('TCP_KEEPCNT', 2))]
    for level, option, value in options:
        if option is not None:
            try:
                sock.setsockopt(level, option, value)
            except OSError:
                pass  # an exposed constant need not be supported by this kernel


class _CountingReader(io.RawIOBase):
    """Count below buffering, including partial status lines before a timeout."""
    def __init__(self, raw, conn):
        self.raw, self.conn = raw, conn

    def readable(self):
        return True

    def readinto(self, buffer):
        # http.client may perform many reads (headers, chunk framing, body).
        # Recompute before EACH raw read: a per-recv timeout is not a deadline.
        end = self.conn.deadline
        first_byte = not self.conn.response_bytes and self.conn.first_byte_end is not None
        if first_byte:
            end = min(end, self.conn.first_byte_end)
        remaining = end - time.monotonic()
        try:
            if remaining <= 0:
                raise socket.timeout('radar response exceeded deadline')
            self.raw._sock.settimeout(remaining)
            count = self.raw.readinto(buffer)
        except socket.timeout as error:
            if first_byte and end < self.conn.deadline:
                raise _StaleFirstByteTimeout('radar reused socket first-byte timeout') from error
            raise
        self.conn.response_bytes += count or 0
        return count

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class _TrackedResponse(http.client.HTTPResponse):
    def __init__(self, conn, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fp = io.BufferedReader(_CountingReader(self.fp.detach(), conn))


class _Connection(http.client.HTTPSConnection):
    def __init__(self, host, port, addresses, timeout):
        super().__init__(host, port, timeout=timeout)
        self.addresses = addresses
        self.response_bytes = 0
        self.deadline = time.monotonic() + timeout
        self.first_byte_end = None
        self.response_class = lambda *a, **kw: _TrackedResponse(self, *a, **kw)

    def send(self, data):
        if self.sock is None:
            self.connect()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout('radar send exceeded deadline')
        self.sock.settimeout(remaining)
        super().send(data)

    def connect(self):
        # Connect to the resolved address; certificate verification, SNI and HTTP
        # Host still use the original hostname. Reconnect never re-enters DNS.
        end = self.deadline
        failure = OSError("no radar IPv4 addresses")
        for family, kind, proto, _, address in self.addresses:
            sock = socket.socket(family, kind, proto)
            try:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout('radar connect exceeded deadline')
                _tcp_keepalive(sock)
                sock.settimeout(remaining)
                sock.connect(address)
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout('radar TLS exceeded deadline')
                sock.settimeout(remaining)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as error:
                failure = error
                sock.close()
                if time.monotonic() >= end:
                    raise
        raise failure


class _Response:
    """A connection belongs to its caller until the body is consumed/closed."""
    def __init__(self, session, key, conn, response):
        self.session, self.key, self.conn = session, key, conn
        self.response = response
        self.released = False
        headers = getattr(response, 'headers', {})
        self.close_after = (getattr(response, 'will_close', False) or
                            'close' in [v.strip() for v in headers.get('Connection', '').lower().split(',')])
        self.idle_sec = session.IDLE_SEC
        match = re.search(r'(?:^|[,;]\s*)timeout\s*=\s*"?([0-9]+(?:\.[0-9]+)?)',
                          headers.get('Keep-Alive', ''), re.I)
        if match:
            # Leave a margin before the server's advertised idle deadline.
            self.idle_sec = min(self.idle_sec, max(0, float(match[1]) - .25))

    def __getattr__(self, name):
        return getattr(self.response, name)

    def __enter__(self):
        return self

    def __exit__(self, kind, error, tb):
        self.close(broken=kind is not None)

    def close(self, broken=False):
        if self.released:
            return
        self.released = True
        # Closing a partly consumed response must never reuse unread bytes.
        unread = self.response.length != 0 and not self.response.isclosed()
        self.response.close()
        self.session._release(self.key, self.conn, broken or unread or self.close_after, self.idle_sec)


class RadarSession:
    MAX_CONNECTIONS = 6
    IDLE_SEC = 2
    FIRST_BYTE_TIMEOUT_SEC = 3
    DNS_TTL_SEC = 15 * 60

    def __init__(self, on_retry=None, first_byte_timeout=None):
        self.on_retry = on_retry
        self.first_byte_timeout = (self.FIRST_BYTE_TIMEOUT_SEC if first_byte_timeout is None
                                   else float(first_byte_timeout))
        if not 0 < self.first_byte_timeout < float('inf'):
            raise ValueError('first-byte timeout must be finite and positive')
        self._pass_deadline = float('inf')
        self.addresses = {}
        self._resolved_at = {}
        self._dns_errors = {}
        self._resolving = {}  # host -> event; cold callers share one lookup
        self.connections = {}  # host -> connections, including leased sockets
        self._busy = set()
        self._used = {}
        self._idle_sec = {}
        self.retries = 0
        self.stale_first_byte_retries = 0
        self._condition = threading.Condition()
        self._closed = False

    def begin_pass(self, deadline=None):
        with self._condition:
            self._pass_deadline = float('inf') if deadline is None else deadline
            # A broken resolver costs once per pass; the next pass may recover.
            self._dns_errors.clear()
            self._expire()

    def _resolve(self, key, event):
        # Never hold the pool lock during resolver I/O, including refreshes.
        try:
            addresses = socket.getaddrinfo(*key, socket.AF_INET, socket.SOCK_STREAM)
            if not addresses:
                raise socket.gaierror('no radar IPv4 addresses')
        except OSError as error:
            with self._condition:
                if not self._closed:
                    self._dns_errors[key] = error
        else:
            with self._condition:
                if not self._closed:
                    self.addresses[key] = addresses
                    self._resolved_at[key] = time.monotonic()
        finally:
            with self._condition:
                self._resolving.pop(key, None)
                event.set()

    def _addresses_for(self, key, end):
        with self._condition:
            if self._closed:
                raise OSError('radar session closed')
            cached = self.addresses.get(key)
            expired = time.monotonic() - self._resolved_at.get(key, 0) >= self.DNS_TTL_SEC
            if cached is not None and not expired:
                return cached
            error = self._dns_errors.get(key)
            if error is not None:
                if cached is not None:
                    return cached  # failed refresh retries next pass
                raise error
            event = self._resolving.get(key)
            resolve = event is None
            if resolve:
                event = self._resolving[key] = threading.Event()
        if resolve:
            # The system resolver is not cancellable. Even the cold leader waits
            # on the event only until its deadline; late results may seed cache.
            threading.Thread(target=self._resolve, args=(key, event),
                             name='radar-dns', daemon=True).start()
        if cached is not None:
            return cached  # stale-while-refresh: first tile never waits for DNS
        if not event.wait(max(0, end - time.monotonic())):
            raise TimeoutError('radar DNS exceeded request deadline')
        with self._condition:
            if self._closed:
                raise OSError('radar session closed')
            if key in self._dns_errors:
                raise self._dns_errors[key]
            return self.addresses[key]

    def _expire(self):
        now = time.monotonic()
        for key, conns in list(self.connections.items()):
            for conn in list(conns):
                if conn not in self._busy and now - self._used.get(conn, now) >= self._idle_sec.get(conn, self.IDLE_SEC):
                    conn.close()
                    conns.remove(conn)
                    self._used.pop(conn, None)
                    self._idle_sec.pop(conn, None)
            if not conns:
                self.connections.pop(key, None)

    def _release(self, key, conn, broken=False, idle_sec=None):
        with self._condition:
            self._busy.discard(conn)
            if broken or self._closed:
                conn.close()
                conns = self.connections.get(key, [])
                if conn in conns:
                    conns.remove(conn)
                self._used.pop(conn, None)
                self._idle_sec.pop(conn, None)
            else:
                self._used[conn] = time.monotonic()
                self._idle_sec[conn] = self.IDLE_SEC if idle_sec is None else idle_sec
            self._condition.notify_all()

    def open(self, req, timeout):
        url = urlsplit(req.full_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('radar requires an HTTPS host')
        key = (url.hostname, url.port or 443)
        end = min(time.monotonic() + timeout, self._pass_deadline)
        for attempt in range(2):
            if time.monotonic() >= end:
                raise TimeoutError('radar pass exceeded deadline')
            addresses = self._addresses_for(key, end)
            with self._condition:
                while True:
                    if self._closed:
                        raise OSError('radar session closed')
                    self._expire()  # also expire sockets released during pool waits
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('radar DNS/pool exceeded request deadline')
                    conns = self.connections.setdefault(key, [])
                    idle = next((c for c in conns if c not in self._busy), None)
                    conn = idle if not attempt else None
                    if attempt and len(conns) >= self.MAX_CONNECTIONS and idle is not None:
                        # A retry needs a fresh socket. Evict an idle lease only
                        # if another caller has already taken the freed slot.
                        self._release(key, idle, broken=True)
                    reused = conn is not None
                    if conn is None and len(conns) < self.MAX_CONNECTIONS:
                        conn = _Connection(*key, addresses, remaining)
                        conns.append(conn)
                    if conn is not None:
                        self._busy.add(conn)
                        break
                    self._condition.wait(remaining)
            conn.timeout = remaining
            conn.deadline = end
            conn.first_byte_end = None
            conn.response_bytes = 0
            try:
                if conn.sock:
                    conn.sock.settimeout(remaining)
                conn.request(req.get_method(), url.path + ('?' + url.query if url.query else ''),
                             headers=dict(req.header_items()))
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('radar request exceeded deadline')
                if reused:
                    conn.first_byte_end = min(end, time.monotonic() + self.first_byte_timeout)
                if conn.sock:
                    conn.sock.settimeout(min(remaining, self.first_byte_timeout) if reused else remaining)
                response = conn.getresponse()
                if response.status != 200:
                    response.close()
                    raise HTTPError(req.full_url, response.status, response.reason, response.headers, None)
                return _Response(self, key, conn, response)
            except Exception as error:
                self._release(key, conn, broken=True)
                if (attempt or not reused or req.get_method() not in ('GET', 'HEAD') or
                        not is_transport_error(error) or conn.response_bytes or
                        time.monotonic() >= end):
                    raise
                first_byte = isinstance(error, _StaleFirstByteTimeout)
                if self.on_retry is not None:
                    # The caller's rate/cooldown gate also covers retries.
                    self.on_retry(end, first_byte=first_byte)
                with self._condition:
                    self.retries += 1
                    self.stale_first_byte_retries += int(first_byte)

    def discard(self, url):
        """Drop idle sockets after a validation error; never interrupt other leases."""
        parsed = urlsplit(url)
        key = (parsed.hostname, parsed.port or 443)
        with self._condition:
            for conn in list(self.connections.get(key, [])):
                if conn not in self._busy:
                    self._release(key, conn, broken=True)

    def close(self):
        with self._condition:
            self._closed = True
            for key, conns in list(self.connections.items()):
                for conn in list(conns):
                    if conn not in self._busy:
                        self._release(key, conn, broken=True)
            self.addresses.clear()
            self._resolved_at.clear()
            self._dns_errors.clear()
            for event in self._resolving.values():
                event.set()
            self._condition.notify_all()
