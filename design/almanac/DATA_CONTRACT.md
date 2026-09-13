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

## Radar v2 — sources, geometry and observed playback

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
is used directly. Single-site eligibility additionally requires the nearest
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

Each uncached MRMS candidate first probes the original archive with HEAD:
`https://mesonet.agron.iastate.edu/archive/data/YYYY/MM/DD/GIS/mrms/lcref_YYYYMMDDHHMM.png`.
Tiles use
`https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/mrms::lcref-YYYYMMDDHHMM/z/x/y.png`.
UTC rollover applies to both paths. MRMS's native raster covers longitude
−130…−60, latitude 20…55; crossing that domain sets `partialCoverage:true`.

The transport uses standard-library `http.client.HTTPSConnection`, with one
persistent connection per host per worker pass. Each host is resolved once
with `AF_INET`; successful addresses and resolver exceptions are cached for that
pass. Connections use the resolved IP while retaining the original TLS SNI,
certificate hostname verification, and HTTP Host. Reconnects reuse cached DNS.
Connections close in the worker's `finally` block. A synchronous system DNS
lookup itself cannot be interrupted by socket timeout; an over-budget lookup
is rejected when it returns and is never repeated within the pass.

All metadata, HEAD, tile, conditional and failing requests share a rolling
90-request/minute monotonic limiter across adapters. Per-source 429 cooldowns
honor numeric or HTTP-date `Retry-After`. Maximum request timeout is 10 s,
primary acquisition budget 25 s, full build budget 150 s, maximum 20 uncached
frame attempts per pass. No deadline was raised for v2. An HTTP error, invalid
PNG, incorrect dimensions, oversized body (>2 MiB), or solid opaque red IEM
placeholder stops that candidate immediately. Transparent data is a valid
clear frame. Negative cache TTL is 120 s; only complete 256×256 tiles contribute
to an atomically published crop. Native alpha is preserved without a paste mask.

Every adapter failure logs WARNING with source, exception, candidate timestamps
and elapsed time. A successful source change logs INFO `radar source SWITCH`.
A failed IEM pass retains a complete IEM frame under 600 s old at the same
station/zoom, without advancing its fetch or observation time. Otherwise it
tries RainViewer. A regressed same-source/station/zoom timestamp is rejected.
Both adapters failing retains the last complete presentation and retries on the
existing lifecycle-managed schedule. Radar failures never alter engine health.

### Verified single-site archive

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

The adapter requests UTC scans, validates timestamps, tries the newest complete
scan, and fills only listed history within one hour. History is capped at 31
source entries; the browser's separate memory cap is 16 decoded frames. A
nominal ~5 min volume can vary with scan mode; the live example was 6–7 min.
Two consecutive listed scan gaps over eight minutes set `scanningSlowly:true`.
`latestOnly:false` is honest here: historical scans were verified tile-fetchable.
A failed site acquisition publishes a mosaic with the site option disabled and
`reason:"not reporting"`.

