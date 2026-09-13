# Offline radar geography

Basemap: Natural Earth, public domain, 1:10m vectors. The appliance makes **no
external basemap requests** and imports no geo packages. It serves locally
created class-only SVGs using its existing static server.

Bundled artifact: **3,733,546 bytes (3.56 MiB)**. SHA-256:
`1381f42a3ad8a9c1e3d6a3e15d4bac086e003a0cc21183619523019cbb1a993f`.
An offline rebuild with the cached source ZIPs was byte-identical.

## Rebuild (maintainer workstation only)

From the repository root:

```sh
python -m venv /tmp/wfp-basemap-build-venv
/tmp/wfp-basemap-build-venv/bin/pip install shapely==2.1.2 pyshp==2.3.1
/tmp/wfp-basemap-build-venv/bin/python tools/build_radar_basemap.py \
  --cache /tmp/wfp-natural-earth --download
```

Omit `--download` to rebuild entirely offline from those cached ZIPs. Only
`lib/data/radar-natural-earth.bin` is shipped; do not add the source ZIPs or the
build environment to the repository. Source URLs and each ZIP's SHA-256 are
embedded in the artifact manifest. To reproduce an existing artifact exactly,
use source ZIPs with those checksums and the pinned build tools; upstream URLs
can change. The builder prints byte size and refuses to write >=5,000,000 bytes.

Sources: Natural Earth's [physical vectors](https://www.naturalearthdata.com/downloads/10m-physical-vectors/)
and [cultural vectors](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/).
Layers: `land`, `coastline`, `lakes`, `admin_0_boundary_lines_land`,
`admin_1_states_provinces_lines`, `roads`, `roads_north_america`. Ocean is the
complement of retained land. This is cartographic context, not authoritative
political boundaries or street navigation.

## Deliberate generalization

* Douglas–Peucker tolerance 0.003 degrees, before binning; topology preserved
  for polygons. Final runtime simplification is one plate pixel.
* Land islands and lakes smaller than one square Mercator pixel at zoom 4 are
  omitted (area adjusted for latitude, capped near the pole). Closed coastline
  rings use the same filter. Tiny islands therefore remain omitted when zoomed
  in. Natural Earth is a generalized atlas, not survey-resolution coast data.
* Global roads: `type=Major Highway`, scale rank <=6. North America supplement:
  Interstate/Federal/State class AND Freeway/Tollway/Primary type. No local
  streets, ferry routes, trails, winter roads or minor highways.
* Coordinates quantized to 0.001 degrees (~111 m latitude). Geography is useful
  at zoom 4–9; source generalization remains visible at the closest view.
* Each feature is clipped to 1° cells at build time; polygon holes survive.
  Separate shoreline lines avoid artificial strokes along cell boundaries.

## Binary format and runtime

Little endian: eight-byte `NEBM0001` magic, uint32 JSON-manifest byte length,
manifest, uint32 compressed-index length, zlib index, then cell payloads.
The index has 360×180 pairs of uint32 `(offset, compressed_length)`, row-major
from (-180,-90). Offsets are relative to the payload start. Zero length and
offset zero means bare land; zero length and offset one means a full ocean cell.
Other cells are independent zlib streams. Each contains repeated uint8 layer,
uint16 ring count, then each ring's uint16 point count and uint16 x/y pairs,
relative to the cell southwest corner in 1/1000-degree units. Layers 0–6 are
ocean, lake, coast, admin0, admin1, global road, North America road. Line entries
have one ring. No names, attributes, keys or station locations are bundled.

Runtime keeps one 518,400-byte decompressed index and at most 256 decoded cells;
it never loads all global geometry. It unwraps cell longitudes on both sides
of the antimeridian and uses the same world projection and integer tile paste
offsets as `_radar_viewport`. Polygon rings use Sutherland–Hodgman and lines
use Liang–Barsky clipping, followed by integer quantization and iterative
Douglas–Peucker. The compound water path uses CSS even-odd filling for holes
and prevents alpha buildup along adjacent cells. Coordinates are viewport
pixels, and only `class`/`d` attributes appear on paths.

An SVG's first comment holds its `coast`/`roads` hints, so restart reuse needs no
sidecar and no geometry decode. `coast` means any water, including lakes;
`roads=dense` means visible North America supplement segments, `sparse` means
only global segments, `none` means no road survived viewport clipping. These
are coverage hints, not road counts.
