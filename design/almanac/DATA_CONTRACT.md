# Almanac overlay — live data contract

The console (data engine) writes this JSON to `wx.json`; the overlay HTML polls it
(~every 2 s) and updates text + gauges. Flat object, display-ready primitives
(numbers/strings), **not** the app's `[value, unit, ...]` lists. Missing/None → `null`;
the HTML shows an em-dash for null. Emitter converts from the app's
`Obs`/`Astro`/`Met`/`Sager`/`System` DictProperties (see the field map in the code-explorer notes / lib/properties.py).

```jsonc
{
  // ——— Freshness. Three independent clocks, and conflating them is how a dead
  // station looked healthy: "ts" only proves the emit tick ran, "obsAgeSec"
  // proves the station is still reporting, and the per-provider ages prove a
  // network fetch still succeeds. All ages are whole seconds, null = never.
  "ts": 1750000000,                 // engine heartbeat: epoch seconds when written
  "obsTs": 1749999940,              // epoch of the newest OUTDOOR observation (obs_st / obs_out_air)
  "obsAgeSec": 60,                  // now - obsTs; from ENGINE START while obsTs is still null
                                    // (a never-heard station ages like a silent one). Past ~5 min the console's masthead reads
                                    // "SILENT" (as against "STALE" for a stalled feed) and
                                    // /health reports status "degraded" — the numbers are
                                    // cached, however fresh "ts" looks. An indoor Air is ignored
                                    // here on purpose: it must not mask a dead Tempest.
  "station": "Seattle",             // [Station] Name
  "date": "Fri, 31 Jul 2026",       // System['date']
  "time": "12:52",                  // System['time']  (HH:MM)
  "dayStartTs": 1749970800,         // station-local midnight today, epoch seconds (the hero curve's day axis)

  // Temperature
  "temp": 64.0, "tempUnit": "°F",   // Obs['outTemp'][0],[1]
  "feelsLike": 64, "feelsDesc": "Warm",   // Obs['FeelsLike'][0],[2] with the core's "Feeling " prefix dropped
  "tempTrendPerHr": 4.6,            // Obs['outTempTrend'][0]  (+ = rising)
  "temp24hDelta": 4.9,             // vs this time yesterday (+ = warmer); null if unknown
  "obsLow": 49.8,  "obsLowTime": "06:15",   // Obs['outTempMin'][0],[2]
  "obsHigh": 64.8, "obsHighTime": "08:04",  // Obs['outTempMax'][0],[2]
  "fcLow": 50, "fcHigh": 82,        // forecast today low/high (Met[...] lowTemp/highTemp)
  "humidity": 67, "dewPoint": 52.9, // Obs['Humidity'][0], Obs['DewPoint'][0]

  // Conditions / short-term forecast
  "conditions": "Clear & Sunny",           // Met['Conditions']
  "conditionsNote": "Clear until 02:00 tomorrow",
  "fcHour": "10:00", "fcWind": "0 mph NW", "fcPrecipPct": 0, "fcDailyPct": 0,
  // 7-day outlook (Open-Meteo daily, hourly refresh; [] hides the band).
  // hi/lo are whole degrees in the console's own temp unit; code is the WMO
  // weather code; pp is max precipitation probability for the day.
  // The TODAY row's hi/lo are overridden with fcLow/fcHigh (WeatherFlow) when
  // known, so the hero and the band never disagree about today.
  // qpf is the day's expected precipitation in rainUnit (Open-Meteo is asked
  // for inch when the station is configured in inches, mm otherwise); null
  // when unknown. The band prints it after the chance and hides it below
  // what the unit can show (0.01 in / 0.1 mm). gust is km/h, a fixed unit.
  "fcDaily": [{"day": "SAT", "date": "2026-08-29", "today": true, "hi": 64, "lo": 54, "code": 95, "pp": 95, "qpf": 0.34, "gust": 38}],
  // Hourly forecast temperature (Open-Meteo, the SAME fetch as fcDaily), from
  // the current station-local hour through the end of tomorrow — 48 points at
  // most. Each point is [epoch SECONDS, temp]; the temp is in tempUnit (the
  // request asks for the console's own unit) to 1 decimal. [] when unknown.
  // This is what the hero's day-curve draws its dotted future segment through,
  // and the ONLY thing it may draw there: with [] the curve stops at now and
  // the forecast low/high stay text, because nothing in the payload says WHEN
  // they happen. The console maps each epoch onto the local day using ts and
  // time, so points from a stale fetch fall outside today and drop out.
  "fcHourly": [[1756400000, 71.2], [1756403600, 73.4]],
  "fcStale": false,   // true when no successful forecast fetch in 24 h; the console hides the band
  "fcAgeSec": 1800,   // seconds since the last SUCCESSFUL forecast fetch (null = never)

  // Wind  (dir in degrees; cardinal string; needle rotates to dir)
  "windSpd": 0.9, "windUnit": "mph", "windAvg": 0.1, "windGust": 2.9, "windMax": 4.3,
  "windDir": 206, "windCardinal": "SSW", "windStatus": "Calm",

  // Barometer  (needle maps slp on 980..1050)
  "slp": 1022.1, "slpUnit": "mb", "slpTrendPerHr": 0.4, "slpTrendDesc": "Rising",
  "slpSeries": [[1756400000, 1018.4], [1756401800, 1018.2]],   // 24h barograph trace, <=48 [epoch, slp] points in slpUnit; [] hides the graph
  "slp24High": 1022.1, "slp24HighTime": "08:20",
  "slp24Low": 1020.2,  "slp24LowTime": "00:00", "slpOutlook": "Unchanged",

  // Rainfall  (rainRateMm drives the tube: mm/hr mapped onto the console core's
  //  own intensity bands 0.25/1/4/16/50 mm/hr, each an equal fifth of the tube.
  //  rainRate is the same rate in display units, for the printed readout.)
  "rainToday": 0.00, "rainYest": 0.00, "rainMonth": 0.15, "rainYear": 40.0,
  //  rainRateMm is max(the sensor's raw minute, the 10-min time-weighted mean):
  //  the haptic sensor reports drizzle as an occasional trace minute with zeros
  //  between, and the window bridges those so light rain never reads as dry.
  //  rainRateInstMm is the raw minute; rainStatus takes the band word for the
  //  windowed rate when the core says "Currently Dry" inside a drizzle.)
  "rainUnit": "in", "rainRate": 0, "rainRateMm": 0, "rainRateInstMm": 0, "rainStatus": "Currently Dry",
  "drySpellDays": 12, "lastRainDate": "Sun 19 Jul", "lastRainAmt": 0.11,

  // Sun & UV  (sunFrac 0..1 = elapsed fraction of daylight → sun position on the arc)
  "uvIndex": 4.0, "uvDesc": "Moderate", "radiation": 468, "radUnit": "W/m²",
  "sunrise": "05:43", "sunset": "20:43", "sunFrac": 0.29,
  "daylight": "11h 37m", "peakSun": 0.65,

  // Air Quality  (US EPA AQI by station lat/lon, Open-Meteo; null hides the block)
  "aqi": 40, "aqiCategory": "Good", "aqiPm25": 13.8,
  // short forecast so a rising smoke event is visible before the number degrades
  "aqiForecast": [[1754247600, 40], [1754251200, 43]], "aqiPeak": 55, "aqiPeakTime": "5 PM",
  "aqiForecastCat": "Moderate", "aqiTrend": "rising", "aqiTrendText": "Moderate by 5 PM",
  "aqiStale": false,          // true if the last successful AQI fetch is > 1 h old
  "aqiAgeSec": 420,           // seconds since that fetch (null = never). A provider can serve a
                              // days-old station reading; this is the age of OUR download.

  // Weather alerts  (active NWS alerts by lat/lon; same-type collapsed, capped 3,
  // sorted by product level: warning > watch > advisory > alert > statement)
  "alerts": [
    { "event": "Air Quality Alert", "eventClass": "air", "level": "advisory",
      "tone": "brass",        // banner colour token: accent | brass | water | verdigris
      "priority": 2,          // level int (4 = warning, highest)
      "short": "Wildfire smoke", "areaShort": "King, Kitsap, Pierce +2",
      "onset": 1754251380, "until": 1754438400, "untilText": "Wed 5 PM",
      "headline": "Air Quality Alert issued August 3 …" }
  ],
  "alertCount": 1,            // distinct types; the HTML shows "+N more" = count-1
  "alertsStale": false, "alertsAsOf": "13:52",   // last successful fetch; stale after 1 h
  "alertsAgeSec": 300,

  // Moon
  "moonPhase": "Waning Gibbous", "moonIllum": 78,
  "moonrise": "22:14", "moonset": "09:38", "nextFull": "Aug 8", "nextNew": "Aug 23",

  // Lightning  (distance only — no bearing; null when quiet)
  // lightningDist is the core's +/-3 km RANGE text ("13-17"); lightningDistNum is
  // its midpoint, which is what the ring geometry and big-number readouts use.
  // lightningTs is the strike EVENT epoch; lightningSinceSec and lightningLast
  // are derived from it at emit time. The core's StrikeDeltaT is frozen at the
  // moment it was calculated, so a payload built an hour later would otherwise
  // still say the strike was seconds ago. Only used as a fallback when no epoch
  // is available.
  "lightningActive": false, "lightningDist": null, "lightningDistNum": null,
  "lightningDistUnit": "miles", "lightningTs": null, "lightningSinceSec": null,
  // lightningRate = strikes/min (the core's StrikeFreq); lightning3hr = the
  // rolling 3-hour count. There is no 3-min/30-min bucket in the data path.
  "lightningRate": 0, "lightning3hr": 0, "lightningToday": 0,
  "lightningLast": "3 days ago",

  // Sager
  "sagerCode": "G·2·3·D", "sagerText": "Fair, little temperature change...",
  "sagerIssued": "09:06",           // when the Sager forecast was generated (Sager['Issued']); sagerCode/Pressure/Wind/Sky are not sourced (null)
  "sagerPressure": "1022.1 rising", "sagerWind": "SSW backing", "sagerSky": "Clear"
}
```