Native indexed source PNG:
`https://mesonet.agron.iastate.edu/archive/data/2026/09/13/GIS/ridge/ATX/N0B/ATX_N0B_202609132140.png`.
N0B's two reserved codes give `dBZ=index/2-33`; representative stops at
5/20/30/40/50/60/70 are `#6c7daa`, `#52d6a2`, `#0c9110`, `#d6c704`,
`#ff8000`, `#ffffff`, `#b200ff`. These match the native PNG and
[IEM's reflectivity curve](https://github.com/akrherz/iem/blob/main/scripts/ridge/ReflectivityColorCurveManager.xml).
The product has its own `legend.id=iem-nexrad-n0b-v1` and `snow:null`.

MRMS retains its seven native representative stops and
`legend.id=iem-mrms-lcref-v1`. RainViewer retains Universal Blue (`colorId:2`),
its seven rain stops, and its snow color; native RGBA is unchanged in both themes.
Opt-in `RADAR_NET_TEST=1` tests verify all three source palettes.

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
"sourceMode": "mosaic", // authoritative selected presentation: mosaic | site
"siteId": null,         // selected site's ICAO when sourceMode=site
"sources": [
  {"mode":"mosaic", "available":true},
  {"mode":"site", "siteId":"KATX", "available":true, "reason":null}
],
"scanningSlowly": false,
"latestOnly": false,
"viewport": {"w":956, "h":490}
```

An unavailable site has reason `"no site in range"` or `"not reporting"`.
`nexrad` adds numeric `distanceMeters`; `id`, `name`, `distanceDisp`, `bearing`
are unchanged. `sources` describes selection options, not a network-health probe
for an unselected site. Actual reporting failure is discovered when selected.
The RainViewer presentation hides the picker; a CONUS mosaic without an eligible
site shows disabled `NO SITE`.

Auto targets 200,000 m across the **490px short axis**:
`round(log2(156543.03392*cos(lat)/(200000/490)))`, bounded 4–9 before each
source's own limits. Duvall resolves to z8, approximately 393×201 km, with a
25-mile scale bar. RainViewer clamps auto to z7. Manual desired zoom persists
independently of effective source bounds, allowing 4–10. `zoomCapped` is true
when the desired manual or auto level differs from the effective level,
including clamping to single-site's lower bound. `zoomSource` is MRMS,
NEXRAD, or RainViewer. Captions explain manual clamping and auto source limits.

Both axes, station center, effective zoom, and tile placements determine crop
identity. Single-site identity additionally includes ICAO to prevent same-minute
collisions between nearby radars. XYZ x wraps at the antimeridian and y clamps
at the poles. Bounds are inverse Mercator (`e<w` denotes wrapping). Marker is
`{x:.5,y:.5}` = (478,245); tiles, SVG and overlays share this geometry. The scale
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
a legacy payload falls back to 3×cadence). Header age suffix starts at
**2×cadence**, floor-rounded to minutes. Nominal cadence belongs only in the
source caption; scan age belongs only beside AS OF. Never label data LIVE.

### Input, layout and playback

Loopback `wx.json` polls may send `radarZoom=auto|4..10` and
`radarSource=mosaic|site`. Duplicate, invalid and non-loopback values are ignored.
The server writes changed values atomically with fsync, preserving durable
symlink targets. The launcher links both markers to
`${XDG_STATE_HOME:-$HOME/.local/state}/wfpiconsole/`, migrating existing runtime
preferences. A two-second preference stat watcher uses the same single-flight
worker and existing budgets. Inputs coalesce while a build is active. A request
is acknowledged only by the decoded authoritative payload, never by an old poll.

The plate fills the 956×490 body at x34,y76 on the 1024×600 tabbed artboard.
The 25px secondary header and 34px gutters remain. The masthead subtitle is
restored; alert cases use an inline subtitle and compact masthead/alert band.
No rail, Range, Updated or Frames rows remain. Source picker/caption live at
top-left, continuous 372px horizontal legend at top-right, loop/track at
bottom-left, zoom at bottom-right. Snow uses 274+10+88px. Controls have >=44px
hit areas. Chrome is confined to y0–72/y418–490, clear of the r150 station disc.
The zoom note begins at y418 so it obeys the protected-band invariant.
Scrims use flat paper .82 / night .78 alpha, never filters; chrome has no accent.

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
ending at newest starts when eight frames are ready (or a smaller supplied
history finishes decoding), then extends as older frames become ready.
Buffering disables Play and displays `Buffering · N of M`. Partial history
reports its actual oldest time; no duplicate/padded scans. The browser retains
at most **16 956×490 bitmaps (~29 MiB)** plus its static newest image/canvas.
Leaving the tab closes all history bitmaps; returning decodes the active history.

Play/Pause and station-local `HH:MM · −N min` / `HH:MM · newest` follow the drawn
scan, with a hairline progress track. One supplied frame is static; clear data
reads `No echoes · clear` with track/play hidden. Hidden pages idle. Reduced
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

PNG and SVG writes are atomic. Retired generations receive 120 seconds of decode
grace, then owned namespaces are pruned; unrelated files are untouched. Full
hour history is acquired only while viewed. At the wider crop, a frame commonly
needs 12–15 tiles, so cold history spans several rate-limited passes. Scheduled
checks remain 180s and retries 120s. `WFP_RADAR_DIR` stays below the static root.

Basemap: Natural Earth (public domain). Artifact provenance and reproducible
build: [tools/RADAR_BASEMAP.md](../../tools/RADAR_BASEMAP.md).
`tests/verify_radar_headless.py` defaults to `/tmp/wfp-radar-v2/`, retaining
cold-load, forecast-blend, both-theme source/zoom/basemap checks and adding
measured rAF intervals, hold, decode/src instrumentation, source picker states,
protected-zone geometry, touch targets and scrim contrast. Browser measurements
do not establish Pi hardware performance; the panel still needs human validation.
