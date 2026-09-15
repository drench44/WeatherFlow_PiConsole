"""Process-owned validated tile inventory. Disk is read once, never polled.

Entries are immutable; the owner serializes writes/evictions with its radar lock.
The boot scanner yields between bounded chunks on a dedicated thread.
"""
from collections import OrderedDict, defaultdict
from pathlib import Path
import os
import time


class TileInventory:
    MAX_FILES = 8000
    MAX_BYTES = 64_000_000
    MAX_ENTRIES = 40000

    def __init__(self):
        self.records = OrderedDict()
        self.bytes = 0
        self.generation = 0
        self.groups = defaultdict(int)
        self.group_counts = defaultdict(int)
        self.directories = set()
        self.startup = {}
        self.writable = True

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

    def scan(self, root, validate):
        """At most 40k directory entries / 8k files / 64MB, in 32-file quanta.

        Only the installed revision is passed in. Unindexed files are misses and
        may be overwritten normally. No symlinks are followed.
        """
        started, cpu = time.perf_counter(), time.thread_time()
        visited = loaded = invalid = 0
        stack = [(Path(root), 0)]
        try:
            while stack and visited < self.MAX_ENTRIES and len(self) < self.MAX_FILES and self.bytes < self.MAX_BYTES:
                directory, depth = stack.pop()
                try:
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            visited += 1
                            if visited > self.MAX_ENTRIES:
                                break
                            if entry.is_dir(follow_symlinks=False) and depth < 5:
                                stack.append((Path(entry.path), depth+1))
                            elif depth == 5 and entry.name.endswith('.png') and entry.is_file(follow_symlinks=False):
                                path = Path(entry.path)
                                try:
                                    source, site, stamp, z, x, y = path.relative_to(root).parts
                                    key = (source, None if site == '-' else site, stamp, int(z), int(x), int(y[:-4]))
                                    length = entry.stat(follow_symlinks=False).st_size
                                    if length > 2*1024*1024 or self.bytes+length > self.MAX_BYTES:
                                        continue
                                    metadata = validate(path, source)
                                    self.add(key, path, length, metadata)
                                    loaded += 1
                                except (OSError, ValueError, KeyError, TypeError):
                                    invalid += 1
                                    path.unlink(missing_ok=True)
                                if (loaded+invalid) % 32 == 0:
                                    time.sleep(.001)
                                if len(self) >= self.MAX_FILES:
                                    break
                except OSError:
                    continue
        finally:
            self.startup = dict(entries=visited, files=loaded, invalid=invalid,
                wallSec=time.perf_counter()-started, cpuSec=time.thread_time()-cpu,
                bounded=bool(stack) or visited >= self.MAX_ENTRIES)
            self.writable = not self.startup['bounded']