Rules: numbers are numbers (HTML formats). Times are `"HH:MM"` strings. Angles/fractions
are numeric so the HTML can drive SVG geometry. The HTML treats any `null`/missing key as
an em-dash and leaves that gauge at a neutral position. The emitter must never write a
partial/invalid file (write to a temp path + atomic rename).

`aqiPm25` is PM2.5 concentration in µg/m³ and is populated only by providers that supply a
concentration. It is `null` for WAQI, whose `iaqi.pm25` value is a pollutant AQI rather than
a concentration.

## Freshness and /health

`design/almanac/kiosk/serve.py` turns these fields into a status the watchdog and any
monitor can act on:

| status | meaning | condition |
|---|---|---|
| `ok` | engine and station both live | `ts` fresh and `obsAgeSec` under 5 min |
| `stale` | engine stalled | `ts` older than `WFP_STALE_SEC` (20 s) |
| `degraded` | sensor silent | `ts` fresh, `obsAgeSec` over `WFP_OBS_STALE_SEC` (300 s) |
| `error` | engine down | `wx.json` missing or unparsable |

`obsAgeSec` in `/health` is the payload's value plus the file's own age, so it stays
truthful when the file itself has stopped moving. Anything but `ok` answers HTTP 503.

`/health` also reports two counters. `polls` counts `wx.json` requests from anyone;
`renders` counts frames the kiosk page confirmed it painted — the page adds `r=1` to
its next poll only after `render()` returned, and the server credits that mark only
from a loopback client. The launcher's watchdog reads `renders`, because a request
count never proved anything reached the screen.

## Radar v4 — the panel owns the map

`radar` is an independent side artifact. It never changes `ts`, `obsAgeSec`, or
engine `/health`. Missing radar or `available:false` hides the tab. A station
change releases the previous station's radar and camera. Geography and source
knowledge can be published before the first observed tile; a null observed time
never invents a measurement.

The page owns one `{lat,lon,zoom}` Mercator camera. Pointer events move that camera;
no gesture waits for a request, payload acknowledgement or server image. The
emitter acquires, validates and remaps immutable 256×256 XYZ tiles. It no longer
allocates viewport RGBA canvases, per-site RGBA layers, crops or basemap SVGs.
`lib/data/radar-natural-earth.bin` remains the sole bundled geographic source.
Attribution remains text; no provider origin is added to Chromium's URL policy.

The default preference is **mosaic** when no saved choice exists. A durable
`site` preference survives restarts; an unavailable saved site falls back to
mosaic without an error card. This resolves “cold start Mosaic” as first use,
while respecting the requested restart persistence.

| Source | `sourceId` | `provider` | Nominal `cadenceSec` | Display stale at (`staleSec`) | Zoom bounds |
| --- | --- | --- | --- | --- | --- |
| IEM MRMS | `iem-mrms-lcref` | `iem` | 120 | 600 seconds | 4–9 |
| IEM NEXRAD N0B | `iem-nexrad-n0b` | `iem` | 300 | 900 seconds | 7–10 |
| RainViewer | `rainviewer` | `rainviewer` | 600 | 1200 seconds | 4–7 |

Mosaic tries IEM first for CONUS station centers, then RainViewer. A bundled
coarse land polygon determines CONUS eligibility. Outside CONUS, RainViewer
is used directly. Site-mode eligibility additionally requires the nearest
bundled NEXRAD within 230 km. This is geographic eligibility, not a guarantee
that a radar sees every point inside that circle. `nexrad` still reports the
nearest site within 285 miles; `distanceMeters` provides the unrounded value
used to decide eligibility.

### Acquisition, budgets and tile service (N)

MRMS metadata:
`https://mesonet.agron.iastate.edu/data/gis/images/4326/mrms/lcref.json`.
`meta.end_valid` must be UTC, an even minute, fresh, with `product:lcref` and
`units:0.5 dBZ`. Conditional requests/304 retain validators and revalidate age.
Candidates start at the advertised `end_valid` (`RADAR_IEM_READY_LAG_SEC = 0`).
v4.9 removes the five-minute readiness hold: it alone made a one-cadence AS OF
impossible on a two-minute feed. Tiles that are not rendered yet remain missing
for this pass; validated sibling tiles publish partial and the next pass repairs
from cache. Every accepted timestamp still names matching provider tiles.

During validation, an MRMS candidate probes the original archive with HEAD
unless that stamp already has a successful probe:
`https://mesonet.agron.iastate.edu/archive/data/YYYY/MM/DD/GIS/mrms/lcref_YYYYMMDDHHMM.png`.
Tiles use
`https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/mrms::lcref-YYYYMMDDHHMM/z/x/y.png`.
UTC rollover applies to both paths. MRMS's native raster covers longitude
−130…−60, latitude 20…55; crossing that domain sets `partialCoverage:true`.

As of 2026-09-14, newest discovery belongs to the source, independently of viewport
geometry. Successful MRMS metadata/readiness/archive validation retains `(newest_stamp, validated_monotonic)`; RainViewer retains its
validated manifest paths/host with the stamp, before tiles begin. N0B retains parsed scan listings
(including empty listings) and newest stamp separately per site. Intent-triggered
zoom, centre, source changes and supersede restarts reuse this knowledge only
while monotonic age is strictly below the source cadence: MRMS 120 s, site 300 s,
RainViewer 600 s. Reuse does not reset that clock. Scheduled/retry passes, first
passes and sources/sites whose acquisition failed validate again; expiry cannot
be extended by repeated interaction. Newly encountered sites list independently. Selected viewport and primary-timeline
sites list concurrently in a four-worker pool; nearest reporting primary and
per-frame scan alignment are independent of response arrival order.

