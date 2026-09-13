"""One IPv4 DNS resolution and reusable TLS connection per host per radar pass."""
import http.client
import socket
import time
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


class RadarSession:
    def __init__(self):
        self.addresses = {}
        self.connections = {}

    def open(self, req, timeout):
        url = urlsplit(req.full_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('radar requires an HTTPS host')
        key = (url.hostname, url.port or 443)
        started = time.monotonic()
        if key not in self.addresses:
            try:
                self.addresses[key] = socket.getaddrinfo(*key, socket.AF_INET, socket.SOCK_STREAM)
            except OSError as error:
                self.addresses[key] = error  # a broken resolver costs once, not N requests
        addresses = self.addresses[key]
        if isinstance(addresses, Exception):
            raise addresses
        timeout -= time.monotonic() - started
        if timeout <= 0:
            raise TimeoutError('radar DNS exceeded request deadline')
        conn = self.connections.get(key)
        if conn is None:
            conn = self.connections[key] = _Connection(*key, addresses, timeout)
        conn.timeout = timeout
        if conn.sock:
            conn.sock.settimeout(timeout)
        try:
            conn.request(req.get_method(), url.path + ('?' + url.query if url.query else ''),
                         headers=dict(req.header_items()))
            response = conn.getresponse()
            if response.status != 200:
                # No unbounded error-body drain; reconnect on next candidate using
                # cached DNS. In particular a 503 does not fetch the remaining tiles.
                response.close()
                conn.close()
                raise HTTPError(req.full_url, response.status, response.reason, response.headers, None)
            return response
        except Exception:
            conn.close()
            raise

    def discard(self, url):
        """A truncated/oversized response cannot leave bytes on a reusable socket."""
        parsed = urlsplit(url)
        conn = self.connections.get((parsed.hostname, parsed.port or 443))
        if conn:
            conn.close()

    def close(self):
        for conn in self.connections.values():
            conn.close()
