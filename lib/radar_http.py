"""Bounded persistent IPv4/SNI transport, shared across radar passes."""
import errno
import weakref
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


class LocalTransportError(socket.timeout):
    """Client connection setup/resource deadline; not evidence of host health."""


class ResolverTimeout(LocalTransportError):
    """A DNS deadline cannot distinguish client resolver from provider DNS."""


class AmbiguousTransportError(socket.timeout):
    """A reused connection failed before a service response; retry fresh first."""


def failure_class(error):
    if isinstance(error, AmbiguousTransportError):
        return 'ambiguous'
    if isinstance(error, (LocalTransportError, socket.gaierror)) or (
            isinstance(error, OSError) and error.errno in {
                errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN,
                errno.EADDRNOTAVAIL, errno.EMFILE, errno.ENFILE, errno.ENOBUFS, errno.ENOMEM}):
        return 'local'
    return 'host'


def local_backoff_failure(error):
    """Every local-class failure feeds the expanding retry floor, DNS included.
    A resolver that cannot answer (gaierror of ANY errno: a Pi with its network
    down reports EAI_NONAME, -2, not only EAI_AGAIN) is a client-side condition
    far more often than a provider losing its name; treating it as a provider
    failure flapped the fallback chain and tripled the log on Linux
    (tests/test_radar_v62.py::test_simulated_outage_hour[Site])."""
    return failure_class(error) == 'local'


# Only active lookups are shared. A resolver that never returns occupies one of
# four process-wide slots across pool replacement; it cannot spawn successors.
_dns_lock = threading.Lock()
_dns_jobs = {}


def _shared_resolve(session, key, event):
    with _dns_lock:
        job = _dns_jobs.get(key)
        if job is not None:
            job.append((weakref.ref(session), event))
            return
        if len(_dns_jobs) >= 4:
            session._dns_errors[key] = LocalTransportError('radar resolver capacity')
            session._resolving.pop(key, None)
            event.set()
            return
        _dns_jobs[key] = [(weakref.ref(session), event)]
    def run():
        addresses, error = None, None
        try:
            addresses = socket.getaddrinfo(*key, socket.AF_INET, socket.SOCK_STREAM)
            if not addresses:
                raise socket.gaierror('no radar IPv4 addresses')
        except OSError as caught:
            error = caught
        with _dns_lock:
            subscribers = _dns_jobs.pop(key)
        for reference, done in subscribers:
            owner = reference()
            if owner is not None:
                with owner._condition:
                    if not owner._closed:
                        if error is not None:
                            owner._dns_errors[key] = error
                        else:
                            owner.addresses[key] = addresses
                            owner._resolved_at[key] = time.monotonic()
                    owner._resolving.pop(key, None)
            done.set()
    threading.Thread(target=run, name='radar-dns', daemon=True).start()


class WarmUnavailable(LocalTransportError):
    pass


class _WarmLease:
    def __init__(self, session, key, conn):
        self.session, self.key, self.conn = session, key, conn
        self.consumed = False

    def close(self):
        with self.session._condition:
            if not self.consumed:
                self.consumed = True
                self.session._release(self.key, self.conn)


