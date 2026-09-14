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


class _CountingReader(io.RawIOBase):
    """Count below buffering, including partial status lines before a timeout."""
    def __init__(self, raw, conn):
        self.raw, self.conn = raw, conn

    def readable(self):
        return True

    def readinto(self, buffer):
        count = self.raw.readinto(buffer)
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
        self.response_class = lambda *a, **kw: _TrackedResponse(self, *a, **kw)

    def connect(self):
        # Connect to the resolved address; certificate verification, SNI and HTTP
        # Host still use the original hostname. Reconnect never re-enters DNS.
        end = time.monotonic() + self.timeout
        failure = OSError("no radar IPv4 addresses")
        for family, kind, proto, _, address in self.addresses:
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(max(.001, end - time.monotonic()))
                sock.connect(address)
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
    IDLE_SEC = 4
    DNS_TTL_SEC = 15 * 60

    def __init__(self, on_retry=None):
        self.on_retry = on_retry
        self.addresses = {}
        self._resolved_at = {}
        self._dns_errors = {}
        self._resolving = {}  # host -> event; cold callers share one lookup
        self.connections = {}  # host -> connections, including leased sockets
        self._busy = set()
        self._used = {}
        self._idle_sec = {}
        self.retries = 0
        self._condition = threading.Condition()
        self._closed = False

    def begin_pass(self):
        with self._condition:
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
            if cached is not None:
                threading.Thread(target=self._resolve, args=(key, event),
                                 name='radar-dns', daemon=True).start()
            else:
                self._resolve(key, event)
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
        end = time.monotonic() + timeout
        for attempt in range(2):
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
            conn.response_bytes = 0
            try:
                if conn.sock:
                    conn.sock.settimeout(remaining)
                conn.request(req.get_method(), url.path + ('?' + url.query if url.query else ''),
                             headers=dict(req.header_items()))
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('radar request exceeded deadline')
                if conn.sock:
                    conn.sock.settimeout(remaining)
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
                if self.on_retry is not None:
                    self.on_retry(end)  # caller's rate/cooldown gate also covers retries
                with self._condition:
                    self.retries += 1

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
