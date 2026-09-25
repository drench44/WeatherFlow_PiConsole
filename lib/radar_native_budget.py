"""Native Level III policy, device default and restart-safe UTC body-byte ledger."""
import json
import os
import threading
import time
from datetime import datetime, timezone
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
    return 'v1' if 'Raspberry Pi 3' in model_reader() else 'v2'


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
    """One engine owns the ledger; its concurrent transport workers share a lock.

    Count received response-body bytes, including partial/invalid products and
    listings. Cached reads and 304 responses add zero. Persist every response.
    """
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.lock = threading.RLock()
        self.day, self.bytes = '', 0
        try:
            record = json.loads(self.path.read_text())
            if isinstance(record['day'], str) and type(record['bytes']) is int and record['bytes'] >= 0:
                self.day, self.bytes = record['day'], record['bytes']
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _rollover(self):
        day = datetime.fromtimestamp(self.clock(), timezone.utc).strftime('%Y-%m-%d')
        if day != self.day:
            self.day, self.bytes = day, 0

    def snapshot(self):
        with self.lock:
            self._rollover()
            state = ('paused' if self.bytes > NATIVE_PAUSE_BYTES else
                     'newest-only' if self.bytes > NATIVE_NEWEST_ONLY_BYTES else 'normal')
            return dict(day=self.day, bytesToday=self.bytes, ceilingState=state)

    def add(self, count):
        if count <= 0:
            return
        with self.lock:
            self._rollover()
            self.bytes += count
            target = self.path.resolve()
            temporary = target.with_name(target.name+'.tmp')
            try:
                with temporary.open('w') as stream:
                    json.dump(dict(day=self.day, bytes=self.bytes), stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            except OSError:
                # An unwritable ledger must not grant an unmetered session.
                self.bytes = max(self.bytes, NATIVE_PAUSE_BYTES+1)
                raise
            finally:
                temporary.unlink(missing_ok=True)
