"""Tile races and host health, independent of the display and immutable caches."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import socket
import threading
import time
from urllib.parse import urlsplit


class CircuitOpen(OSError):
    pass


class AttemptCancelled(OSError):
    pass


class Attempt:
    def __init__(self, fresh=False, hedged=False):
        self.fresh = fresh
        self.hedged = hedged
        self.first_byte = threading.Event()
        self.cancelled = threading.Event()
        self.sock = None
        self.lock = threading.Lock()

    def check(self):
        if self.cancelled.is_set():
            raise AttemptCancelled('discarded radar attempt')

    def attach(self, sock):
        with self.lock:
            self.check()
            self.sock = sock

    def cancel(self):
        with self.lock:
            self.cancelled.set()
            if self.sock is not None:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


class HostHealth:
    WINDOW = 60
    COOLDOWN = 30

    def __init__(self):
        self.lock = threading.RLock()
        self.hosts = {}
        self.sources = {}
        self.hedges = self.retries = self.discarded = 0
        self.last_success = None
        self.last_error = None

    def _host(self, source, url):
        host = urlsplit(url).netloc
        self.sources.setdefault(source, set()).add(host)
        state = self.hosts.setdefault(host, dict(samples=deque(), until=0, probe=False,
                                                url=url, metadata=False))
        self._prune(state)
        return state

    def _prune(self, state):
        now = time.monotonic()
        while state['samples'] and now-state['samples'][0][0] >= self.WINDOW:
            state['samples'].popleft()

    def state(self, s):
        if s['probe']:
            return 'half'
        return 'open' if s['until'] else 'closed'

    def admit(self, source, url, metadata=False):
        with self.lock:
            s = self._host(source, url)
            if metadata:
                s.update(url=url, metadata=True)
            if s['until']:
                if time.monotonic() < s['until'] or s['probe'] or not metadata:
                    raise CircuitOpen('radar host circuit open: '+urlsplit(url).netloc)
                s['probe'] = True
                return True
            return False

    def record(self, source, url, success, error=None, probe=False):
        with self.lock:
            s = self._host(source, url)
            if error is not None:
                self.last_error = str(error) or type(error).__name__
            if probe:
                s['probe'] = False
                s['until'] = 0 if success else time.monotonic()+self.COOLDOWN
                s['samples'].clear()
            s['samples'].append((time.monotonic(), bool(success)))
            if not s['until'] and len(s['samples']) >= 6:
                if sum(ok for _, ok in s['samples']) / len(s['samples']) < .5:
                    s['until'] = time.monotonic()+self.COOLDOWN

    def probes(self, source):
        with self.lock:
            states = [self.hosts[h] for h in self.sources.get(source, ())]
            if any(s['until'] and (s['probe'] or time.monotonic() < s['until']) for s in states):
                raise CircuitOpen('radar source host circuit open: '+source)
            return [(s['url'], s['metadata']) for s in states if s['until']]

    def probe_delay(self, sources=None):
        with self.lock:
            hosts = self.hosts if sources is None else {
                h: self.hosts[h] for source in sources for h in self.sources.get(source, ())}
            delays = [max(0, s['until']-time.monotonic()) for s in hosts.values() if s['until']]
            return min(delays) if delays else None

    def snapshot(self):
        with self.lock:
            hosts = {}
            samples = []
            for host, s in self.hosts.items():
                self._prune(s)
                values = [ok for _, ok in s['samples']]
                samples.extend(values)
                hosts[host] = dict(breaker=self.state(s), samples60s=len(values),
                    successRate60s=sum(values)/len(values) if values else None)
            states = {h['breaker'] for h in hosts.values()}
            return dict(lastSuccessTs=self.last_success,
                successRate60s=sum(samples)/len(samples) if samples else None,
                hedges=self.hedges, retries=self.retries, discardedHedges=self.discarded,
                breaker='open' if 'open' in states else 'half' if 'half' in states else 'closed',
                lastError=self.last_error, hosts=hosts)


def tile_race(request, deadline, hedge, claim_hedge, discarded):
    """At most two attempts; winning bytes alone leave this function."""
    controls = [Attempt()]
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='radar-attempt') as pool:
        pending = {pool.submit(request, controls[0], False)}
        started = time.monotonic()
        second = False
        error = None
        try:
            while pending:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('radar tile race exceeded deadline')
                due = started+hedge-time.monotonic() if hedge is not None and not second else remaining
                done, pending = wait(pending, timeout=max(0, min(remaining, due)), return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        result = future.result()
                        if pending:
                            discarded(len(pending))
                        return result
                    except Exception as caught:
                        error = caught
                if not second:
                    failed = not pending
                    eligible = hedge is not None and not controls[0].first_byte.is_set()
                    if failed or (eligible and claim_hedge()):
                        # A hedge is also the tile's sole retry; failures never
                        # create a third request after an overlapping attempt.
                        second = True
                        controls.append(Attempt(fresh=True, hedged=not failed))
                        pending.add(pool.submit(request, controls[-1], True))
                    elif hedge is not None:
                        hedge = None
            raise error
        finally:
            for control in controls:
                control.cancel()
