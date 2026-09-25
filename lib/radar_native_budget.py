"""Native Level III policy, device default and restart-safe UTC body-byte ledger."""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from pathlib import Path

NATIVE_NEWEST_ONLY_BYTES = 150_000_000
NATIVE_PAUSE_BYTES = 250_000_000


def read_device_model():
    try:
        return Path('/proc/device-tree/model').read_text().rstrip('\0\n')
    except (OSError, UnicodeError):
        return ''


@lru_cache(maxsize=None)
def default_renderer(model_reader=read_device_model):
    """Read once per process; an injected reader makes device policy testable."""
    model = model_reader()
    return 'v1' if any(name in model for name in ('Raspberry Pi 3', 'Compute Module 3', 'Raspberry Pi Zero 2', 'BCM2837')) else 'v2'


def render_preference(path, model_reader=read_device_model):
    try:
        raw = Path(path).read_text()[:128].strip()
        if raw in ('v1', 'v2'):
            return raw
    except (OSError, UnicodeError):
        pass
    return default_renderer(model_reader)


def native_allowed(requested, tier, ceiling_state):
    return requested and tier in ('live', 'warm') and ceiling_state != 'paused'


class NativeBudget:
    """Short in-memory accounting lock; serialized, debounced durable writes.

    Call persist outside the renderer lock. Thresholds and forward UTC rollovers
    flush immediately; other changes are written at most once per 60 seconds.
    Failed writes retry with bounded backoff. Native remains memory-metered under
    the same byte ceilings; ledger health is independent of the daily limit.
    """
    def __init__(self, path, clock=time.time, monotonic=time.monotonic):
        self.path, self.clock, self.monotonic = Path(path), clock, monotonic
        self.lock = threading.RLock()
        self.persist_lock = threading.Lock()
        self.day, self.bytes = '', 0
        self.clock_day = None
        self.failed = False
        self.retry_at = 0.
        self.write_failures = 0
        self.saved = None
        self.last_write = None
        try:
            record = json.loads(self.path.read_text())
            if (isinstance(record['day'], str) and
                    datetime.strptime(record['day'], '%Y-%m-%d').strftime('%Y-%m-%d') == record['day'] and
                    type(record['bytes']) is int and record['bytes'] >= 0):
                self.day, self.bytes = record['day'], record['bytes']
                self.saved = (self.day, self.bytes, self._state())
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _state(self):
        return ('paused' if self.bytes > NATIVE_PAUSE_BYTES else
                'newest-only' if self.bytes > NATIVE_NEWEST_ONLY_BYTES else 'normal')

    def _rollover(self):
        now = self.clock()
        clock_day = int(now // 86400)
        if clock_day == self.clock_day:
            return
        today = datetime.fromtimestamp(now, timezone.utc).date()
        day, tomorrow = today.isoformat(), (today + timedelta(days=1)).isoformat()
        if day > self.day or self.day > tomorrow:
            self.day, self.bytes = day, 0
        self.clock_day = clock_day

    def snapshot(self):
        with self.lock:
            self._rollover()
            return dict(day=self.day, bytesToday=self.bytes, ceilingState=self._state(),
                        ledgerState='retrying' if self.failed else 'ok')

    def add(self, count):
        if count <= 0:
            return
        with self.lock:
            self._rollover()
            self.bytes += count
        self.persist()

    def persist(self):
        # Workers never queue behind SD I/O. The next worker/tick picks up bytes
        # that arrived during a write; a crossed threshold is drained below.
        if not self.persist_lock.acquire(blocking=False):
            return
        temporary = None
        try:
            while True:
                with self.lock:
                    self._rollover()
                    current = (self.day, self.bytes, self._state())
                    urgent = self.saved is None or current[0] != self.saved[0] or current[2] != self.saved[2]
                    if self.failed and self.monotonic() < self.retry_at:
                        return
                    if not self.failed and (current == self.saved or (not urgent and self.last_write is not None and
                            self.monotonic()-self.last_write < 60)):
                        return
                target = self.path.resolve()
                temporary = target.with_name(target.name+'.tmp')
                with temporary.open('w') as stream:
                    json.dump(dict(day=current[0], bytes=current[1]), stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                with self.lock:
                    self.failed = False
                    self.write_failures = 0
                    self.saved = current
                    self.last_write = self.monotonic()
        except Exception as error:
            # Accounting must never replace a response or its original failure.
            with self.lock:
                first_failure = not self.failed
                self.failed = True
                self.write_failures += 1
                self.retry_at = self.monotonic() + min(300, 5 * 2**min(self.write_failures-1, 6))
            if first_failure:
                logging.getLogger(__name__).warning('Native radar ledger unavailable; retrying, bytes counted in memory: %s', error)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            self.persist_lock.release()
