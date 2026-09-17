"""Process-owned validated tile inventory. Disk is read once, never polled.

Entries are immutable; the owner serializes writes/evictions with its radar lock.
The boot scanner yields between bounded chunks on a dedicated thread.
"""
from collections import OrderedDict, defaultdict
from pathlib import Path
import os
import shutil
import time


class TileInventory:
    # Class defaults are the floor. An instance built with a root derives its
    # own caps from free space (see configure); the file ceiling is set by the
    # boot validation cost (every cached PNG is opened once at startup on the
    # Pi's inventory thread, ~5 ms each), not by disk.
    MAX_FILES = 8000
    MAX_BYTES = 64_000_000
    MAX_ENTRIES = 40000
    FREE_SPACE_SHARE = 0.02
    MAX_BYTES_CEILING = 256_000_000
    MAX_FILES_CEILING = 16000
    TYPICAL_TILE_BYTES = 8192

    def __init__(self, root=None):
        self.records = OrderedDict()
        self.bytes = 0
        self.generation = 0
        self.groups = defaultdict(int)
        self.group_counts = defaultdict(int)
        self.directories = set()
        self.startup = {}
        self.writable = True
        if root is not None:
            self.configure(root)

    def configure(self, root):
        """Size the cache to the disk it lives on: a share of free space, never
        below the class floor, never above the ceiling the boot scan can
        validate in time. Entries scale with files so a full cache is never a
        bounded scan. Falls back to the floor when the disk cannot be asked."""
        try:
            probe = Path(root)
            while not probe.exists() and probe != probe.parent:
                probe = probe.parent
            free = shutil.disk_usage(probe).free
        except OSError:
            free = 0
        floor = type(self)
        self.MAX_BYTES = int(min(floor.MAX_BYTES_CEILING, max(floor.MAX_BYTES, free*floor.FREE_SPACE_SHARE)))
        self.MAX_FILES = int(min(floor.MAX_FILES_CEILING, max(floor.MAX_FILES, self.MAX_BYTES//floor.TYPICAL_TILE_BYTES)))
        self.MAX_ENTRIES = max(floor.MAX_ENTRIES, self.MAX_FILES*3)
        return dict(maxFiles=self.MAX_FILES, maxBytes=self.MAX_BYTES, maxEntries=self.MAX_ENTRIES, free=free)

    def __contains__(self, key):
        return key in self.records

    def __len__(self):
        return len(self.records)

    def add(self, key, path, length, metadata):
        self.discard(key)
        self.records[key] = (Path(path), length, metadata)
        self.group_counts[key[:4]] += 1
        self.bytes += length
        self.generation += 1
        self.groups[key[:4]] = self.generation
        self.directories.add(str(Path(path).parent))

    def discard(self, key):
        record = self.records.pop(key, None)
        if record is not None:
            self.bytes -= record[1]
            self.generation += 1
            self.groups[key[:4]] = self.generation
            self.group_counts[key[:4]] -= 1
            if not self.group_counts[key[:4]]:
                self.group_counts.pop(key[:4],None)
                self.groups.pop(key[:4],None)
        return record

    def evict(self, pinned=(), incoming_size=0, incoming_files=0):
        if len(self)+incoming_files <= self.MAX_FILES and self.bytes+incoming_size <= self.MAX_BYTES:
            return
        # At pressure only: visit victims + the bounded displayed pins. No
        # filesystem inventory/stat/sort, or scan of all cached records.
        remaining = len(self.records)
        while remaining and (len(self)+incoming_files > self.MAX_FILES or self.bytes+incoming_size > self.MAX_BYTES):
            remaining -= 1
            key = next(iter(self.records))
            if key in pinned:
                self.records.move_to_end(key)
                continue
            record = self.records[key]
            record[0].unlink(missing_ok=True)
            self.discard(key)
            path = record[0]
            root = path.parents[5] if len(path.parents)>5 and path.parents[4].name==key[0] else path.parent
            parent = path.parent
            while parent != root:
                try: parent.rmdir()
                except OSError: break
                self.directories.discard(str(parent))
                parent = parent.parent

    def scan(self, root, validate, suffix=(), entry_limit=None):
        """Index the installed revision, newest stamp first, within MAX_ENTRIES
        directory entries in 32-file quanta. Then fit the disk to the caps:
        stamp directories a bounded scan never reached are removed (they are
        the oldest, and unindexed files could otherwise never be evicted), and
        the index evicts oldest-first down to MAX_FILES/MAX_BYTES. The cache is
        writable afterwards whatever the scan found: a full disk is a cache to
        trim, never a reason to render tiles and throw them away.

        No symlinks are followed. Unindexed leftovers inside a kept stamp
        directory are misses and may be overwritten normally.
        """
        limit = self.MAX_ENTRIES if entry_limit is None else max(0,entry_limit)
        started, cpu = time.perf_counter(), time.thread_time()
        visited = loaded = invalid = purged = 0
        first = len(self)
        stack = [(Path(root), 0)]
        try:
            while stack and visited < limit:
                directory, depth = stack.pop()
                try:
                    with os.scandir(directory) as entries:
                        children = []
                        for entry in entries:
                            visited += 1
                            if visited > limit:
                                break
                            if entry.is_dir(follow_symlinks=False) and depth < 5:
                                children.append(entry.path)
                            elif depth == 5 and entry.name.endswith('.png') and entry.is_file(follow_symlinks=False):
                                path = Path(entry.path)
                                try:
                                    source, site, stamp, z, x, y = path.relative_to(root).parts
                                    key = (source, None if site == '-' else site, stamp, int(z), int(x), int(y[:-4])) + suffix
                                    length = entry.stat(follow_symlinks=False).st_size
                                    if length > 2*1024*1024:
                                        raise ValueError('oversize cached tile')
                                    metadata = validate(path, source)
                                    self.add(key, path, length, metadata)
                                    loaded += 1
                                except (OSError, ValueError, KeyError, TypeError):
                                    invalid += 1
                                    path.unlink(missing_ok=True)
                                if (loaded+invalid) % 32 == 0:
                                    time.sleep(.001)
                        # LIFO: push ascending so the newest stamp pops first.
                        stack.extend((Path(child), depth+1) for child in sorted(children))
                except OSError:
                    continue
        finally:
            bounded = bool(stack) or visited >= limit
            if bounded:
                purged = self._purge_unindexed(Path(root))
            # Newest-first indexing leaves the oldest at the END; eviction pops
            # the front, so restore age order for this scan's records.
            if loaded:
                older = list(self.records.items())[:first]
                newer = list(self.records.items())[first:]
                self.records = OrderedDict(older + newer[::-1])
            before = len(self)
            self.evict()
            self.startup = dict(entries=visited, files=loaded, invalid=invalid, purgedDirs=purged,
                evicted=before-len(self), wallSec=time.perf_counter()-started,
                cpuSec=time.thread_time()-cpu, bounded=bounded)
            self.writable = True

    def _purge_unindexed(self, root):
        """Remove every <source>/<site>/<stamp> directory under root that holds
        no indexed tile. Only called after a bounded scan, when the remainder
        is by construction the oldest stamps."""
        kept = {record[0].parents[2] for record in self.records.values()
                if len(record[0].parents) > 2}
        purged = 0
        try:
            for source in os.scandir(root):
                if not source.is_dir(follow_symlinks=False):
                    continue
                for site in os.scandir(source.path):
                    if not site.is_dir(follow_symlinks=False):
                        continue
                    for stamp in os.scandir(site.path):
                        if stamp.is_dir(follow_symlinks=False) and Path(stamp.path) not in kept:
                            shutil.rmtree(stamp.path, ignore_errors=True)
                            self.directories = {d for d in self.directories if not d.startswith(stamp.path)}
                            purged += 1
        except OSError:
            pass
        return purged
