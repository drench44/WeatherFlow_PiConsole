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

## Radar — hybrid sources and observed loop

The nested `radar` sibling is an independent side artifact. It never changes
`ts`, `obsAgeSec`, or `/health`. Missing `radar` or `available:false` hides the
Radar tab and returns an active Radar screen to Observations.

Every scheduled cycle tries **IEM MRMS lcref first** for eligible CONUS station
centers, even when RainViewer is currently displayed. A bundled coarse land
polygon, intersected with the MRMS raster domain, determines eligibility;
`nexrad` is only a caption. Alaska, Hawaii, territories, Berlin and Sydney use
RainViewer directly. Coast/border detail in this mask is approximate; it is not
legal boundary data or a radar visibility guarantee. No runtime geocoding.

| Source | `sourceId` | `provider` | Native `cadenceSec` | Stale strictly after |
| --- | --- | --- | --- | --- |
| IEM / NOAA MRMS | `iem-mrms-lcref` | `iem` | 120 | 600 s |
| RainViewer | `rainviewer` | `rainviewer` | 600 | 1200 s |

IEM metadata is fetched from
`https://mesonet.agron.iastate.edu/data/gis/images/4326/mrms/lcref.json` with
`Cache-Control: no-cache` and conditional validators when supplied. Its
`meta.end_valid` must be UTC, on an even minute, fresh, and accompanied by
`product:lcref` and `units:0.5 dBZ`. A cached/304 response still undergoes freshness
validation. Start at the earlier of that valid time and UTC now minus four
minutes rounded down to an even minute; probe backward in two-minute steps while
within the source's freshness threshold. Wall-clock enumeration only identifies
candidate URLs: a frame is accepted after archive existence and complete tile
validation, never by relabeling a latest alias.

Each uncached slot first checks the original archive with HEAD:
`https://mesonet.agron.iastate.edu/archive/data/YYYY/MM/DD/GIS/mrms/lcref_YYYYMMDDHHMM.png`.
Its XYZ tiles come from
`https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/mrms::lcref-YYYYMMDDHHMM/z/x/y.png`.
UTC date rollover applies to both paths. Live original PNG dimensions verified
2026-09-13 were 7000×3500, at .01 degrees/cell; its world file starts at pixel
center (-129.995, 54.995), giving outer bounds -130/-60 longitude and 20/55 latitude.
A viewport crossing those bounds carries `partialCoverage:true`. Opaque solid-red
IEM placeholders are rejected. Valid transparent crops are accepted as no echo.

Any usable fresh complete IEM latest wins, even with partial/missing history.
If none can be acquired within the primary budget, RainViewer is attempted in
the same cycle. RainViewer uses its public weather-map manifest's `radar.past`,
256px tiles, color 2, options `1_1`; nowcast is ignored. No snapshot or animation
mixes source pixels. If both providers fail, retain the last-good snapshot,
including source, legend, observation and refresh times. A station change clears
the old-location snapshot before acquisition, so a failure cannot mislabel it.

