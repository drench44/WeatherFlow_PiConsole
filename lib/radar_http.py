"""Bounded persistent IPv4/SNI transport, shared across radar passes."""
import http.client
import socket
import time
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit


class _Connection(http.client.HTTPSConnection):
    def __init__(self, host, port, addresses, timeout):
        super().__init__(host, port, timeout=timeout)
        self.addresses = addresses

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
        self.session._release(self.key, self.conn, broken or unread)


class RadarSession:
    MAX_CONNECTIONS = 4
    IDLE_SEC = 60

    def __init__(self):
        self.addresses = {}
        self.connections = {}  # host -> connections, including leased sockets
        self._busy = set()
        self._used = {}
        self._condition = threading.Condition()
        self._closed = False

    def begin_pass(self):
        with self._condition:
            # A broken resolver costs once per pass; the next pass may recover.
            self.addresses = {k:v for k,v in self.addresses.items() if not isinstance(v, Exception)}
            self._expire()

    def _expire(self):
        now = time.monotonic()
        for key, conns in list(self.connections.items()):
            for conn in list(conns):
                if conn not in self._busy and now - self._used.get(conn, now) > self.IDLE_SEC:
                    conn.close()
                    conns.remove(conn)
                    self._used.pop(conn, None)
            if not conns:
                self.connections.pop(key, None)
                self.addresses.pop(key, None)

    def _release(self, key, conn, broken=False):
        with self._condition:
            self._busy.discard(conn)
            if broken or self._closed:
                conn.close()
                conns = self.connections.get(key, [])
                if conn in conns:
                    conns.remove(conn)
                self._used.pop(conn, None)
            else:
                self._used[conn] = time.monotonic()
            self._condition.notify_all()

    def open(self, req, timeout):
        url = urlsplit(req.full_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('radar requires an HTTPS host')
        key = (url.hostname, url.port or 443)
        end = time.monotonic() + timeout
        with self._condition:
            if self._closed:
                raise OSError('radar session closed')
            self._expire()
            if key not in self.addresses:
                try:
                    self.addresses[key] = socket.getaddrinfo(*key, socket.AF_INET, socket.SOCK_STREAM)
                except OSError as error:
                    self.addresses[key] = error
            addresses = self.addresses[key]
            if isinstance(addresses, Exception):
                raise addresses
            while True:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('radar DNS/pool exceeded request deadline')
                if self._closed:
                    raise OSError('radar session closed')
                conns = self.connections.setdefault(key, [])
                conn = next((c for c in conns if c not in self._busy), None)
                if conn is None and len(conns) < self.MAX_CONNECTIONS:
                    conn = _Connection(*key, addresses, remaining)
                    conns.append(conn)
                if conn is not None:
                    self._busy.add(conn)
                    break
                self._condition.wait(remaining)
        conn.timeout = remaining
        if conn.sock:
            conn.sock.settimeout(remaining)
        try:
            conn.request(req.get_method(), url.path + ('?' + url.query if url.query else ''),
                         headers=dict(req.header_items()))
            response = conn.getresponse()
            if response.status != 200:
                response.close()
                raise HTTPError(req.full_url, response.status, response.reason, response.headers, None)
            return _Response(self, key, conn, response)
        except Exception:
            self._release(key, conn, broken=True)
            raise

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
            self._condition.notify_all()