A pass reusing MRMS knowledge goes straight to tiles for newest and history,
without metadata or archive requests. Full validation caches each positive HEAD
for the emitter lifetime, independently of zoom, viewport, transport session and
cadence; negative probes retain the 120 s retry TTL. All tiles still require the
same PNG validation and tile metadata rules. Failure of remembered newest tiles
(including a purged scan's 404 or failed site layer) discards knowledge and runs
one full source validation in the same pass, sharing its original deadline,
build limit, cooldown and request budget. Supersession is not a provider failure.
Idle prefetch reuses still-valid remembered stamps/listings. It may discover the
other mode: cached per-site listings for N0B, or MRMS metadata plus archive
readiness when no valid MRMS stamp exists. These requests share the background
reserve and cancellation checkpoints. No separate background worker is added; provider cadence,
observed timestamps and stale thresholds are unchanged. v4.9 removes the artificial
readiness delay and yields incomplete newest work before starting these tiers.

The transport uses standard-library `http.client.HTTPSConnection`, with at most
nine leased connections per host during tile races: six ordinary workers plus
three rescue slots. Metadata-only sessions retain the six-connection bound. A
connection stays leased until the response body has been consumed or closed;
newest frames use six tile workers, history
and idle prefetch use four. Sessions and successful IPv4 DNS results survive
worker passes and socket expiry. Each host is resolved with `AF_INET`; the
resolved IP retains the original TLS SNI, certificate hostname verification and
HTTP Host. DNS has its own 15-minute monotonic TTL. One caller resolves outside
the pool lock, with a per-host event sharing the result among cold callers;
another host can proceed meanwhile. Expired good addresses remain immediately
usable while one background refresh runs. A failed refresh keeps those addresses
and retries next pass; a cold resolver failure is also charged only once per pass.
System DNS cannot be interrupted by socket timeout: cold lookups also run in a
daemon resolver thread. Every cold caller waits on the shared event only until
its deadline; a late result may populate the cache, but cannot issue a request.
Cached callers never wait for resolution.

**v4.9 request isolation, hedges and deadlines.** Each immutable tile gets at
most two attempts, each capped at six seconds including DNS, pool waits, TCP,
TLS, send and every raw header/body read. A failed tile does not abort siblings.
The winner must be a complete, bounded, decoded 256×256 native PNG; truncated,
placeholder and invalid responses cannot win. A fast failure retries immediately
on a fresh connection. A partial-body failure may also retry the immutable tile.
No third attempt is possible, including through the v4.8 transport retry path.

For newest only, no first response byte after `RADAR_HEDGE_SEC = 2` launches the
second attempt on a fresh connection while the first remains eligible to win.
A received status/header byte suppresses the hedge; slow bodies keep the six-second
absolute cap. First valid response wins. The loser is shut down, drained/joined
and discarded before cache mutation. At most `floor(len(ctx.tiles)/2)` hedges are
reserved per source pass, shared by its site layers; every admitted hedge/retry
uses the rolling rate gate. The cap is conservative for multi-site views (the
viewport count, rather than the sum of layer tiles). Three extra pool slots keep
six hanging primaries from starving all rescue requests. Other hedges may wait
inside their request deadlines; no unbounded threads or abandoned tile workers.
History and warming have four tile workers, sequential retries and no hedges.
An incomplete newest publishes its partial inventory and requests another pass
(normally two seconds with headroom) before history or warming can start.

Metadata and archive HEADs have eight-second absolute request budgets. The
v4.8 zero-byte retry remains for these: a reused socket gets up to three seconds
from send for its first response byte; a zero-byte failure retries once fresh
inside the original eight-second budget. First attempts and retries are sampled
and rate-gated. Partial metadata responses are not replayed. Cold discovery is
still a dependency and does not promise a three-second acquisition; valid intent
knowledge and stale-while-refresh DNS remove that wait on warm paths.

`RADAR_BUILD_DEADLINE_SEC = RADAR_PRIMARY_DEADLINE_SEC = 25` remains the shared
absolute pass deadline. Each source gets at most 16 seconds to acquire newest,
leaving up to nine seconds for the next source. A source timeout, open breaker or
transport failure falls through immediately in the existing site → MRMS →
RainViewer order as applicable. v4.8's three-failed-pass transport hold is removed.
After newest publication, background tiers may use the remaining original
25-second budget. Resumed warming gets a new 25-second pass. All active requests
and their cleanup remain bounded by the original deadlines (0.4–0.5 second test
cleanup grace; no hard real-time Pi claim).

Idle sockets still expire after two seconds or the shorter advertised Keep-Alive
minus 250ms. Every checkout checks expiry. Hedges and retries bypass idle sockets.
`Connection: close`, provider changes and shutdown discard connections. SNI,
certificate verification and IPv4 DNS caching remain intact. Best-effort Linux
TCP keepalive uses 2/2/2 seconds/seconds/probes; missing options are harmless.

**Host circuit breaker.** Completed attempt outcomes form a rolling 60-second
window per hostname/port. At ≥6 samples and <50% successes, stop admitting that
host for 30 seconds. Recovered hedges still count their failed/cancelled loser;
HTTP 404 is a responding host with unavailable content, not a host outage.
After cooldown, one fresh metadata request owns the `half` state; concurrent
probes are rejected. Success clears the old samples and closes; failure opens
another 30 seconds. Tile-only hosts use HEAD of a remembered tile URL because
another metadata hostname cannot prove their recovery. Existing 429 cooldowns
and the shared rate cap still apply. Eligible fallback runs in the same pass;
recovery is retried after cooldown (or an already scheduled earlier retry).
A shared-host outage applies to both IEM products. Paid-for successes stay cached.

**Operator health.** `wx.json.radar.health` is exposed as `/health.radar`:

```json
{"lastSuccessTs":1789444700.5,"successRate60s":0.78,"hedges":9,"retries":9,
 "discardedHedges":9,"breaker":"closed","lastError":"discarded radar attempt",
 "hosts":{"example.invalid":{"breaker":"closed","samples60s":41,"successRate60s":0.78}}}
```

`lastSuccessTs` is Unix seconds of the last usable newest publication (including
partial), not the measurement timestamp. Ratios are 0–1, null without samples.
Hedges/retries are cumulative admitted attempts, including bounded DNS/pool waits;
a hedge is also a retry. `discardedHedges` counts pending losers when a race wins.
`breaker` is the worst state across known hosts (`open`, `half`, `closed`), so a
working fallback does not hide the primary outage. `lastError` retains the last
request error even after recovery. Every ordinary fetch pass emits this object at
INFO; the v4.8 retry line still carries `transport_retries` and
`stale_first_byte_retries`. Radar faults do not change the engine/sensor HTTP health
verdict. Health reflects the latest engine payload, like other `/health` fields.

Validated native PNG bytes enter a 400-tile in-memory LRU before cancellation is
checked. Keys include source, site when applicable, scan timestamp, zoom and tile
X/Y. IEM native bytes do not depend on the console palette revision; RainViewer
keys also include its server-side colour scheme and options. A pan or restarted
pass reuses overlapping tiles without HTTP, even if the earlier crop never finished.
Truncated, oversized, placeholder and invalid tiles never enter this cache.

The existing request budget remains 240/minute, rolling and shared across every
source, HEAD, metadata request, tile hedge and retry. The interaction reserve is
34; optional warming and deep history retain 60 further slots. No gesture bypasses
a provider cooldown. The worker has one flight, its preference watcher runs every
100 ms, and supersession drains paid-for tile responses into the cache before
scheduling the latest view. A superseded pass publishes no restarted notice.
Successful partial tiles survive another tile's failure or a budget yield.

| Priority | Work |
| --- | --- |
| 1 | Newest, integer Z, viewport plus one-tile margin, nearest first; at most 35 tiles per mosaic |
| 2 | Other mode newest at the current camera/Z first, then newest centre 2×2 at Z−1 and Z+1 and other-mode neighbours |
| 3 | History slots 2–8, viewport only, at most 15 tiles per mosaic |
| 4 | History 9–31, viewport only, after 20 seconds of continuous viewing at this view |

Multi-site retains eight source slots. Other-mode warming retains its existing
adjacent-level work after its current-level tiles, within the same reserve.
The `radar_viewed` 15-minute demand hint and runtime `radar_viewing:{since,last}`
continuous-view gate survive. Hidden documents omit the view signal. Off-tab
passes fetch newest only and retain the hour on disk; warm view-start publishes
that retained manifest without changed observation/fetch times. It also resumes
missing opposite-mode warming; already resident rounds need no provider HTTP.
Scheduled provider checks remain 180 seconds; external failures retry at 120
seconds; capacity and view-gate yields schedule their next eligible opportunity.

```
radar/t/<renderRevision>/<sourceId>/<ICAO-or->/<YYYYMMDDHHMM>/<z>/<x>/<y>.png
```

The stamp is the provider's actual UTC scan time. The 12-hex rendering revision
hashes the remap revision, native inverse tables, both ramps and palettes,
wire-metadata revision, and basemap revision. A changed renderer gets
a new URL. The page checks PNG metadata against `tiles.remapRevision`. The site
table has an independent content revision and immutable URL. Unknown revisions
and themes return plain 404, even if an obsolete file remains on disk. `radar_palette.remap` runs on
cache fill, once per tile, after native PNG/size/placeholder validation. Writes
are atomic. `radarRemap` PNG tEXt has exactly `remapped`, `unmatchedColors`,
`opaqueColors`, `unmatchedPixels`, `opaquePixels`, `ambiguousPixels`, `revision`.
A separate `radarVisiblePixels` tEXt integer records nontransparent output pixels
after remapping (native opaque pixels can be below the displayed floor). This
avoids client readback allocation while keeping clear-state decisions exact.
Cache hits decode and verify 256×256 dimensions, revision, counts and LUT colours,
including the supplemental output-alpha count.
Invalid entries are removed and rebuilt. Native bytes retain their separate
400-entry LRU and transport lifecycle; no RGBA layer cache replaces it.

The existing HTTP/1.1 static handler serves valid, existing tile and geometry
paths with `Cache-Control: public, max-age=31536000, immutable`. Missing files
return ordinary 404, without immutable caching; there is no 202, long poll or
new dynamic route. Last-served tile atime drives eviction. The cache retains
stamps within 3600 seconds of newest at the current level and levels touched in
the last 15 minutes. Outside that protected set, oldest-served tiles yield first.
Caps are 8,000 files and 64,000,000 bytes. Admission yields if protection leaves
no room: it cannot both exceed the cap and promise retention. The first remapper
revision run removes owned crop and SVG directories and obsolete remapped tiles.

At approximately 8 KiB per tile, one level's 31×35 working set is about 8.7 MB,
and write churn is about 76 GB/year. `WFP_RADAR_DIR` may point to tmpfs beneath
the static root to trade persistent warm starts for no SD writes; this is an
optional deployment choice, not a changed security or preference model.

### Manifest and measurement honesty (P)

```jsonc
"geo": {"version":"<sha256-first-12>", "base":"radar/geo/", "sites":"radar/sites-<siteRevision>.json"},
"tiles": {
  "base":"radar/t/", "revision":"<renderRevision>", "remapRevision":"native-v3.2-1", "source":"iem-mrms-lcref", "site":"-", "z":8,
  "levels":[7,8,9], "grid":{"x0":39,"y0":88,"w":4,"h":3},
  "newest":{"stamp":"202609141733","mask":"fff","expectedMask":"fff","completeMask":"fff"},
  "frames":[{"ts":1789407180,"at":"10:33","stamp":"202609141733",
             "siteScans":[],"levels":{"7":false,"8":true,"9":false}}]
},
"center":{"lat":47.61,"lon":-122.33},
"units":"mi", "rings":[{"meters":40233.6,"label":"25 mi"}],
"scaleChoices":[{"meters":16093.44,"label":"10 mi"}, {"meters":40233.6,"label":"25 mi"}],
"zoomAuto":true, "zoomAutoLevel":8, "zoomMin":4, "zoomMax":9,
"zoomDesired":null, "zoomCapped":false, "zoomSource":"MRMS",
"refresh":{"state":"history","frameIndex":4,"frameTotal":8}
```

`center` is **always the station**. `tiles.z/grid` describes the worker's latest
reported viewport, not a command to move the page. Grid covers the viewport;
the one-tile warming margin is additional. At other levels its geographic bounds
are rescaled into XYZ, rather than reusing Z's integer indices. This resolves the
v4 example's 35-tile grid against its normative ≤35 grid-plus-margin and ≤15
history budgets. `mask` has one meaningful row-major bit per grid position, LSB
at `(x0,y0)`, zero-padded to `ceil(w*h/4)` hex digits. A set bit means an actual
newest tile exists (at least one aligned site for multi-site); padding bits are
zero. `frames[].levels[Z]` is true only when the frame's needed grid tiles at Z
exist. History is oldest first, never padded or fabricated. `siteScans` carries
the actual aligned source timestamps in stacking order.

The existing source, attribution, cadence, stale, observed/fetched time,
partialCoverage, frame count/spacing/gap, site-reporting, source preference and
fallback fields survive. `frameCount` counts candidate slots;
`completeFrameCount` describes completed sets. The page's decoded inventory
(including frames on either side of a gap) reflects decoded coverage at its own current camera, not those server counters.

`frameSpacingSec` is median
completed-set spacing, not a promise of fixed scan cadence. `observedAt` is
scan time, `updatedAt` fetch completion time (retained for telemetry, absent
from the face). A failed fetch changes neither. `ageSec` is scan age;
`stale` starts at the source's `staleSec` (a per-source threshold set above that
source's freshest-possible frame — MRMS is never shown younger than ~5 min
because IEM 503s newer minutes, so a bare 3×cadence = 6 min would flag every
healthy scan; `staleSec` is exposed so the console re-derives it consistently and
a legacy payload falls back to 3×cadence). Header age suffix starts at **80% of
`staleSec`** (3×cadence for a legacy payload), floor-rounded to minutes — so a
routinely-delayed feed reads clean and the suffix forecasts the stale flag. Nominal cadence belongs only in the
source caption; scan age belongs only beside AS OF. Never label data LIVE.