```jsonc
"radar": {
  "available": true, "reason": null,
  "sourceId": "iem-mrms-lcref", "provider": "iem",
  "attribution": "IEM / NOAA MRMS",
  "attributionUrl": "https://mesonet.agron.iastate.edu/ogc/",
  "cadenceSec": 120, "frameSpacingSec": 120,
  "historyGaps": false, "historySpanSec": 240, "completeFrameCount": 3,
  "partialCoverage": false,
  "center": {"lat": 47.61, "lon": -122.33},
  "zoom": 7, "zoomAuto": true, "zoomAutoLevel": 7,
  "zoomMin": 4, "zoomMax": 9, "zoomSource": "MRMS",
  "zoomCapped": false, "zoomDesired": null, "viewport": {"w": 480, "h": 480},
  "bounds": {"n": 49.36, "s": 45.80, "e": -119.69, "w": -124.97},
  "basemap": {"hash": "<viewport-hash>", "url": "radar/basemap/<viewport-hash>.svg",
              "coast": true, "roads": "dense"},
  "marker": {"x": 0.5, "y": 0.5},
  "metersPerPixel": 824.5,
  "scaleBar": {"distDisp": "50 mi", "meters": 80467.2, "pixels": 97.59, "unit": "mi"},
  "rings": [{"label": "50 mi", "px": 97.59}, {"label": "100 mi", "px": 195.18}],
  "frames": [
    // Oldest to newest, <= one hour. Incomplete entries have no URL.
    // ID includes product/palette revision, viewport hash and epoch seconds.
    {"id": "iem-mrms-lcref-v1/<viewport-hash>/1789243200",
     "ts": 1789243200, "at": "13:00", "complete": true,
     "url": "radar/iem-mrms-lcref-v1/<viewport-hash>/1789243200.png"}
    // ... remaining history entries omitted in this illustrative example
  ],
  "latest": "iem-mrms-lcref-v1/<viewport-hash>/1789243200",
  "frameCount": 31, // includes incomplete slots; completeFrameCount counts usable crops
  "observedAt": "13:00", "observedTs": 1789243200,
  "updatedAt": "13:04", "fetchedAt": 1789243440,
  "ageSec": 240, "stale": false,
  "legend": {
    "id": "iem-mrms-lcref-v1", "colorId": null, "colorName": "IEM MRMS reflectivity",
    "rain": [
      {"dbz": 11, "hex": "#a4a4ff", "label": "Weak"},
      {"dbz": 25, "hex": "#3366cc", "label": ""},
      {"dbz": 35, "hex": "#00cc00", "label": ""},
      {"dbz": 45, "hex": "#ffcc00", "label": ""},
      {"dbz": 55, "hex": "#d90000", "label": ""},
      {"dbz": 65, "hex": "#cc00cc", "label": ""},
      {"dbz": 71, "hex": "#ffffff", "label": "Strong"}
    ],
    "snow": null
  },
  "nexrad": {"id": "KATX", "name": "Seattle", "distanceDisp": "41 mi", "bearing": "N"}
}
```

Geometry above is illustrative. Bounds use inverse Mercator; `e < w` denotes an
antimeridian crossing. Tile x wraps and tile y clamps at the poles. Distances
follow `Units/Distance`: `mi`/`miles` becomes `mi`, otherwise `km`. Scale and rings
retain the existing geodesic calculations. `nexrad` is the closest of 160 bundled
WSR-88D sites within 285 miles, with station-to-radar bearing; otherwise null.
Auto zoom targets 256 km across 480px, clamped to 4–7 and recomputed each pass.
A manual preference can range from 4 to 9; each attempted source composites at
`max(4, min(desired, source.max_zoom))` (MRMS: 9; RainViewer: 7). Source fallback
recomputes the entire viewport while retaining the same build count/deadline.
No additional mapping/decoding dependency beyond Pillow.

| Zoom field | Type | Meaning |
| --- | --- | --- |
| `zoom` | int | Effective zoom of the published crop |
| `zoomAuto` | bool | No manual override is in force |
| `zoomAutoLevel` | int | Latitude-auto level, including when manually pinned to that level |
| `zoomMin` | int | Shared floor, 4 |
| `zoomMax` | int | Published source ceiling, MRMS 9 / RainViewer 7 |
| `zoomSource` | string | `MRMS` or `RainViewer`, for limit captions |
| `zoomCapped` | bool | Desired manual level exceeds the published source ceiling |
| `zoomDesired` | int or null | Exact saved manual intent; null means auto |

`zoomDesired` is an additive acknowledgement field: effective z7 alone cannot
confirm whether a capped z8 or z9 was saved, or whether z7 is manual or automatic.
The preference is a single line in `radar_zoom`, beside `radar_viewed` and the
emitter output. Missing, unreadable, malformed, `auto`, and out-of-band values
select auto. An override is never rewritten by the emitter, including during
source fallback. A saved z8 clamps to z7 on RainViewer and restores to z8 when
MRMS succeeds. A failed acquisition retains the complete old presentation,
including its zoom metadata; it never relabels old pixels with requested geometry.

