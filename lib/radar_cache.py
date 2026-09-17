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
    MAX_FILES_CEILING = 12000  # panel-measured 5 ms per validated tile at boot: ~60 s
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
        validate in ordinary use. The entry allowance bounds boot admission;
        unusually sparse layouts may still exhaust it before the file cap.
        Falls back to the floor when the disk cannot be asked."""
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
        return self.scan_roots(((root, suffix),), validate, entry_limit)

    def scan_roots(self, roots, validate, entry_limit=None):
        """Admit newest stamps across all sources, sites and render revisions.

        Directory discovery/cleanup must see the whole tree; MAX_ENTRIES bounds
        validation, not that metadata traversal. Only indexed files survive,
        including in a stamp where the budget expires. No symlinks are followed.
        Both render revisions share one budget and one oldest-first eviction.
        """
        limit = self.MAX_ENTRIES if entry_limit is None else max(0, entry_limit)
        started, cpu = time.perf_counter(), time.thread_time()
        visited = loaded = invalid = 0
        bounded = False
        roots = [(Path(root), suffix) for root, suffix in roots]
        stamps = []
        discovered = 0

        def discover():
            nonlocal visited, discovered, bounded
            discovered += 1
            if visited < limit:
                visited += 1
            else:
                bounded = True

        def children(path):
            try:
                with os.scandir(path) as entries:
                    return list(entries)
            except FileNotFoundError:
                return []

        for root, suffix in roots:
            if root.is_symlink():
                root.unlink()
                continue
            for source in children(root):
                discover()
                if not source.is_dir(follow_symlinks=False):
                    continue
                for site in children(source.path):
                    discover()
                    if not site.is_dir(follow_symlinks=False):
                        continue
                    for stamp in children(site.path):
                        discover()
                        if stamp.is_dir(follow_symlinks=False):
                            stamps.append((stamp.name, Path(stamp.path), root, suffix))

        # A DFS sorted within each site is not a global age order. In particular
        # an old smooth revision must never displace a new native revision.
        for _, stamp, root, suffix in sorted(stamps, key=lambda s: (s[0], str(s[1])), reverse=True):
            stack = [(stamp, 3)]
            while stack:
                if visited >= limit:
                    bounded = True
                    break
                directory, depth = stack.pop()
                for entry in children(directory):
                    if visited >= limit:
                        bounded = True
                        break
                    visited += 1
                    if entry.is_dir(follow_symlinks=False) and depth < 5:
                        stack.append((Path(entry.path), depth+1))
                    elif depth == 5 and entry.name.endswith('.png') and entry.is_file(follow_symlinks=False):
                        path = Path(entry.path)
                        try:
                            source, site, stamp_name, z, x, y = path.relative_to(root).parts
                            key = (source, None if site == '-' else site, stamp_name, int(z), int(x), int(y[:-4])) + suffix
                            length = entry.stat(follow_symlinks=False).st_size
                            if length > 2*1024*1024:
                                raise ValueError('oversize cached tile')
                            metadata = validate(path, source)
                            self.add(key, path, length, metadata)
                            loaded += 1
                        except (OSError, ValueError, KeyError, TypeError):
                            invalid += 1
                        if (loaded+invalid) % 32 == 0:
                            time.sleep(.001)

        purged = sum(self._purge_unindexed(root) for root, _ in roots)
        self.records = OrderedDict(sorted(self.records.items(), key=lambda item: item[0][2]))
        before = len(self)
        self.evict()
        self.startup = dict(entries=visited, discoveredEntries=discovered, files=loaded, invalid=invalid, purgedDirs=purged,
            evicted=before-len(self), wallSec=time.perf_counter()-started,
            cpuSec=time.thread_time()-cpu, bounded=bounded)
        self.writable = True

    def _purge_unindexed(self, root):
        """Reconcile the disk to the inventory, including partially indexed stamps.

        Visit only retained branches; remove unowned subtrees wholesale. Remove
        symlinks themselves, never their targets. A failed removal is an I/O
        error, not a successfully trimmed cache.
        """
        kept = {record[0] for record in self.records.values() if root in record[0].parents}
        directories = {parent for path in kept for parent in path.parents if root == parent or root in parent.parents}
        purged = 0

        def clean(directory, depth=0):
            nonlocal purged
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        path = Path(entry.path)
                        if entry.is_dir(follow_symlinks=False):
                            if path in directories:
                                clean(path, depth+1)
                            else:
                                # Count omitted stamps even when deleting their
                                # entire site/source branch in a single operation.
                                if depth < 2:
                                    clean(path, depth+1)
                                else:
                                    purged += depth == 2
                                shutil.rmtree(path)
                        elif path not in kept:
                            path.unlink(missing_ok=True)
            except FileNotFoundError:
                return

        clean(root)
        self.directories = {str(record[0].parent) for record in self.records.values()}
        return purged