AS OF, ageSec and staleness all describe the newest primary scan. The loop
frame read names the displayed historical scan once the eight-frame inventory
is ready. A tile contributes its own remap counts only when drawn. More than
2% discarded opaque pixels or any cross-stop ambiguity marks `palette incomplete`.
Incomplete acquisition or remapping cannot assert clear conditions.

Missing tiles first use a **same-stamp**, same-source/site-set parent at Z−1 or
Z−2, nearest-neighbour scaled. Without that fallback, missing radar tiles draw
nothing. There is no hatch, pattern, coverage tint,
delayed overlay, or partial-coverage aria suffix in either theme. Same-stamp
parent tiles still supply measured echoes while finer tiles are in flight;
other timestamps never fill a missing tile. **Absence of echoes in a region may
mean "not yet loaded".** The loop read and note carry that acquisition state.
The loop read shows `Buffering · N of 8` (or `Paused · N of 8`) until the eight
frames are ready, including when a partially acquired scan already shows echoes.
The note retains `Refreshing · newest frame` / `Refreshing · frame N of M`.

The former acquiring-versus-partial 40% classification is removed. What remains
is copy driven by decoded frame readiness/inventory and the refresh stage
(newest versus history). A tile's `partial` flag still prevents an incomplete
multi-site tile from claiming readiness/clear weather and permits retry; it
never controls decoration. Cold acquisition tiles paint at full opacity on the
next rAF after decode. Temporal playback blending does not apply to arrivals.
This user decision supersedes P4 and the v4.4 hatch rules.

The reporting timeline owner (`siteId`, station-nearest reporting site) remains
the caption/picker subject while its tiles are late: e.g. `Camano Island radar
+ 1 nearby · new scan every ~5 min · IEM / NOAA · KATX loading`. Nearby counts
count actual other contributors; station distance stays with the nearest site.
`KATX loading` replaces `timeline KATX` (v4.4 overrides the exception vocabulary
retained in Fable v4.3 §1.4). A site explicitly marked `not reporting` can still
yield to a drawn contributor, with the existing honest not-reporting suffix.

Retired: public `frames`, frame `id/url/complete`, `latest`, `basemap`,
`geometryOnly`, `marker`, `centered`, `bounds`, `viewport`, `metersPerPixel`,
`scaleBar.pixels`, `rings[].px`, `intent`, `refresh.forSeq/state:superseded`,
`X-Radar-Intent-Seq`, fast poll windows, crop hashes, CSS layer transforms,
preview/commit/gesture fences and `Refreshing · restarted`. Internal worker
completion and cancellation bookkeeping is not a page acknowledgement.

### Local raster geography (v4.1 L′)

The unchanged Natural Earth bundle is the only shipped basemap artifact.
`radar_basemap.tile(theme,z,x,y)` produces opaque 256×256 PNG-8 tiles for z4–10.
URLs are `radar/geo/<version>/<paper|night>/<z>/<x>/<y>.png`. Version is SHA-256
of bundle, exact style table, style revision and renderer revision, first 12 hex.
No geometry binary service, client projection buffers or backing canvas remain.

Paper ground is **#EBE6DB**, including the 3.5% ink plate inset; night ground is
**#0B0D11**. Water is #DFDCD4 / #151B21. Full coast-over-water is #95A3AA /
#445A68; coast-over-land #9BA9AE / #3F525F. Admin0 over land is #9F9B90 /
#686766, admin1 #CFCBC0 / #575756, roads #BAB6AB / #464747. Stroke widths are
1px at the tile's zoom. Admin1 starts at z5, major highways z6, secondary roads
z8. Admin1 dashes are 4/3. Degree-cell ocean runs and stitched line fragments
are retained; Douglas–Peucker tolerance is always 0.5 tile pixels.

