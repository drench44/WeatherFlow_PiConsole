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

## Radar v3.2 — sources, geometry, acquisition intent and observed playback

`radar` is an independent side artifact. It never changes `ts`, `obsAgeSec`, or
engine `/health`. Missing radar or `available:false` hides the tab. A station
change clears the prior station's crop before any acquisition attempt.

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

### Acquisition and stability

MRMS metadata:
`https://mesonet.agron.iastate.edu/data/gis/images/4326/mrms/lcref.json`.
`meta.end_valid` must be UTC, an even minute, fresh, with `product:lcref` and
`units:0.5 dBZ`. Conditional requests/304 retain validators and revalidate age.
Candidates start at `min(end_valid, floor((now-300)/120)*120)`. The five-minute
readiness lag avoids IEM's as-yet-unrendered newest tiles. It is acquisition
latency, not a substituted valid time: every accepted timestamp has a complete
matching crop. The UI still reports actual age and may correctly mark MRMS stale.

During validation, an MRMS candidate probes the original archive with HEAD
unless that stamp already has a successful probe:
`https://mesonet.agron.iastate.edu/archive/data/YYYY/MM/DD/GIS/mrms/lcref_YYYYMMDDHHMM.png`.
Tiles use
`https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/mrms::lcref-YYYYMMDDHHMM/z/x/y.png`.
UTC rollover applies to both paths. MRMS's native raster covers longitude
−130…−60, latitude 20…55; crossing that domain sets `partialCoverage:true`.

