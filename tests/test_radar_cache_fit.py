"""The tile cache fits the disk to its caps at boot instead of freezing writes.

2026-09-16: the Pi 4 rebooted with 8,592 cached tiles against an 8,000 cap. The
boot scan stopped at the cap, marked the cache read-only for the life of the
process, and every tile after that was fetched, rendered and discarded once its
directory existed. The panel showed no radar for the evening and the pass log
said outcome=deferred error=None."""
from collections import namedtuple
from pathlib import Path
import pytest
from lib.radar_cache import TileInventory


def tile(root, source, site, stamp, z, x, y, size=100):
    path = root/source/site/stamp/str(z)/str(x)/f'{y}.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x'*size)
    return path


def fill(root, stamps, per_stamp=4, site='KATX'):
    for stamp in stamps:
        for i in range(per_stamp):
            tile(root, 'iem-nexrad-n0b', site, stamp, 8, 0, i)


def stamps_on_disk(root):
    return sorted(p.name for p in (root/'iem-nexrad-n0b'/'KATX').iterdir())


def test_overflowing_disk_is_trimmed_to_the_cap_and_stays_writable(tmp_path):
    root = tmp_path/'t'/'rev'
    stamps = [f'2026091600{h:02d}' for h in range(10)]  # oldest .. newest
    fill(root, stamps)                                    # 40 files
    cache = TileInventory(); cache.MAX_FILES = 24
    cache.scan(root, lambda *a: {})
    assert cache.writable
    assert len(cache) == 24 and cache.startup['evicted'] == 16 and cache.startup['files'] == 40
    assert not cache.startup['bounded'] and cache.startup['purgedDirs'] == 0
    assert stamps_on_disk(root) == stamps[4:]            # the oldest four stamps left the disk
    assert cache.bytes == 24*100
    # a later write is admitted by the same arithmetic the emitter uses
    assert len(cache)+1 > cache.MAX_FILES                # full: the emitter prunes then writes
    cache.evict(incoming_files=1)
    assert len(cache) == 23 and stamps_on_disk(root)[0] == stamps[4]


def test_bounded_scan_purges_what_it_never_reached(tmp_path):
    root = tmp_path/'t'/'rev'
    stamps = [f'2026091600{h:02d}' for h in range(10)]
    fill(root, stamps, per_stamp=2)
    cache = TileInventory(); cache.MAX_ENTRIES = 40      # ~4 stamps' worth of entries
    cache.scan(root, lambda *a: {})
    assert cache.startup['bounded'] and cache.writable
    kept = stamps_on_disk(root)
    assert kept and kept == stamps[-len(kept):]         # newest survive, oldest were purged
    assert cache.startup['purgedDirs'] == 10-len(kept)
    assert {k[2] for k in cache.records} <= set(kept)


def test_eviction_after_scan_is_oldest_first(tmp_path):
    root = tmp_path/'t'/'rev'
    stamps = ['202609160100', '202609160200', '202609160300']
    fill(root, stamps, per_stamp=1)
    cache = TileInventory(); cache.scan(root, lambda *a: {})
    assert [k[2] for k in cache.records] == stamps      # age order restored after newest-first indexing
    cache.MAX_FILES = 2; cache.evict()
    assert [k[2] for k in cache.records] == stamps[1:]


def test_caps_follow_free_space_between_floor_and_ceiling(tmp_path, monkeypatch):
    import lib.radar_cache as rc
    usage = namedtuple('usage', 'total used free')
    for free, files, size in ((1_000_000, 8000, 64_000_000),          # tiny disk: the floor
                              (110_000_000_000, 12000, 256_000_000),  # the Pi 4: the ceiling
                              (4_000_000_000, 9765, 80_000_000)):     # in between: 2 % of free
        monkeypatch.setattr(rc.shutil, 'disk_usage', lambda p, free=free: usage(free*2, free, free))
        cache = TileInventory(tmp_path/'missing'/'radar')             # a root that does not exist yet
        assert (cache.MAX_FILES, cache.MAX_BYTES) == (files, size), (free, cache.MAX_FILES, cache.MAX_BYTES)
        assert cache.MAX_ENTRIES >= cache.MAX_FILES*3
    assert TileInventory.MAX_FILES == 8000                            # class floor untouched


def test_unaskable_disk_uses_the_floor(tmp_path, monkeypatch):
    import lib.radar_cache as rc
    def boom(p): raise OSError('no statvfs')
    monkeypatch.setattr(rc.shutil, 'disk_usage', boom)
    cache = TileInventory(tmp_path)
    assert (cache.MAX_FILES, cache.MAX_BYTES) == (8000, 64_000_000)