class _StaleFirstByteTimeout(AmbiguousTransportError):
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
        if count and getattr(self.conn, "attempt", None):
            self.conn.attempt.progress()
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
        self.attempt = None
        self.pool = None
        self.warm_only = False
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
        if self.warm_only:
            raise WarmUnavailable('hedge requires an already connected socket')
        pool = self.pool
        key = (self.host, self.port)
        if pool is None:
            return self._connect()
        with pool._condition:
            while pool._connecting.get(key, 0) >= pool.MAX_HANDSHAKES:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise LocalTransportError('radar handshake admission deadline')
                if self.attempt:
                    self.attempt.check()
                pool._condition.wait(min(remaining, .05))
            pool._connecting[key] = pool._connecting.get(key, 0) + 1
        try:
            return self._connect()
        finally:
            with pool._condition:
                pool._connecting[key] -= 1
                pool._condition.notify_all()

    def _connect(self):
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
                if getattr(self, "attempt", None):
                    self.attempt.attach(sock)
                _tcp_keepalive(sock)
                sock.settimeout(remaining)
                sock.connect(address)
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout('radar TLS exceeded deadline')
                sock.settimeout(remaining)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host,
                    do_handshake_on_connect=False,
                    session=self.pool._tls_sessions.get((self.host, self.port)) if self.pool else None)
                if getattr(self, "attempt", None):
                    self.attempt.attach(self.sock)
                self.sock.do_handshake()
                return
            except OSError as error:
                failure = (LocalTransportError('radar connection/TLS setup timed out: '+str(error))
                           if isinstance(error, socket.timeout) else error)
                sock.close()
                if self.sock is not None:
                    self.sock.close()
                    self.sock = None
                if time.monotonic() >= end:
                    raise failure from error
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
    MAX_HANDSHAKES = 2
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
        self._connecting = {}
        self._tls_contexts = {}
        self._tls_sessions = {}
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
            _shared_resolve(self, key, event)
        if cached is not None:
            return cached  # stale-while-refresh: first tile never waits for DNS
        if not event.wait(max(0, end - time.monotonic())):
            raise ResolverTimeout('radar DNS exceeded request deadline')
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
            control = vars(conn).get("attempt")
            if control is not None:
                with control.lock:
                    control.sock = None
            self._busy.discard(conn)
            if broken or self._closed:
                conn.close()
                conns = self.connections.get(key, [])
                if conn in conns:
                    conns.remove(conn)
                self._used.pop(conn, None)
                self._idle_sec.pop(conn, None)
            else:
                sock = conn.sock
                if isinstance(sock, ssl.SSLSocket) and sock.session.has_ticket:
                    self._tls_sessions[key] = sock.session
                self._used[conn] = time.monotonic()
                self._idle_sec[conn] = self.IDLE_SEC if idle_sec is None else idle_sec
            self._condition.notify_all()

    def reserve_hedge(self, url):
        """Atomically reserve an idle, connected lease; never DNS/connect/wait."""
        parsed = urlsplit(url)
        key = (parsed.hostname, parsed.port or 443)
        with self._condition:
            if self._closed:
                return None
            self._expire()
            conn = next((c for c in self.connections.get(key, ())
                         if c not in self._busy and c.sock is not None), None)
            if conn is None:
                return None
            self._busy.add(conn)
            return _WarmLease(self, key, conn)

    def open(self, req, timeout):
        url = urlsplit(req.full_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('radar requires an HTTPS host')
        control = getattr(req, "radar_attempt", None)
        key = (url.hostname, url.port or 443)
        queued_at = time.monotonic()
        end = min(queued_at + timeout, self._pass_deadline)
        for attempt in range(1 if control is not None else 2):
            if time.monotonic() >= end:
                raise TimeoutError('radar pass exceeded deadline')
            if control is not None:
                control.check()
            lease = getattr(control, 'warm_lease', None)
            if control is not None and control.hedged and lease is None:
                raise WarmUnavailable('no warm radar hedge lease')
            addresses = None if lease else self._addresses_for(key, end)
            with self._condition:
                while True:
                    if lease is not None:
                        if lease.session is not self or lease.key != key or lease.consumed:
                            raise WarmUnavailable('invalid radar hedge lease')
                        lease.consumed = True
                        conn, reused = lease.conn, True
                        remaining = end - time.monotonic()
                        break
                    if self._closed:
                        raise OSError('radar session closed')
                    self._expire()  # also expire sockets released during pool waits
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise LocalTransportError('radar DNS/pool exceeded request deadline')
                    conns = self.connections.setdefault(key, [])
                    idle = next((c for c in conns if c not in self._busy), None)
                    fresh = attempt or (control is not None and control.fresh)
                    limit = self.MAX_CONNECTIONS
                    conn = idle if not fresh else None
                    if fresh and len(conns) >= limit and idle is not None:
                        # A retry needs a fresh socket. Evict an idle lease only
                        # if another caller has already taken the freed slot.
                        self._release(key, idle, broken=True)
                    reused = conn is not None
                    if conn is None and len(conns) < limit:
                        conn = _Connection(*key, addresses, remaining)
                        conn.pool = self
                        # One verification context per host makes TLS tickets reusable.
                        conn._context = self._tls_contexts.setdefault(key, conn._context)
                        conns.append(conn)
                    if conn is not None:
                        self._busy.add(conn)
                        break
                    self._condition.wait(remaining)
            conn.warm_only = bool(control is not None and control.hedged)
            conn.attempt = control
            conn.timeout = remaining
            conn.deadline = end
            conn.first_byte_end = None
            conn.response_bytes = 0
            try:
                if control is not None:
                    control.check()
                    if conn.sock:
                        control.attach(conn.sock)
                if conn.sock:
                    conn.sock.settimeout(remaining)
                req.radar_queue_wait = time.monotonic()-queued_at
                conn.request(req.get_method(), url.path + ('?' + url.query if url.query else ''),
                             headers=dict(req.header_items()))
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('radar request exceeded deadline')
                if reused and (control is None or not control.hedged):
                    conn.first_byte_end = min(end, time.monotonic() + self.first_byte_timeout)
                if conn.sock:
                    conn.sock.settimeout(min(remaining, self.first_byte_timeout) if reused and (control is None or not control.hedged) else remaining)
                if control is not None:
                    control.waiting_response = True
                response = conn.getresponse()
                if response.status != 200:
                    response.close()
                    raise HTTPError(req.full_url, response.status, response.reason, response.headers, None)
                return _Response(self, key, conn, response)
            except Exception as error:
                self._release(key, conn, broken=True)
                if req.get_method() in ('GET','HEAD') and reused and not conn.response_bytes and is_transport_error(error) and not isinstance(error, LocalTransportError):
                    error = AmbiguousTransportError(str(error)) if not isinstance(error, _StaleFirstByteTimeout) else error
                if (control is not None or attempt or not reused or req.get_method() not in ('GET', 'HEAD') or
                        not is_transport_error(error) or conn.response_bytes or
                        time.monotonic() >= end):
                    raise error
                first_byte = isinstance(error, _StaleFirstByteTimeout)
                failure = getattr(req, 'radar_retry_failure', None)
                if failure is not None:
                    failure(error)
                try:
                    check = getattr(req, 'radar_retry_check', None)
                    if check is not None:
                        check()
                    if self.on_retry is not None:
                        # The caller's rate/cooldown gate also covers retries.
                        self.on_retry(end, first_byte=first_byte)
                except Exception:
                    req.radar_gate_failed = True  # failed attempt already sampled
                    raise
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
        with _dns_lock:
            for key, subscribers in _dns_jobs.items():
                _dns_jobs[key] = [(ref, event) for ref, event in subscribers if ref() is not self and ref() is not None]
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