While Radar is active, pending input appends `&radarZoom=<4..9|auto>` to the
ordinary `wx.json` poll. The server parses a single value with `parse_qs`, trusts
only loopback, validates it, and atomically writes only changes. Duplicate,
malformed, and non-loopback preferences are ignored; I/O errors never interrupt
polling or affect `/health`. Reset explicitly writes `auto`. No localStorage is
used. Requests repeat until the decoded payload acknowledges the exact desired
intent and effective zoom; Auto/Manual and source bounds then follow that payload.
Rapid presses coalesce to the latest input; a reset superseding an in-flight
manual request must itself be sent before it can be acknowledged.

The kiosk feed lives in `/tmp`, so the launcher links its `radar_zoom` sibling to
`${XDG_STATE_HOME:-$HOME/.local/state}/wfpiconsole/radar_zoom`, migrating an existing
runtime preference on first setup. The server resolves this link before atomic
replacement (and fsyncs the new file). The link is recreated after reboot; the
stored intent survives. This is per station installation, like the feed itself.
Custom server setups must likewise place the sibling on durable storage or link
it to durable storage to retain it over reboot.

A lifecycle-managed preference stat watcher runs at the emit interval (2 s).
Changes enter the existing single-flight radar worker; changes during a build
coalesce until that worker finishes. There is no alternate compositing path or
budget reset. The 90 requests/minute limit, per-source 429 cooldowns, deadlines,
frame-build cap and 900-second view TTL all continue to apply. Failures retry on
the existing schedule; confirmation can take longer under backpressure.

The rail contains 44px minus/plus steppers, Auto/Manual mode, and a reset visible
in Manual. Pending input dims the mode. Plus is disabled at the live source
ceiling and minus at the floor. The source ceiling/capped caption explains the
limit without changing saved intent. Keyboard `+`/`=`, `-`/`−`/`_`, and `0` work
only on the active Radar tab, respecting text inputs and modified shortcuts.
Buttons support Tab, Enter and Space with visible focus. The control uses only
`--rule`, `--ink`, `--ink-soft`, and `--sans` theme tokens; no accent chrome.
Zoom changes rebuild scale/rings/Range and history from the effective viewport,
preserving loop pause state; the loop hides until new history is ready. Echoes
are server-rasterized, never CSS zoomed. Geographic basemap and optional pinch
interaction are outside this build.

All numeric frame timestamps and `fetchedAt` are UTC epoch seconds. `observedAt`
is the newest **complete frame's valid time**, while `updatedAt` is the last
successful active-source refresh completion time, formatted station-local HH:MM.
`frames[].at` is independently formatted with the station timezone, including
DST changes. `ageSec`/`stale` use frame time and the active source's threshold.
A successful unchanged metadata check with a matching cached crop advances
`fetchedAt`/`updatedAt`, but not `observedTs`/`observedAt`. Failed refreshes advance
neither. If a newer IEM slot fails and back-probing finds the same displayed
frame, it keeps its previous refresh time. Pure backfill publications reuse the
latest acquisition's refresh completion time, even if filling history is slow.

`frameSpacingSec` is the median positive gap between complete frame timestamps,
null with fewer than two frames. `historySpanSec` and `completeFrameCount` report
actual usable history. `historyGaps` marks non-native spacing within that history;
missing leading history alone is warm-up, not an internal gap. `frameCount`
retains its inclusive semantics. Both sources are bounded by
`RADAR_HISTORY_SEC=3600` relative to their accepted latest frame (31 native IEM
slots or normally seven RainViewer frames). Missing slots have no invented scans.

