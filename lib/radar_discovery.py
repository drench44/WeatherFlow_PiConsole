"""Readiness predictions in wall time; the emitter owns one cancellable wakeup."""
from statistics import median


class DiscoverySchedule:
    FAST_POLLS = 6
    BACKOFF = 120

    def __init__(self):
        self.identity = None
        self.expected = None
        self.due = None
        self.polls = 0
        self.interval = 20
        self.last_poll = None

    def observe(self, snap, now, ready_lag):
        if snap.ts_frame is None:
            return
        identity = (snap.source_id, snap.site_id, snap.ts_frame)
        if identity == self.identity:
            if now < self.expected:
                self.due = self.expected  # an early recovery probe keeps the scan's phase
            return
        cadence = snap.cadence
        if snap.source_mode == 'site':
            stamps = sorted({f['ts'] for f in snap.frames})
            gaps = [b-a for a, b in zip(stamps, stamps[1:]) if b > a][-4:]
            cadence = max(300, min(600, median(gaps))) if gaps else 300
        lag = ready_lag if snap.source_id == 'iem-mrms-lcref' else 0
        self.identity = identity
        self.interval = 30 if snap.source_mode == 'site' else 20 if snap.source_id == 'iem-mrms-lcref' else 25
        self.expected = snap.ts_frame + cadence + lag
        self.due = max(now + 1, self.expected)
        self.polls = 0

    def started(self, now):
        self.last_poll = now
        self.polls += 1
        self.due = now + (self.interval if self.polls < self.FAST_POLLS else self.BACKOFF)

    def telemetry(self, now, stamp):
        return dict(expectedReadyTs=self.expected, nextPollTs=self.due,
                    lastPollTs=self.last_poll, fastPolls=min(self.polls, self.FAST_POLLS),
                    backingOff=self.polls >= self.FAST_POLLS,
                    ageSec=max(0, int(now-stamp)) if stamp is not None else None)