Compound ocean/lake fills use one aliased even-odd scanline pass at 1×.
Each stroke uses a reused 1024×1024 L coverage buffer and `Image.reduce(4)`.
Later stroke coverage replaces previous strokes against the original backdrop.
The two backdrops and five stroke layers × two backdrops × sixteen coverages
form a fixed 162-entry palette, with no dither, alpha or tRNS chunk.

**v4.2 scheduling:** geography has its own single-flight `geo` worker, scheduled
every 250ms from engine startup, independently of radar fetches, view markers
and live viewing sessions. Each invocation renders at most one missing tile;
250ms is an admission quantum, not a render deadline. A slow tile completes
before the next geo invocation, without holding the radar result or transport
locks. Background work sleeps another 50ms after a tile when there is no fresh
viewed viewport. An in-progress tile cannot be interrupted.

The home 7×5 block is pinned at every zoom in both themes (490 tiles away from
the polar tile-row limits). Order is home zoom's central 5×3, its margin, z−1,
z+1, z4, remaining zooms by distance; then the other theme in the same order.
The initial theme defaults to paper without an activity report. The queue
retains progress and idles when complete; station or basemap revision changes
rebuild it, reusing any existing immutable tiles. An engine restart checks the
same disk set and does not rerender existing tiles.

Loopback `radar_activity` atomically carries `{at, theme, moving, center, zoom}`.
The displayed camera (`radarGeoCenter`, `radarGeoZoom` on the ordinary poll) is
independent of durable requested/auto zoom and the radar worker's current pass.
With a viewed marker and activity age **0–5s**, missing tiles for that settled
viewport and its margin precede the remaining home queue, in the reported theme.
The freshness check applies only to viewport priority. Missing, malformed or
stale activity never gates home warming, except that a report of `moving=true`
suppresses both queues until a settled report or marker removal. No request is
added during gestures: existing polls remain suppressed while moving, so the
engine cannot know about unreported motion and can only yield at tile boundaries.
Home and viewport PNGs, and the served geography revision marker, use a unique
same-directory temporary file followed by atomic replacement. Geography has an
independent **32,000,000-byte / 6,000-file** served-atime
LRU. Pruning removes empty directories and never evicts home tiles. Radar cache
pressure cannot evict geography. Routine tile access/miss logs are suppressed.

`tools/benchmark_radar_kiosk.py` prints `summary` first: cached `firstPaintMs`,
pan/pinch `{frames, maxDrawMs, p95DrawMs, maxDrawImages, serverRequests}`,
`memoryPeakMiB`, `geoTilesOnDisk`, `radarTilesOnDisk`, and `fences`. CDP network
events exclude browser-cache/service-worker responses from server request counts;
each gesture includes its settling interval. Disk counts cover all revisions
under `--radar-dir` (default `WFP_RADAR_DIR` or `~/almanac_web/radar`), and are
null if that root is absent. Raw frames/fetches/decode/GPU details require
`--verbose`. These graphics counters do not measure Chromium process RSS.

Every opaque base-canvas paint is: baked ground → whole-plate graticule →
same-version/same-theme z−2 ancestors → z−1 ancestors → exact tiles. Ancestors
are skipped when all exact tiles are resident. Thus uncovered regions retain
graticule rather than silently reading as land or sea. No hatch or note denotes
missing geography. The graticule uses `--rule-faint` and the largest step from
5°, 2°, 1°, .5°, .25°, .125° with local parallel spacing ≤220px. At extreme
latitudes where .125° exceeds that bound, it is halved until the bound holds;
meridian/parallel steps are also widened independently when needed to retain
the 18/7 segment limits. This explicitly corrects the delta's incompatible
finite-step/segment assumptions at polar and equatorial edges; spacing remains
below 220px. The Seattle table is unchanged.

The page retains ≤36 basemap bitmaps across tab switches. Theme changes evict
that LRU and change the path prefix. Base tiles use bilinear sampling when
scaled and disable smoothing at native scale; echoes always disable smoothing.
Neither layer uses a CSS filter. Missing tiles retry no faster than every 2s.
Only missing visible coverage requests ancestors; resident margins do not churn
against unnecessary ancestor prefetch. Cached activation targets <100ms to an
actual geography-tile paint, not merely a ground/graticule paint.

### Rendering, graphics memory and gestures (M′/Q)

`#rad-base` and `#rad-echo` are 956×490. Fractional zoom uses the floor native
level until the integer snap, bounding each exact layer to fifteen draws even
at half zoom. Echoes and geography share unwrapped placement math; only cache
keys and URLs wrap x. The manifest rectangle and row-major masks are unwrapped.
Site `expectedMask` denotes geographic intersection; `mask` means any acquired
contribution and `completeMask` means all required site contributions acquired.
Outside-range tiles do not block playback, and partial site tiles cannot claim
complete acquisition or clear conditions.

Playback uses up to eight 956×490 bitmaps (including newest) plus one reusable
plate scratch, within the same 40MiB admission cap. Newest tiles remain available
for acquisition and camera moves. A moving history bitmap exclusively owns its projected rectangle;
component tiles are clipped outside that rectangle, so alpha/reflectivity never
double-composites. Its hasEcho/legend metadata remain attached to its pixels.
Settle/cancellation snaps and clamps the camera, invalidates history and reports
one settled intent. History work runs only on otherwise unpainted idle frames;
loop ticks never stack on a camera repaint. Draw-count fences are ≤32 steady,
≤56 degraded. Wall-time target is 4ms on the actual panel, requiring CDP evidence.

One rAF loop targets 30fps. Events mutate camera state; drawing is coalesced.
Echo arrivals repaint the echo plate; geography arrivals damage only their
rectangles. Polls do not rerender geography.
Overlay groups `rad-geo` and `rad-chrome` persist; gesture updates change
attributes only. Ring labels use one translation attribute apiece (site labels
retain x/y), bounding four sites plus two rings to twelve writes. Site arcs are geodesic circles sampled once at 120 points per
site/session. The scale's chosen distance freezes during gestures; its length
still follows `156543.034*cos(lat)/2^zoom`, and settle chooses the largest distance
≤25% of the short axis. Theme changes repaint geography, not radar data.