IEM's seven representative RGB stops were verified against the
[IEM native colortable](https://mesonet.agron.iastate.edu/GIS/rasters.php?rid=4)
on 2026-09-13 and independently matched the downloaded original PNG's indexed
palette. Their indices are 86, 114, 134, 154, 174, 194 and 206, with
`dBZ = index/2 - 32`. They are sampled native colors, not bin boundaries or an
interpolated replacement palette. `rain` is a legacy field name for reflectivity;
MRMS does not supply precipitation type, so `snow:null` hides the Snow legend row.
RainViewer retains `legend.id=rainviewer-universal-blue-v1`, `colorId:2`,
`colorName:Universal Blue`, rain stops 5 `#92887164`, 20 `#00a3e0ff`,
30 `#005588ff`, 40 `#ffaa00ff`, 50 `#c10000ff`, 60 `#ff77ffff`,
65 `#ffffffff`, and Snow `#7fbfffff`. Native RGBA stays identical in both themes;
pasting without an alpha mask preserves translucent echoes. Both provider
legend-fidelity network tests require `RADAR_NET_TEST=1`; normal pytest is hermetic.

The worker starts after 60 seconds and checks every `RADAR_CHECK_INTERVAL=180`
seconds. Lifecycle-managed failures and unfinished viewed history retry after
120 seconds, coalescing through the existing worker/retry registry. Every HTTP
attempt (metadata, archive HEAD, tiles, failures and conditional requests) uses
one rolling `RADAR_REQUESTS_PER_MIN=90` limiter shared across both providers.
HTTP timeout is at most 10 seconds, primary acquisition budget 25 seconds,
overall build deadline 150 seconds, and at most 20 uncached frame build attempts
per pass. Budgets and provider cooldowns use monotonic time. A 429 stops that
provider's burst and honors numeric or HTTP-date `Retry-After`; fallback may
proceed within the shared limit. Failed slots are negatively cached for 120
seconds. Complete cached crops imply positive archive availability.

The limiter yields work to a later scheduled pass rather than sleeping inside
the worker when its window is full. Latest is published promptly before
newest-to-oldest backfill, and every publication is a complete `_RadarResult`
assignment with source metadata attached. No incomplete PNG becomes a cache hit.
Cache paths are `radar/<source-product-palette-revision>/<viewport-hash>/<ts>.png`;
the hash includes station coordinates, crop tile placement, zoom and size.
Writes use a same-directory temporary PNG and `os.replace`. Only owned source
namespaces are pruned after success. Retired displayed files receive at least
120 seconds of decode grace; unrelated PNGs and legacy timestamp-only files are
left alone. Total failure keeps the last-good files. `WFP_RADAR_DIR` (default
`~/almanac_web/radar`) must remain below the existing static web root.

History demand is unchanged. While Radar is active, `wx.json` polls append
`&view=radar`, independently of `r=1`. The trusted loopback signal writes
`radar_viewed` beside the emitter's output path. Its age must be in `[0,900)`;
missing, malformed, non-finite, future and expired markers mean unviewed. The
marker is read before any unchanged-frame fast path. Unviewed cycles only
maintain the latest crop; opening the tab warms history on the next cycle even
if the latest timestamp is unchanged. For a nine-tile crop, cold unviewed IEM
costs 11 requests (metadata + archive HEAD + nine tile GETs), RainViewer costs
10; warm unchanged checks cost one metadata request and no HEADs/tile GETs.
Viewed cold IEM history spans multiple passes under the shared request ceiling.

The console stages a new presentation until its newest image decodes, then
switches the decoded image node, geometry, legend, source attribution and times
in one turn. Source/product, legend revision, viewport and frame identity form
the presentation key. Generation tokens fence latest/history callbacks; late
loads from abandoned generations cannot overwrite the display. While staging
or after failure, the old presentation stays intact and its loop stops. A
failed load marks retained data stale; retry delay is 120 seconds. A successful
same-frame refresh updates metadata without replacing/downloading the image.
Only one accepted generation's decoded history is retained.

Three times have three homes, using existing theme tokens:

- Header `AS OF HH:MM`: newest complete frame; label 11.5px sans caps, value 17px
  serif tabular. Fixed during playback, also visible in clear/stale states.
- Rail `UPDATED HH:MM`: successful refresh, replacing the redundant Observed
  row. `FRAMES · <span> · <spacing>` reports actual loop span and median spacing,
  with `History loading`/gaps when appropriate. Fewer than two complete frames
  say `Source cadence ~2 min` (or `~10 min`). `Past hour` requires a full hour.
- Play/Pause caption: `−N min ──── newest` during playback, relative to the newest
  frame, never “now”. Pausing holds that selected frame and displays its own
  absolute station-local time, preferring `frames[].at`.

The loop steps every 550ms, holding newest 1500ms, and idles off-tab, when the
page is hidden, under reduced motion, on stale data, or without echo-bearing
history. Transparent latest crops say “No echoes shown”; this does not claim
coverage. Stale echoes retain their palette at .55 opacity, with the existing
accent reserved for stale status. Legacy payloads without the new fields retain
the RainViewer legend/view, hide unavailable Updated/cadence rows, and use the
previous frame-time derivation when `frames[].at` is missing.

`tests/verify_radar_headless.py` checks source-switch staging, per-paint image
palette agreement, abandoned/failed loads, same-frame refresh, loop cycling and
pause/idle, clear/stale and legacy payloads. Its 1024×600 layout checks and
screenshots include alerts, both themes, seven IEM legend rows (plus RainViewer's
Snow row), Updated and cadence text, keeping all Radar content above y=568.

The zoom verifier additionally drives persistent input through the real loopback
server and hermetic emitter transport at Seattle, Berlin, Sydney, mid-ocean and
antimeridian sites, in paper and night themes. Screenshots default to
`/tmp/wfp-radar-zoom/`. It checks pending/confirmed, Auto/Manual, reset, ceilings,
source clamp/restore, keyboard/focus, refresh persistence, geometry, pause/history,
and clear/stale states. These are deterministic UX fixtures, not live weather.

### Offline geographic basemap

Optional `radar.basemap` contains `hash`, `url`, `coast` and `roads`. The hash
is the echo viewport identity (station latitude/longitude, effective zoom,
480px size and tile offsets), shared across providers at the same zoom. The
relative URL is `radar/basemap/<hash>.svg`. `coast` means any visible ocean or
lake; false leaves the plate ground as land. `roads` is `dense` when a visible
North America supplement road survives clipping, `sparse` for global roads
only, or `none`. These hints never alter the reflectivity legend.

The SVG is generated lazily on a viewed radar worker cycle using the same
`radar_viewed` TTL as history warm-up. Existing files may be reused off-tab; no
new file is generated then. Files are atomically published and retired with
the same decode grace as echo crops, within the owned `radar/basemap` namespace.
Basemap failure omits this optional object, preserves radar, and never changes
engine `/health`. Missing object, failed fetch, or malformed SVG leaves the
existing graticule. A successful empty SVG is valid for bare land.

The browser fetches only while Radar is active, once per hash in its bounded
16-viewport cache (including failures). It imports only validated class/path
nodes into `#rad-base`. Geometry keys include the basemap hash; frame animation
never fetches, replaces or mutates the basemap. Ocean/lake/coast/admin0/admin1/road
classes resolve exclusively through existing theme tokens, including system
dark mode. No baked colours, accent, raster recolouring or runtime geo dependency.

Basemap: Natural Earth (public domain). Source layers, simplification, artifact
format and reproducible build commands: [tools/RADAR_BASEMAP.md](../../tools/RADAR_BASEMAP.md).
The standalone headless verifier now defaults to `/tmp/wfp-radar-basemap/`,
including paper/night before/after, missing/legacy and five-site screenshots.