As of 2026-09-14, newest discovery belongs to the source, independently of crop
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
for the emitter lifetime, independently of zoom, crop, transport session and
cadence; negative probes retain the 120 s retry TTL. All tiles still require the
same PNG validation and complete-crop rules. Failure of remembered newest tiles
(including a purged scan's 404 or failed site layer) discards knowledge and runs
one full source validation in the same pass, sharing its original deadline,
build limit, cooldown and request budget. Supersession is not a provider failure.
Idle prefetch reuses still-valid remembered stamps/listings. It may discover the
other mode: cached per-site listings for N0B, or MRMS metadata plus archive
readiness when no valid MRMS stamp exists. These requests share the background
reserve and cancellation checkpoints. No separate background worker is added; provider cadence,
readiness lag, observed timestamps, retained-failure handling and stale thresholds
are unchanged.

The transport uses standard-library `http.client.HTTPSConnection`, with at most
six leased connections per host. A connection stays leased until the response
body has been consumed or closed; newest frames use six tile workers, history
and idle prefetch use four. Sessions and successful IPv4 DNS results survive
worker passes and socket expiry. Each host is resolved with `AF_INET`; the
resolved IP retains the original TLS SNI, certificate hostname verification and
HTTP Host. DNS has its own 15-minute monotonic TTL. One caller resolves outside
the pool lock, with a per-host event sharing the result among cold callers;
another host can proceed meanwhile. Expired good addresses remain immediately
usable while one background refresh runs. A failed refresh keeps those addresses
and retries next pass; a cold resolver failure is also charged only once per pass.
System DNS cannot be interrupted by socket timeout; an over-budget cold lookup
is rejected on return, while cached callers never wait for resolution.

Idle sockets expire after four seconds or the smaller advertised Keep-Alive
window minus 250 ms. A reused GET/HEAD that fails before any response byte is
retried once on a fresh socket within the original deadline and atomic rate gate.
Partial responses and fresh-socket failures propagate. Connection: close drops
the socket; provider changes and shutdown close the session. A primary transport
hiccup retains the completed scan and retries in seconds; repeated failed passes
permit fallback. Transport errors do not negatively cache frames.

Validated native PNG bytes enter a 400-tile in-memory LRU before cancellation is
checked. Keys include source, site when applicable, scan timestamp, zoom and tile
X/Y. IEM native bytes do not depend on the console palette revision; RainViewer
keys also include its server-side colour scheme and options. A pan or restarted
crop reuses overlapping tiles without HTTP, even if the earlier crop never finished.
Truncated, oversized, placeholder and invalid tiles never enter this cache.

After newest publishes, viewed acquisition has three priorities, shared by intent,
scheduled cadence and budget-resume passes:

1. Build the newest eight loop slots (`RADAR_LOOP_FRAMES=8`), newest first. Reuse
   complete cached crops immediately. Multi-site retains its existing eight-slot
   cap; single-site/MRMS can still expose the full hour (up to 31 slots).
2. Publish `idle` and warm newest native tiles **before** deeper history.
   A fresh `radar_viewed` demand hint and at least 60 free requests above the
   34-request interaction reserve admit each source round (as for the original
   two-neighbour tier); every request in that round still preserves 34. Mosaic warms its own Z−1
   and Z+1 first. At Z≥7 with `sources[1].available`, it then discovers the sites
   selected at the same centre and warms their aligned newest scan set at Z,
   then Z−1/Z+1 within 7…10. Site mode warms mosaic newest at Z, then site
   Z−1/Z+1 within 7…10, then mosaic neighbours (within its source bounds), covering
   combined mode/zoom presses in both directions. No background history crops
   or publications are created. All targets use exactly the foreground LRU keys.
   Admission is once per target source/zoom/centre and site-scan set, including
   the actual aligned contributors and empty/non-reporting sites. Records are
   bounded to 400; interrupted rounds count once. Successful in-flight tiles
   remain cached when any intent cancels at tile boundaries. Acquisition failures
   discard affected discovery knowledge but do not change the published crop,
   trigger fallback or negatively cache it. Insufficient admission headroom
   schedules the next budget opportunity through the existing retry mechanism.
3. Build slots 9…31 only after at least 20 seconds of continuous live viewing at
   this geometry/intent. A frame starts only when its estimated requests fit
   above **both** the interaction reserve and 60 spare requests. The atomic
   per-request gate applies that same floor to tiles, archive HEAD probes and
   transparent transport retries, so concurrent starts cannot cross it. On a
   budget yield, retain published frames, return idle and schedule the next
   capacity opportunity; on an active view still under 20 seconds, wait for the
   remaining residence time as well. No worker sleeps while waiting for capacity.

The loopback server atomically writes `radar_viewing` as `{since,last}` UTC epoch
seconds during `view=radar` polls. Hidden documents omit that view signal.
A loopback off-tab poll removes that session
marker; a heartbeat gap of five seconds or more starts a new session. The emitter
requires `last` younger than five seconds and measures geometry residence with a
monotonic clock. New intents/geometries restart residence; scheduled stamps at an
unchanged geometry do not. This separate marker leaves the existing 900-second
`radar_viewed` hint and basemap/loop demand behavior intact. Missing, expired or
malformed session markers deny deep history. An inactive view uses the ordinary
retry cadence instead of polling the view gate in a tight loop.

All tiers check intent at tile boundaries; active requests drain into the LRU.
Deep history also rechecks continuous viewing there. Prefetch and loop requests
preserve the interaction reserve; deep history preserves another 60 slots. Before
each loop or deep frame, newest native entries (including warmed neighbours and
the other mode) are touched in the existing LRU so least-urgent history cannot evict the next press's tiles.
The cache size and compressed-byte representation are unchanged.

The existing 400-entry limit already fits three zooms × 15 tiles × two stamps
(90 entries), so it is unchanged. Only compressed PNG bytes are retained: memory
is the sum of their byte lengths plus dictionary/key overhead, not 400 decoded
RGBA images. For comparison, 90 decoded 256×256 RGBA tiles would occupy 22.5 MiB.
The transport's 2 MiB body limit gives a conservative 800 MiB raw-byte ceiling
for 400 maximally sized responses; normal radar PNGs are much smaller.

All metadata, HEAD, tile, conditional and failing requests share a rolling
240-request/minute monotonic limiter across adapters. Reservation is locked across
tile threads, so concurrent starts cannot overspend it. Per-source 429 cooldowns
honor numeric or HTTP-date `Retry-After`. Maximum request timeout is 10 s,
primary acquisition budget 25 s, full build budget 150 s, maximum 20 uncached
frame attempts per pass. No deadline was raised for v3. An HTTP error, invalid
PNG, incorrect dimensions, oversized body (>2 MiB), or solid opaque red IEM
placeholder stops new submissions for that mosaic candidate (or individual site layer).
Already-active requests drain, retaining their valid tiles; at most six newest or
four history/prefetch tiles are in flight. Transparent data is a valid clear frame. Negative cache TTL is 120 s; only complete 256×256 tiles contribute
to an atomically published crop. Native alpha is preserved within each tile
layer. Site layers use RGBA alpha compositing, with the site nearest the viewport
centre on top.

Every adapter failure logs WARNING with source, exception, candidate timestamps
and elapsed time. A successful source change logs INFO `radar source SWITCH`.
A failed IEM pass retains a complete IEM frame under 600 s old at the same
station/zoom, without advancing its fetch or observation time. Otherwise it
tries RainViewer. A regressed same-source/station/zoom timestamp is rejected.
Both adapters failing retains the last complete presentation and retries on the
existing lifecycle-managed schedule. Radar failures never alter engine health.

### Verified NEXRAD archive and multi-site composite

[IEM's RIDGE documentation](https://mesonet.agron.iastate.edu/GIS/ridge.phtml)
documents real scan listing and immutable timestamped XYZ tiles. Live curl
verification on 2026-09-13 established that **the listing takes `ATX`, not `KATX`,
and current reflectivity is `N0B`**; the current N0Q listing was empty.

Listing:
`https://mesonet.agron.iastate.edu/json/radar.py?operation=list&radar=ATX&product=N0B&start=2026-09-13T20:55Z&end=2026-09-13T21:55Z`
returns `{"scans":[{"ts":"2026-09-13T20:59Z"}, ...]}`.
Tiles:
`https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/ridge::ATX-N0B-202609132133/8/41/89.png`
and the `202609132140` version both returned real 256×256 PNGs with different
SHA-256 hashes. The `-0` suffix is a latest alias, **not an animation index**.
No frame is synthesized from latest aliases or guessed volume intervals.

At each site-mode pass, select every bundled site's 230 km spherical range circle
that intersects the viewport bounds (including corners and antimeridian wrapping).
`RADAR_SITE_MAX_COUNT=4` caps this set by distance from the **viewport centre**. `sitesConsidered` is the pre-cap intersection count; `sitesDrawn` is
the capped set size, including dark sites. `sites` lists this set in stacking
order, farthest first and nearest on top. This is geometric coverage, not a
claim that every site contributes echo pixels.

List the capped viewport sites and up to four station-nearest sites within
230 km, deduplicating requests. The latter fixed station set chooses the nearest
reporting timeline primary independently of panning and the viewport cap. Only
viewport sites supply layers. `siteId` names the timeline primary even if it
supplies no pixels. Timestamp monotonicity applies within source, primary,
center and zoom; recovery to a different primary may legitimately move backward.

An empty or stale listing means `not reporting`. Listing transport errors and
failed tiles mean `scan unavailable`; budget-skipped layers mean `deferred`.
No intersecting range circle means `out of view`. If no station-timeline site
reports or no viewport layer completes, mosaic fallback remains available.
Budget exhaustion before newest retains a fresh previous result as an idle deferral.

For primary slot `t`, each reporting secondary site supplies its newest scan
`ts <= t`, only when `t-ts <= 900`. No future scan or fabricated timestamp is
used. Per site, only viewport tiles whose bounding boxes intersect its 230 km
circle are fetched. Each site builds a complete clipped layer before it is alpha-composited
onto the 956×490 canvas; a bad tile discards that site's entire layer and logs
the failure. Other complete site layers remain usable. Reflectivity is remapped before pasting; native alpha and the
viewport-relative stacking order are preserved. Acquisition fetches nearest first
and holds at most four layers (about 9 MiB of working RGBA canvases), then pastes
farthest first. Global budget/deadline exhaustion discards the unfinished layer
and stops acquisition, retaining any complete layers as a valid degraded frame.
If no layer completed, the frame is withheld. Budget-skipped layers never enter
the negative cache. Complete layers survive a shared budget yield without
raising any limit.
A per-site negative cache suppresses failed layers for 120 s. Degraded crops
are keyed by the actual contributing pairs, so recovery cannot reuse a crop
missing a recovered site. Per-frame `siteScans:[{id,ts}]` records those pairs;
its IDs are ICAO codes, while provider requests use three-letter IDs.

The adapter validates timestamps, tries the newest usable primary slot, and
fills only the primary's listed history within one hour. History is capped at 8
source entries when at least two sites supply the newest slot, otherwise 31; the browser's separate memory cap is 16 decoded frames. A
nominal ~5 min volume can vary with scan mode; the live example was 6–7 min.
Two consecutive listed scan gaps over eight minutes set `scanningSlowly:true`.
`latestOnly:false` is honest here: historical scans were verified tile-fetchable.
External site acquisition failure may publish a mosaic with the site option
disabled and the specific reporting/acquisition reason preserved.

The site picker names an actual contributor plus the number of other actual
contributors (`KATX +2`), using the displayed frame's `siteScans`. A non-contributing
primary is identified separately as `timeline KATX`.
The caption is `NEXRAD · KATX +2 · ~5 min volumes · IEM / NOAA`, appending
`· KATX not reporting` when a selected site is dark. The plate's accessible name
lists contributing IDs. With at least two contributors, `#rad-over` draws their
geodesic 230 km arcs in `--rule-faint` (1px, dash 2 3), clipped to the plate.
Site centres inside the viewport have 2px `--ink-soft` dots and 11.5px labels
(dx5, dy13), omitted unless the complete label fits y76–412 and the viewport.
Station marker, rings, scale bar and displaced-station treatment retain their meanings.

### Payload and zoom

Existing snapshot fields remain: `available`, `reason`, `sourceId`, `provider`,
`attribution`, `attributionUrl`, `cadenceSec`, `frameSpacingSec`, `historyGaps`,
`historySpanSec`, `completeFrameCount`, `partialCoverage`, `center`, `zoom`,
`zoomAuto`, `zoomAutoLevel`, `zoomMin`, `zoomMax`, `zoomSource`, `zoomCapped`,
`zoomDesired`, `viewport`, `bounds`, `marker`, `metersPerPixel`, `scaleBar`,
`rings`, `frames`, `latest`, `frameCount`, `observedAt`, `observedTs`, `updatedAt`,
`fetchedAt`, `ageSec`, `stale`, `legend`, `nexrad`, and optional `basemap`.

Additions:

```jsonc
"sourceMode": "mosaic", // actually drawn: mosaic | site
"sourcePref": "site",   // durable choice; only a picker tap changes this
"sourceFallback": "site-zoom-floor", // otherwise null
"sitePreferred": true,  // sourcePref === site
"siteResumeZoom": 7,
"siteId": null,         // nearest reporting primary ICAO when sourceMode=site
"sources": [
  {"mode":"mosaic", "available":true},
  {"mode":"site", "siteId":"KATX", "available":true, "reason":null}
],
"sites": [],           // capped site set, bottom-to-top; also retained on site fallback
// Site record: {id, primary, ageSec, contributing, reason, lat, lon,
//               distanceMeters, viewportDistanceMeters, reporting, newestTs}.
// contributing describes the newest complete crop; reason is null,
// "not reporting", "scan unavailable", "deferred", or "out of view".
// newestTs/ageSec are null when the listing has no valid scan in its query window.
// reporting is the pass-time health decision; ageSec is recalculated each emit tick.
"sitesConsidered": 0,  // circles intersecting the viewport before the cap
"sitesDrawn": 0,       // capped set size (includes sites with reporting:false)
"intent": {"seq":41, "zoom":5, "center":"station", "source":"site"},
"refresh": {"state":"idle", "frameIndex":1, "frameTotal":1, "forSeq":41},
"scanningSlowly": false,
"latestOnly": false,
"viewport": {"w":956, "h":490}
```

Unavailable-site reasons distinguish `"no site in range"`, `"not reporting"`,
`"scan unavailable"`, `"deferred"`, and `"out of view"`.
`nexrad` adds numeric `distanceMeters`; `id`, `name`, `distanceDisp`, `bearing`
are unchanged. `sources` describes selection options, not a network-health probe
for an unselected site. Actual reporting failure is discovered when selected.
The RainViewer presentation hides the picker; a CONUS mosaic without an eligible
site shows disabled `NO SITE`.

Auto targets 200,000 m across the **490px short axis**:
`round(log2(156543.03392*cos(lat)/(200000/490)))`, bounded 4–9 before each
source's own limits. Seattle (47.6° N) resolves to z8, approximately 393×201 km, with a
25-mile scale bar. RainViewer clamps auto to z7. Manual desired zoom persists
independently of effective source bounds, allowing 4–10. `zoomCapped` is true
when the desired manual or auto level differs from the effective level,
with no lower-bound site clamp: the available mosaic makes `zoomMin:4`
for both preferences. A site preference below z7 serves the mosaic and publishes
`sourceFallback:"site-zoom-floor"`, without rewriting the durable source marker.
Returning to z7 automatically tries the site again. The picker always presses
the source actually drawn; the standing site preference has a dotted underline
while the mosaic is showing. The mosaic caption appends `· wider than KATX reaches`.
Tapping that site at z4–6 posts source=site and zoom=7 together; the site segment
is disabled when site acquisition is unavailable. `zoomSource` is MRMS,
NEXRAD, or RainViewer. Captions explain manual clamping and auto source limits.

Both axes, composite center, effective zoom, and tile placements determine crop
identity. Frame paths begin `almanac-reflectivity-v1/<sourceId>/`, separating
providers and invalidating all old native-colour crops. Site frame identity additionally hashes the ordered `(site ICAO, scan ts)`
pairs that actually composed it. Changing any contributing scan or the site set
changes the identity even when the primary timestamp is unchanged. Fully cached
identical frames require no tile requests. XYZ x wraps at the antimeridian and y clamps
at the poles. Bounds are inverse Mercator (`e<w` denotes wrapping). `center` reports the composite center. `centered:true` retains the exact station
view: `marker:{x:.5,y:.5}` = (478,245). With a center override, `centered:false`
and `marker{x,y}` report the station's fractional position under that crop,
using `radar_geometry.plate_point` and the compositor's integer paste registration.
The marker may be outside [0,1]. Tiles, SVG and overlays share this geometry. The scale
bar uses at most 25% of the short axis; ring labels are omitted outside y76–412.

Frames are oldest-to-newest, at most one hour. Each has `id`, UTC epoch `ts`,
station-local `at`, and `complete`; only complete frames carry `url`.
`latest` identifies the newest complete scan. `frameCount` includes incomplete
entries; `completeFrameCount` counts usable crops. `frameSpacingSec` is median
complete-frame spacing, not a promise of fixed scan cadence. `observedAt` is
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

### Intent and refresh

An immutable record pairs the pass-start intent with acquisition state. Before any
network request, each adapter computes the new viewport/bounds/marker/rings/scale,
ensures its basemap, and publishes `geometryOnly:true`, `frames:[]`, `latest:null`
with `refresh.state:"newest"`. This available payload describes the NEW geometry;
it does not relabel old frame pixels. `observedTs` and `observedAt` are null until
newest is ready. The last complete result is retained separately for recovery.
Every subsequent newest/history publication carries `geometryOnly:false`.

Every publication schedules `Clock.schedule_once(self._emit, 0)`, coalescing to
one pending immediate callback. Only the Kivy thread builds/writes the payload;
worker threads never read Kivy properties. The regular two-second emit remains.
No `progress` field is published.

```jsonc
"intent": {"seq":41, "zoom":7, "center":"station", "source":"site"},
"refresh": {"state":"history", "frameIndex":4, "frameTotal":8, "forSeq":41}
```

`zoom` is the requested integer or `"auto"`, before source caps. `center` is
`"station"` or `{lat,lon}`; `source` is the requested preference. `forSeq` is the
sequence read at pass start. `state` is `idle | newest | history | superseded | failed`.
`frameIndex` counts complete frames published in the pass (0 before the first,
then 1…N); warm history can advance it by several. `frameTotal` is the planned
work (1 before listing or when not viewed; otherwise the adapter's capped slots).
Success ends at `idle`; external newest failures end at `failed`. Local limiter
or cooldown deferrals remain `idle` when retaining a fresh previous result.
Geometry publication needs no request headroom. Before network acquisition,
require metadata plus a complete frame's tile headroom. If the limiter/cooldown
blocks it, retain the geometry-only view, publish idle, and retry at the necessary
request-window or cooldown expiry. Retained failure records describe the retained
intent and frame counts. Mid-newest budget exhaustion without any fresh retained
result remains failed; history budget yields remain idle.
The first eight loop slots start a frame only if it leaves at least 34 requests: two widest mosaics
of 5×3 tiles plus metadata and archive HEAD (or the current newest cost, if larger).
Deep history additionally leaves 60 free requests above that reserve and requires
20 seconds of continuous viewing, as specified in the acquisition tiers above.
Supersession queues one notice, but a newer intent's acknowledgement takes priority
over an older queued notice. Neither failure nor cancellation changes the last
successfully published crop.

The only status corner is `#rad-note`, a fixed 14px box at top418/right12 with
`role=status`, `aria-live=polite`, `pointer-events:none`. Priority: refresh,
existing upper zoom-cap copy, then `KATX resumes at zoom 7`. For 600ms after a
local intent post it stays suppressed. Progress reads `Refreshing · newest frame`
or `Refreshing · frame 4 of 8`; an older `forSeq` reads `Refreshing · restarted`.
A superseded notice holds restarted for 800ms, then uses the replacement phase.
Failure reads `Couldn't refresh · showing 17:12`, naming the visible scan and
persisting until a successful publish. Idle frees the line for the other claimants.
Only a 120ms opacity transition is used, disabled for reduced motion. There is
no Updating pill, spinner, second note, or `aria-busy` write on the interactive plate.

### Shared reflectivity palette

`lib/radar_palette.py` pins complete native colour inverses in `lib/data/`, with
byte-identical provenance fixtures in `tests/fixtures/`. MRMS's indexed formula
is `i/2−32`, N0B's is `i/2−33` with reserved codes 0/1 transparent. RainViewer
uses the published Universal Blue table; tiles request scheme 2 with options
`0_0`. These lookup tables supply intensity only, never precipitation type.

All three sources share the nine rain bands and use one crop raster for both
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
also divides clear air from the rain scale; there is no extra rule or word row.
The legend rebuild key includes the floor even when source and legend id agree.
Clear-air swatches use opaque `--rad-clear-air` composites (`#9F9FAA` paper,
`#5D606E` explicit or system night), because translucent paint over the legend
scrim would differ from the plate. Other segments keep their payload gradients.

Site captions append `· from 5 dBZ` before a dark-site `· KATX not reporting`
tail, inside the existing 544px maximum; mosaic captions gain nothing. The ramp
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

Complete frames carry `legend:{remapped}`, `remapped`, `unmatchedColors`,
`opaqueColors`, `unmatchedPixels`, `opaquePixels`, `ambiguousPixels`, and `revision`.
Counts describe clipped viewport portions of complete layers, excluding discarded
partial layers. More than 2% discarded opaque pixels or any cross-stop ambiguity
sets `remapped:false`; the displayed frame caption says `palette incomplete`.
Incomplete acquisition or remapping cannot assert clear conditions. The counts
and flag are embedded in the atomic PNG (`radarRemap`) and
read back on cache hits, including warm history. Cache identity includes the
native-table/remapper revision. Hits must fully decode a correctly sized PNG
and satisfy count/flag invariants and single-source LUT pixel validation; invalid
entries are removed and rebuilt. Spatial overlay keys omit changing ages and
health text, so routine polls do not rebuild geometry. The top-level legend's flag
refers to the newest frame. Failed/abandoned layers do not pollute these counts.

### Input, layout and playback

Loopback `wx.json` polls may send `radarZoom=auto|4..10` and
`radarSource=mosaic|site` and `radarSeq=<int>`. Sequence values must match
`^[0-9]{1,12}$` in ASCII. The page increases `intentSeq` for each posted intent
(held in `radarIntent.localSeq`); the server writes it to runtime `radar_intent`,
never following a durable symlink. Duplicate, invalid and non-loopback values are ignored.
The loopback server validates a complete `{seq,zoom,source,center}` intent and
atomically replaces one runtime `radar_intent` JSON record with fsync. Zoom and
source are also written through durable symlinks for restart persistence. Once
present, only the runtime record is consumed and watched by the worker; separate
legacy files are read only before the first runtime transaction. Pan remains transient.
The server rejects generations less than or equal to its current marker; exact
retries are idempotent. `X-Radar-Intent-Seq` on loopback wx.json responses gives
the browser the current authoritative marker even when emitted pixels are older.
The browser allocates above the maximum server/payload/superseded sequence,
retains a failed delivery's exact sequence and desired values for retry, and
flushes queued input as soon as an outstanding poll finishes. After posting, the
page polls every 400 ms until the payload acknowledges the target sequence, then
returns to two seconds; the fast window expires after 20 s. The delivery header
alone never ends this window. Geometry acknowledgement updates the map and controls;
only a decoded matching frame settles the echoes.

Every tile boundary,
between history frames, before replacing a temporary crop, and before snapshot
publication checks the stamp again. `_RadarSuperseded` cancels pending tile work,
drains active requests into the LRU, removes the in-progress temporary file,
publishes superseded once, and leaves the last published
result untouched (including a newest frame already published before history).
Negative entries for an aborted frame are not committed. The single-flight worker
releases its guard before scheduling an immediate preference check; the normal
100 ms watcher also observes the unserved stamp. This wakeup replaces any
120-second retry and shares all existing rate/cooldown budgets. A request
is acknowledged only by its authoritative payload, never by an old poll; its
echo transform clears only after matching-frame decode.

Loopback polls also accept `&radarCenter=<lat>,<lon>` or
`&radarCenter=station`. The strict decimal grammar is
`^-?\d{1,3}(\.\d+)?,-?\d{1,3}(\.\d+)?$` (ASCII digits), bounded by
latitude ±85.05112878 and longitude ±180. Duplicate parameters, invalid and
non-loopback values are ignored. `radar_intent.center` holds the validated runtime center beside `wx.json`;
**the runtime intent is never symlinked to durable storage**. Legacy
`radar_center` is read only before a runtime intent exists. `station` selects
the station center. The preference watcher observes whole-intent replacements. The emitter uses the override
for each adapter's viewport, basemap and crop identity, so a new center always
gets new frame IDs. Source eligibility, primary distance and automatic zoom remain
station-based; capped site selection follows the viewport centre. `metersPerPixel` still follows the viewport latitude's cosine:
east/west pan leaves it unchanged; north/south pan changes it slightly at fixed
zoom. Bounds, scale and rings are recomputed using that authoritative geometry.
Zoom persists across reboot; pan is transient.

Pointer Events on the plate share a captured pointer Map: one finger pans, two
pinch. The echo canvas and map/overlay layers have independent transforms;
`#rad-stack` itself never transforms. Controls/caption are target-gated and remain
fixed. Only the plate uses `touch-action:none`. Pan uses
independent CSS X/Y scales and the exact Mercator forward/inverse; no
meters-per-pixel approximation. World edges and a 1.5 viewport-diagonal station
radius resist at 0.3 and clamp on release. Pinch snaps to integer source limits
through the same `radarZoom` intent as the stepper; wheel/ctrl-wheel debounce at
120 ms. No pan inertia or focal-point geographic pinch is applied.

Playback freezes on the currently drawn image through gesture and commit.
The fence never clears or redraws the echo canvas. Each new gesture starts from
the held transform, including a fetch already in flight. Steppers, source taps,
recenter and gesture commits share a 120ms trailing intent debounce. A payload
arriving under pointers is evaluated once on release. Matching geometry-only
payloads immediately redraw basemap/graticule, rings, marker, scale and site arcs
without a map transform. The echo canvas keeps the currently drawn scan, reprojected
from its own original Mercator geometry at stale opacity (.66). A second gesture
composes independently from the new map and that held echo. The canvas is never
cleared for a geometry change. Only matching-frame decode replaces its pixels and
clears its transform, without a spring. Existing generation guards reject abandoned
decodes and older intents. History resumes playback as matching frames stream in.
Reduced motion removes the 160 ms commit spring.
When displaced, the station/rings travel together, the crosshair disappears, and
an accent outer ring identifies the station. A bottom-center `Recenter on station`
button sends `station`; 90 seconds without plate interaction does the same.
Leaving the tab or hiding the document cancels the preview and idle timer. Any
already posted intent can still finish on the server and is reconciled on return.

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

A new latest image decodes before committing pixels, geometry, legend and scan
time together. Active source switches also stage up to eight newest-ending
frames (or the available shorter history) before releasing the previous source. Generation fences reject abandoned callbacks. While switching,
the previous presentation stays visible. Same-source backward timestamps retain
the newer presentation and update its true age. A failed image load retains
old metadata, marks it stale, and backs off before retrying.

Playback draws pre-decoded ImageBitmaps onto one canvas. The visible canvas has
no `src`; a tick neither fetches nor decodes. A monotonic requestAnimationFrame
clock uses 110ms steps and an 1100ms newest hold. RainViewer short loops use
`clamp(2400/frameCount,110,180)` ms (the design's explicit formula). A 120ms
opacity dip marks rewind; no crossfade blends scans. A contiguous ready suffix
ending at newest starts as soon as two frames are decoded and extends as older
frames arrive. Play intent survives all acquisition/buffering fences; the second
frame starts playback without another press.
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

With fewer than two ready frames the read is `Buffering · N of 8` for play
intent or `Paused · N of 8` for paused intent, including zero frames. With two
or more it follows the drawn `HH:MM · newest` / `HH:MM · −N min`, prefixed by
`Paused · ` when paused. Clear history reads `No echoes · clear`, likewise
prefixed when paused. The read has neither status role nor aria-live; only the
corner note describes pass progress and owns that live
region and its 600ms suppression. Partial history
reports its actual oldest time; no duplicate/padded scans. The browser retains
at most **16 956×490 bitmaps (~29 MiB)** plus its static newest image/canvas.
Leaving the tab closes all history bitmaps; returning decodes the active history.

Play/Pause and station-local `HH:MM · −N min` / `HH:MM · newest` follow the drawn
scan, with a hairline progress track. One supplied frame is static; clear data
keeps the cluster visible and enabled. Hidden pages idle. Reduced
motion shows newest with a Play button; a tap runs one complete sweep and stops
on newest, with wrap dip and track transition disabled. The loop never changes
`#rad-base`, whose SVG is fetched once per hash into a bounded 16-viewport cache.

### Offline basemap and cache

Optional `basemap` carries `hash`, `url`, `coast`, `roads`. It uses the same
956×490 viewport, clipping/projecting both axes independently. Geographic paths
contain only validated semantic classes and integer SVG path data. Theme tokens
supply all pigment, including system dark mode. Missing/malformed artifacts
retain the graticule without changing radar availability. Generation is lazy
while `radar_viewed` is in `[0,900)` seconds, and warm files are reused off-tab.

Tab activation immediately posts `view=radar` on its wx.json request, aborting
an older in-flight poll if necessary. While waiting for at least eight complete
payload frames, polls run every 100ms, capped at 20 seconds independently of
geometry acknowledgement. The usual intent fast-poll cadence remains 400ms.

The 100ms intent watcher treats stale→fresh `radar_viewed` as a view-start
edge. A new `radar_viewing.since` session also wakes a return inside the
15-minute demand TTL. Repeated polls in one session do not schedule more passes;
a view event during a worker queues one single-flight pass. For unchanged,
fresh geometry, that pass restores validated cached frame descriptors and emits
history immediately with zero provider HTTP when eight crops exist. Cold or
stale caches use the existing acquisition path and shared budgets. Scheduled
provider validation remains unchanged; cached publication does not refresh the
observed or fetched timestamps.

PNG and SVG writes are atomic. The current source/geometry/legend revision keeps
all crop stamps within `RADAR_HISTORY_SEC` (3600 seconds, inclusive) of its newest
scan, viewed or unviewed. Off-tab cadence passes still fetch only newest and add
that crop to the retained hour (31 two-minute scans); older stamps and other
geometries/revisions retire. Abandoned generations retain the existing 120-second
decode grace. Pruning remains restricted to owned namespaces. Full
hour history follows the loop and neighbour priorities and the continuous-view
gate above. At the wider crop, a frame commonly needs 12–15 tiles, so deep history
spans several paced passes. Scheduled checks remain 180s and failure retries 120s;
budget/view-residence yields schedule their next eligible opportunity. `WFP_RADAR_DIR` stays below the static root.

Basemap: Natural Earth (public domain). Artifact provenance and reproducible
build: [tools/RADAR_BASEMAP.md](../../tools/RADAR_BASEMAP.md).
`tests/verify_radar_headless.py` defaults to `/tmp/wfp-radar-v2/`, retaining
cold-load, forecast-blend, both-theme source/zoom/basemap checks and adding
measured rAF intervals, hold, decode/src instrumentation, source picker states,
protected-zone geometry, touch targets and scrim contrast. Browser measurements
do not establish Pi hardware performance; the panel still needs human validation.