The graphics budget admits storage **before allocation**, with a shared 40 MiB
cap across echo and geography jobs. Retained canvases are 3×1,873,760 bytes,
histories ≤7×1,873,760, echo LRU ≤40×262,144 and basemap LRU ≤36×262,144.
Four shared decode slots reserve 786,432 bytes each. A 262,144-byte merge scratch
is included too, so simultaneous nominal maxima require eviction of at least
one tile (the delta's 39.87 MiB omitted the scratch).
History transfers reserve a plate before allocation; if eviction cannot make
room, admission blocks. Merging has no getImageData/readback buffer. Compressed
responses are bounded at 128 KiB; oversize tiles follow the unavailable path.
This accounting covers owned graphics and bounded decode working storage,
not Chromium process RSS, driver internals or its HTTP cache. The harness also
tracks bitmap creation/close and canvas dimensions independently.

Gesture state is `idle | gesturing | inertia`. Pan follows the pointer 1:1;
polar edges and 1.5 viewport-diagonal station radius resist at .3 and clamp on
release. Inertia uses release-time samples from the last 60ms (a stationary hold cannot fling), capped at 2400px/s,
exponential τ325ms, stopping below 8px/s or at the clamp (≤780px). Reduced motion
has no inertia. Pinch preserves the geographic point under a translating midpoint.
Release snaps to the nearest integer in 160ms about the final focal point;
reduced motion snaps immediately. The zoom read shows the eventual integer.
Wheel/double tap use ±1/+1 about the input point; steppers zoom about the centre.
Recenter and 90-second idle recenter ease to the station over 280ms. Only the
plate uses touch-action:none; controls remain target-gated, with ≥44px hits.

The only motion is 160ms zoom, 280ms recenter, inertia, 120ms note opacity,
120ms temporal scan crossfade, tick movement and 120ms Play press dip. Reduced
motion removes blending and incidental animation, retaining its opt-in single
sweep with hard cuts. No tile fade, shimmer, skeleton or loading pulse is introduced.

A cold provider outage with a valid station keeps Radar available. The local map
and graticule remain while measurement inventory is empty; no observed time or
clear conditions are invented. Cold activation uses durable manual/auto zoom,
clamps to source bounds, preserves requested zoom through temporary caps, and
reports its initial camera. AS OF ordering is scoped to source/station/primary
identity. Newer accepted transitions invalidate old pending source generations.
Failed viewport reports remain pending until delivered; only a newer settled
camera supersedes them. First tab entry skips acquisition only after the full
required eight-frame inventory has been checked on disk. Publishable partial
frames are distinct from complete sets in completion counters and retry work.

### Loopback reports and the single note

The loopback server validates a complete `{seq,zoom,source,center}` intent and
atomically replaces one runtime `radar_intent` JSON record with fsync. Zoom and
source are also written through durable symlinks for restart persistence. Once
present, only the runtime record is consumed and watched by the worker; separate
legacy files are read only before the first runtime transaction. Pan remains transient.

The query grammar and writer/fsync/durable-symlink rules are unchanged.
`radarCenter` is `station` or ASCII decimal latitude/longitude bounded by
±85.05112878/±180. Duplicate and non-loopback writes are ignored. The runtime
intent is never symlinked to durable storage; zoom/source persist, pan is
transient. The page maintains its camera in sessionStorage. Reports carry a
monotonic local sequence, seeded from current epoch deciseconds to survive page
reloads without an acknowledgement handshake. The sequence only cancels obsolete
worker warm-ups, never gates pixels.

Camera reports occur on activation and after settle's 120ms trailing debounce.
**v4.3 source input** renders intent synchronously on primary pointer contact;
native click also supports mouse, keyboard and assistive activation. The next
event-loop task sends the complete intent on the existing loopback `wx.json` GET
channel, coalescing a synchronous burst and aborting an obsolete in-flight poll.
The pointer's compatibility click does not send a duplicate transaction. Source
input below the site floor snaps the camera to zoom 7 before sending.

Ordinary polling remains 2s and is omitted during gestures/inertia. A source
choice polls every 300ms until the payload acknowledges its source preference,
for at most 20s; hidden/inactive Radar never gets accelerated polling. Pending
presentation lasts until that source's tiles land, even after fast polling ends.
New plate activity cancels an unposted camera report. An in-flight tile is
allowed to finish into the LRU; the pending queue is rebuilt for the new camera.
Four concurrent page tile requests leave room for wx.json. Newest-visible tiles
precede margin, adjacent 2×2 and newest-first history. During gestures only newly
visible demand is added. Missing tiles back off at least two seconds and the
manifest mask suppresses requests for known unavailable newest positions.

The only status corner is `#rad-note`, a fixed 14px box at top418/right12 with
`role=status`, `aria-live=polite`, `pointer-events:none`. For 600ms after a local
report it stays suppressed. Priority is refused-source copy (v4.3b), newest/history refresh, failure naming
the visible scan, upper zoom-cap copy, then `KATX resumes at zoom 7`. Copy remains
`Refreshing · newest frame`, `Refreshing · frame 4 of 8`, and
`Couldn't refresh · showing 17:12`. The failure copy requires both
`refresh.state=failed` and a known newest measurement strictly older than two
`cadenceSec` intervals; one failed pass with fresh newest shows no failure note.
The visible loop read still names its displayed scan, and AS OF names the newest
measurement, never fetch completion time. Only a 120ms opacity transition is used,
disabled for reduced motion. There is no Updating pill, spinner, second note,
or `aria-busy` write on the interactive plate.

### Immediate source choice (v4.3)

`#rad-src` and the requested segment carry `data-state="pending"`; the group
carries `aria-busy="true"`. The requested segment has a static dotted underline,
while `aria-pressed` continues to identify the displayed source. The caption
continues describing that displayed source. After the note's same 600ms grace
(`radarIntent.postedAt`), it appends `· switching`, including when no new frame
or poll arrives. A matching payload starts tile acquisition; the new caption
and confirmed state replace pending when a tile is available to draw.
An acknowledged preference that cannot be displayed clears pending and shows
`Couldn't switch · showing <subject>` in the existing 14px note. A preference
ack during newest/history acquisition alone does not claim a refused switch.
A decoded tile can complete the transition without another fetch. Repeated
payloads for the same transition retain its in-flight work; a newer choice
invalidates the old transition. Refresh progress remains in the existing note.
No new animation is introduced, including under reduced motion.

The emitter's existing idle tier writes both native LRU bytes and the immutable
remapped disk paths used by `serve.py` and the v4 page. Opposite-mode newest at
the current camera takes precedence over optional zoom neighbours. Source
cooldowns cannot block another source's eligible round; the shared 240/minute
cap, 34-request reserve and 60-slot round admission still apply. A round is
remembered only after all its tiles succeed; missing disk files invalidate the
completion shortcut. Interrupted rounds resume using paid-for native/disk tiles.
A warm eight-frame tab return publishes immediately and resumes missing
opposite-mode work on the next 100ms watcher tick in the same radar flight lane. Expired
current-source metadata prevents warming its own neighbours, but does not prevent
discovery of the opposite source. Warm intent passes reuse fresh listings; no
listing request is needed until their existing cadence expires. Cold passes
retain the displayed-source caption plus `· switching` after the grace while discovery/acquisition runs. First-tile
publication orders timestamps within source/primary identity, so a site volume
older than the displayed mosaic can still publish immediately.

### Shared reflectivity palette

`lib/radar_palette.py` pins complete native colour inverses in `lib/data/`, with
byte-identical provenance fixtures in `tests/fixtures/`. MRMS's indexed formula
is `i/2−32`, N0B's is `i/2−33` with reserved codes 0/1 transparent. RainViewer
uses the published Universal Blue table; tiles request scheme 2 with options
`0_0`. These lookup tables supply intensity only, never precipitation type.

All three sources share the nine rain bands and use the same remapped tiles for both
themes. MRMS and RainViewer publish the following 10 dBZ legend:

```jsonc
"legend": {
  "id":"almanac-reflectivity-v1", "floorDbz":10, "remapped":true,
  "bands":[
    {"lo":10,"hi":20,"start":"#8AA3C6","end":"#4E79B4"},
    {"lo":20,"hi":25,"start":"#2E93A8","end":"#227F92"},
    {"lo":25,"hi":35,"start":"#3FA65E","end":"#2A8448"},
    {"lo":35,"hi":40,"start":"#C79C14","end":"#B0870D"},
    {"lo":40,"hi":45,"start":"#E5871A","end":"#D2700F"},
    {"lo":45,"hi":50,"start":"#DE5C17","end":"#C94C0C"},
    {"lo":50,"hi":60,"start":"#DD4530","end":"#BC2A1A"},
    {"lo":60,"hi":70,"start":"#CE4E88","end":"#A9389B"},
    {"lo":70,"hi":75,"start":"#8A46C2","end":"#8A46C2"}
  ]
}
```

Only `sourceId:"iem-nexrad-n0b"` (single or multi-site) publishes `floorDbz:5`
and prepends `{"lo":5,"hi":10,"start":"#7F8295","end":"#7F8295","alpha":180,"kind":"clear-air"}`.
The rain bands gain neither `alpha` nor `kind`. Site indices 0/1 and 2…75 are
transparent, 76…85 (5…9.5 dBZ) use this flat step, 86 starts the rain ramp, and
217…255 retain the open top. The unchanged MRMS formula starts visible output
at index 84. The B2 zoom-floor fallback serves MRMS, so its legend stays at 10.
There is no precipitation-type key, inference or secondary ramp. RainViewer's
caption ends `· reflectivity only`.

The 26 rain LUT entries remain sRGB samples every 2.5 dBZ, 10 through 72.5,
with an open-ended last stop. The site palette prepends one 5 dBZ step without
extending that LUT. Every rain sample meets ≥2:1 on both page paper `#F2EDE2`
and tinted plate paper `#EBE6DB`, and ≥3:1 on night `#0B0D11`. Clear air uses
`#7F8295` at alpha 180/255: composites `#9F9FAA` on plate paper (2.10:1),
`#A1A1AC` on page paper (2.18:1), and `#5D606E` on night (3.10:1).
Below the actual source floor is transparent. Stale echo opacity remains .66;
the canvas has no CSS filter or theme-dependent recolouring. At stale opacity
the clear-air composite falls to 1.60:1 on plate paper and 1.99:1 on night;
these floors describe the plate, not the one-pixel basemap hairlines beneath it.
On paper the clear-air composite has nearly the same luminance as the 10 dBZ
start, separated by chroma and flatness; a tritanope may confuse those two marks.

The legend stays 414px wide, right:12px, with 8px left padding, a 34px unit cell
and a 372px ramp. Band widths are proportional to `(hi−lo)` across that full
372px; its shared 1px border overlays the segments without consuming scale
width. Site has ten segments (5 dBZ spans 26.571px, 10 spans 53.143px), mosaic
nine. Ticks are de-duplicated `[floorDbz,10,20,30,40,50,60,70]`, positioned at
`(dBZ−floorDbz)/(75−floorDbz)*372`, each with a 1×3px hairline. The 10 tick
also divides clear air from the rain scale; there is no extra rule.
The legend rebuild key includes the floor even when source and legend id agree.
Clear-air swatches use opaque `--rad-clear-air` composites (`#9F9FAA` paper,
`#5D606E` explicit or system night), because translucent paint over the legend
scrim would differ from the plate. Other segments keep their payload gradients.

When `legend.floorDbz===5`, `.rad-clear-note` adds one right-aligned line under
the ticks: `Grey band: clear air, not rain`. It uses 11.5px Source Sans and the
caption's existing `--ink-soft`, on the legend's existing `--plate-scrim`.
It is absent at every other floor. Outer legend width stays 414px, unit 34px,
ramp 372px; the extra line remains within the top y0–72 control zone. The ramp
has `role="img"` and `aria-label="Reflectivity scale, 5 to 75 dBZ. Below 10 dBZ in grey: clear-air return, not precipitation."`
in site mode, or `aria-label="Reflectivity scale, 10 to 75 dBZ."` for mosaic.

IEM XYZ tiles actually arrive as RGBA with antialiased colours. After existing
PNG/size/placeholder validation, the compositor remaps each distinct colour:
exact RGBA, then exact RGB, then nearest native RGB within Euclidean distance 3.
Visible output uses `round(nativeCoverage * targetAlpha / 255)`, with the
alpha-zero suppression path unchanged. Opaque rain stops preserve their exact
pre-v3.2 RGBA bytes in indexed and RGBA tiles. Repeated and tolerated RGB matches
retain their candidate dBZ range; crossing a target stop sets `remapped:false`
and counts `ambiguousPixels`. Verified provider-indexed PNG palettes preserve
numeric indices rather than losing repeated-colour intensity information.
Unknown opaque colours become transparent and are counted.

For at most 256 native RGBA colours, verify an adaptive palette's exact RGBA
round trip before swapping its palette. Pillow's RGBA octree is not always
lossless even below 256 colours; if verification fails, verified exact RGB
median-cut and a separately rounded coverage/target-alpha product perform
the remap in C. More than
256 colours uses channel masks, bounded at 1024 colours; larger inputs are
rejected. There are supersede checkpoints before and after every tile.

### Source rendering and playback

Site selection, real scan listings, nearest reporting station-timeline primary,
230km spherical range intersection, four-site cap and farthest-first stacking
survive. Each secondary scan is real, no later than the primary and no more than
900s older. Tile failures preserve other successful tiles; the page combines
aligned site tiles using one 256px scratch, never four viewport layers.

The site picker names the reporting primary plus the number of actual other
contributors (`KATX +2`), using the displayed frame's drawn sites. A late primary
keeps its identity and the caption adds `KATX loading`; an explicitly non-reporting
primary can yield to an actual contributor.
The visible segments are `Region` and the live callsign (`KATX`, `KATX +2`).
The region segment has `aria-label="Region: many radars blended"`; the site
segment expands to `KATX: Camano Island radar, 39 mi NE` or
`KATX and 2 nearby: Camano Island radar, 39 mi NE`. Distance is omitted from
both accessible and visible copy when that primary is not `nexrad.id`.

Caption copy (v4.3b) names the subject, arrival cadence, and provider:
- MRMS: `Many radars blended · new image every 2 min · IEM / NOAA`.
- RainViewer: `Worldwide blend · new image every 10 min · RainViewer · reflectivity only`.
- Nearest single site: `Camano Island radar · 39 mi NE · new scan every ~5 min · IEM / NOAA`.
- Neighbours: `Camano Island radar + 2 nearby · new scan every ~5 min · IEM / NOAA`.
- Non-nearest primary: `KLGX radar · new scan every ~5 min · IEM / NOAA`.

For the nearest primary, its site-table name wins over `nexrad.name`, then its
callsign; a non-nearest primary uses its callsign, even if the table names it.
Only `nexrad.distanceDisp` plus `bearing` supplies the station-relative distance.
Visible distance yields to the neighbour count; the accessible name retains
it when valid. Mosaic cadence stays derived from `cadenceSec/60`; the site's
approximate volume cadence keeps its tilde. `#rad-status` alone states freshness.
No caption contains NEXRAD, MRMS, mosaic, volumes, or dBZ. Provider remains a
visible `#rad-attrib` text-only anchor without href, including during switching.

Exception wording and ordering after attribution remain as before (including
`reflectivity only`, `latest only`, `scanning slowly`, `KXXX loading`,
`scan unavailable`, `deferred`, `out of view`, `not reporting`,
`palette incomplete`, `wider than KXXX reaches`). This follows Fable's exact
RainViewer and dark-neighbour assertions; `· switching` is always last.
Rendered overflow drops distance, then shortens `new scan every ~5 min` to
`every ~5 min`, then drops `+ n nearby`. Provider and exception text is never
removed. The caption uses `overflow:hidden; text-overflow:ellipsis; white-space:nowrap`
for any remaining overflow. Its outer maximum is 530px (516px text plus the
existing 14px padding), satisfying Fable's explicit bounding-box gate; this is
stricter than the spec's 530px content / 544px outer prose budget. The plate's accessible name
lists contributing IDs. With at least two contributors, `#rad-over` draws their
geodesic 230 km arcs in `--rule-faint` (1px, dash 2 3), clipped to the plate.
Site centres inside the viewport have 2px `--ink-soft` dots and 11.5px labels
(dx5, dy13), omitted unless the complete label fits y76–412 and the viewport.
Station marker, rings, scale bar and displaced-station treatment retain their meanings.

A source switch retains the previous visible scan and metadata until the first
new-source tile decodes, then presents its matching source/legend/time together.
It does not hold a second eight-frame loop: that would exceed the graphics cap.
Same-source backwards times retain the newer scan and its true age. Each frame's
actual drawn site list and remap quality drive its caption; a dark or missing
site is not relabelled clear.

The plate fills the 956×490 body at x34,y76 on the 1024×600 tabbed artboard.
The 25px secondary header and 34px gutters remain. The masthead subtitle is
restored; alert cases use an inline subtitle and compact masthead/alert band.
No rail, Range, Updated or Frames rows remain. Source picker/caption live at
top-left, continuous 372px horizontal legend at top-right, loop/track at
bottom-left, zoom at bottom-right. The single legend stays 372px for every source. Controls have >=44px
hit areas. Chrome is confined to y0–72/y418–490, clear of the r150 station disc.
The single note begins at y418 so it obeys the protected-band invariant.
Scrims use flat paper .82 / night .78 alpha, never filters; chrome has no ramp pigment.
The plate scopes secondary ink to 78% `--ink` / 22% `--paper`, so note, caption
and reads meet 4.5:1 even over worst-case echoes in both themes. The displaced
station accent is unchanged. Ramp colours are confined to echo data and its scale.

Playback draws pre-decoded ImageBitmaps onto one canvas. The visible canvas has
no `src`; a tick neither fetches nor decodes. A monotonic requestAnimationFrame
clock uses **350ms** steps and an **1100ms** newest hold. RainViewer short loops
use `clamp(2400/frameCount,110,180) * 350/110` ms, scaling the existing formula.
Each transition (including loop wrap) blends two real cached scans linearly for
120ms: previous weight `1-a`, next weight `a`. Two `drawImage` calls per composite
paint use nearest-neighbour sampling. Premultiplied additive composition preserves
translucent echo alpha; ordinary source-over would not give a linear mixture.
The blend is **temporal**, never spatial smoothing or a fabricated scan. The read
names the frame whose weight is ≥0.5 (next wins the tie). Reduced motion uses
hard cuts and an opt-in single sweep.

**v4.7 manifest continuity (no new wire fields):** A scheduled stamp advance
at unchanged source, site timeline, station, viewport bounds/zoom, render revision
and legend retains the previously published frames whose timestamps are within
3600 seconds of the incoming newest (inclusive). After discovery is validated,
the engine lists the newest as pending before native tile I/O: a warm 31-frame
MRMS hour becomes 31 listed / 30 complete, with only the expired oldest removed.
Every partial-tile publish carries that window; completion updates the newest in
place. Backfill retains historical `siteScans` exactly, even if fresh per-site
listings would choose different scan pairs. Site windows slide by actual scan
stamps. The shared 25-second pass deadline may leave newest pending for a
retry, but does not collapse history. Cold start and changed geometry still
publish their first measurement with a new window.

**v4.6 playback state machine, with v4.7 reconciliation:** `loaded` contains the
latest eight listed scans plus previously held scans omitted by a truncated
manifest while they remain within the incoming newest's hour. An omission alone
cannot close a composite or remove it from the decoded inventory. Source/site,
station, tile grid/zoom, render revision or legend changes release the old window.
When the complete manifest returns, normal latest-eight selection applies again.
`readyFrames` is the latest eight decoded composites in time order, and `cycle`
is the fixed playable snapshot for the underway pass. A missing middle
frame does not exclude older decoded scans. `good` names acquisition's newest;
`current` names the drawn scan. Acquisition remains newest-first.

| State / event | Display and next transition | Inventory / lifetime |
| --- | --- | --- |
| Cold acquisition | Paint newest as it decodes; start when `min(4, total)` scans are decoded (at least two to animate). | Count decoded composites, not tile coverage or a contiguous suffix. |
| Playing / manifest or decode arrives | Preserve current scan, blend and deadline; finish the fixed cycle and its old-newest 1100ms hold. | Stage the latest window and decoded arrivals; retain composites with the same frame key. |
| Wrap | Snapshot all currently decoded scans in the latest window; blend old-newest → slid-oldest, then progress to new-newest and its hold. | Late frames join here. Close aged-out composites once neither cycle, current nor blend needs them. |
| Paused / manifest arrives | Keep the displayed scan; update the window silently. Resume from that scan if retained, otherwise wrap to the slid-oldest. | Retain a displayed aged-out composite until playback leaves it. |
| Reduced motion | Same wrap adoption, no crossfade. One requested sweep stops at that cycle's newest; another Play uses the updated window. | Same decoded inventory and bitmap lifetime rules. |

`AS OF` updates immediately on the manifest and always names the newest measurement,
independent of playback or decode progress. Frame identity is render revision +
source + the frame's own stamp + site-scan identities; it excludes the manifest's
newest stamp. Source/revision are captured on each loaded frame so mutable manifest
fallbacks cannot re-key retained composites. Aging removes only the composite;
resident native tiles remain subject to the existing LRU. A wrap performs no fetch
or decode; a resident-native new scan requires compositing only. Newly unavailable
native inputs still require normal newest-first acquisition.

The loop cluster at left:12px/bottom:12px keeps its box through play, clear,
buffering, zoom, source swaps and frame staging; only an inactive radar tab or
no radar hides it. The 220×1px rail is permanent. Its 2px marker has `display:none`
with zero ready frames, appears at left:218px with one, and tracks frame index
with two or more. The existing .11s left transition remains the entire marker
animation, suppressed for reduced motion.

Play stays visible and **never disabled while the cluster is shown**. This
2026-09-14 user direction explicitly overrides Fable v3.2 K13/K14/K16's
content-based disabled-control rules. The 44×44px control uses ▶ and
`aria-label="Play radar loop"` for paused intent, or ❙❙ and
`aria-label="Pause radar loop"` for play intent, including buffering/fetching.
Every press changes the glyph and read immediately and gives a 120ms button
opacity dip (suppressed for reduced motion). Transport, gesture, stale and
visibility fences still control actual animation, independently of intent.

Before the start threshold, the read is `Buffering · N of M` for play intent or
`Paused · N of M` for paused intent, including zero. `M` is the actual window
inventory (capped at eight), not a hard eight; six scans per hour count as six.
Once playing, the read follows the drawn `HH:MM · newest` / `HH:MM · −N min`
without returning to buffering when a new scan arrives. Paused scan reads have
`Paused · ` prefixed. Complete clear history reads `No echoes · clear`, likewise
prefixed when paused. The tick tracks position in the cycle's ready set; newly
decoded frames change those positions only at the wrap. While paused it tracks
the updated decoded set, clamped to the oldest position if the held scan aged out.
The read has neither status role nor aria-live; the corner note describes pass
progress and owns that live region and its existing 600ms suppression. No scans
are duplicated or padded. The browser normally retains eight window composites,
plus outgoing frames still needed by a cycle/display/blend. A truncated manifest
also retains omitted in-hour composites (eight old plus one pending scan in the
stamp-advance regression); they remain subject to the existing 40MiB graphics
admission cap and are closed on expiry or release. One supplied frame is
static; clear data keeps the cluster visible and enabled. Hidden pages idle.
The loop never changes `#rad-base`.

The loop never changes geography. Leaving the tab or hiding the document closes
all echo bitmaps and cancels gesture/idle work; decoded basemap tiles remain.
Basemap: Natural Earth (public domain). Artifact provenance and reproducible
build: [tools/RADAR_BASEMAP.md](../../tools/RADAR_BASEMAP.md).

### Verification and hardware boundary (R)

`tests/test_radar_basemap.py` covers deterministic raster goldens, fixed palette,
exact theme pixels, antialiasing, even-odd holes, zoom membership, prewarm order,
immutable HTTP and the independent geography disk cap. Radar emitter suites
cover per-tile bytes/metadata, coverage masks, wrapped grids, budgets, providers
and security. `RADAR_NET_TEST=1` adds Seattle/Aberdeen live tile sets compared
byte-for-byte with remapping of their native provider responses.

`tests/verify_radar_headless.py` uses the real loopback handler in both themes.
It measures 200px pans, focal pinch/snap, tile request/decode activity, cached
first paint, ancestor identity, graticule pixels and absence of hatch pixels,
real capture/cancellation,
source transitions, failed-report recovery, overlay mutations, palette/legend
purity, playback and 60-second memory sessions. Its `chrome` checks cover all
27 Fable v4.3b copy/accessibility/geometry assertions in paper and night, plus
font-measured overflow drop order, name fallbacks, shared-timer delivery,
acquisition acknowledgement and stale-versus-new refresh failures.
`tests/verify_radar_v44.py` also runs the v4.5 pixel oracle in
`tests/verify_radar_v45.py`: all missing fractions, transparent acquired tiles,
site/MRMS boundaries, RainViewer, unknown masks, cached composites, and delayed
mid-acquisition stills in both themes must show no hatch element or pigment.
`tests/verify_radar_v46.py` drives the real renderer with a deterministic monotonic
clock in both themes: manifest during a blend, old/new newest holds, slid wrap,
resident-native stamp advances with zero tile fetches, bitmap retention/closure,
late middle decode, paused updates/resume, cold newest, read/tick/AS OF and reduced
motion single sweeps. `tests/test_radar_v46.py` covers identity and readiness in
the offline suite. Existing v4.4/v4.5 blend/pixel checks remain in force.
`tests/verify_radar_picker.py` preserves the real touch, click, keyboard, immediate
intent, 20-second polling-bound and integrated warm emitter-to-canvas checks,
including a Region return that refills the decoded loop with zero additional
native provider tile requests;
first-frame captions describe the old source and acquire `switching` after 600ms. Independent instrumentation
tracks canvas/bitmap allocation and draw calls. Headless wall times are reported,
not treated as Pi acceptance. The cold-outage fixture has no measurement frames.
Stills and timing logs default to `/tmp/wfp-radar-v41/`.

Run `python3 tools/benchmark_radar_kiosk.py > radar-v41-pi.json` on the panel
with the kiosk's CDP port at 127.0.0.1:9222. It evaluates the real page and prints
per-frame pan/pinch draw time/count, bilinear basemap draw time, cached first
paint, memory and GPU information. It restores the original camera afterward.
The GPU-enabled Pi measurements, not development-machine timings, decide the
4ms performance target. Renderer generation likewise needs Pi median/p95 evidence.

The v4.7 regression runner (`tests/verify_radar_v47.py`) uses only a 127.0.0.1
fixture server. In both themes it verifies zero-fetch same-stamp truncation,
bitmap lifetime and identity/expiry boundaries, then holds new-scan native HTTP
responses for 25 seconds and prints loaded/ready/read once per second. The eight
retained scans keep cycling and the new scan joins at a later wrap. Engine tests
(`tests/test_radar_v47.py`) inspect the first and every partial publish for warm
MRMS and per-site windows, including primary-deadline exit and retry.
