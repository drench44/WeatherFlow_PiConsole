# Changelog

Changes in this fork's Almanac work, newest first. The upstream WeatherFlow
PiConsole keeps its own release notes; entries under **Core** below are fixes
to shared upstream code that the classic console benefits from too.

## 2026-09-13

### Radar
- **Stale no longer false-alarms on a healthy 2-minute feed.** The stale flag was a
  bare 3× cadence (6 min for the mosaic), but the emitter never shows an MRMS frame
  younger than ~5 min (IEM renders on demand and returns 503 for newer minutes), so
  the freshest-possible frame already tripped it — the console read STALE, in alarm
  red, almost constantly. Stale now uses a per-source threshold set above each
  source's inherent latency (MRMS 10 min, single-site 15, global 20); "stale" once
  again means the feed actually stopped, not that the source runs its normal few
  minutes behind. Exposed as `staleSec` so the console stays consistent.
- **Radar v2: the radar is the star of its tab.** The plate grows from a 480px
  square to the whole 956 × 490 body — twice the echo area, all of it horizontal,
  where weather comes from — and the rail is gone: its eight elements become
  four quiet overlays confined to the top and bottom bands (nothing within 150px
  of the station marker), the dBZ legend turns into a horizontal ramp with ticks,
  and RANGE/UPDATED/FRAMES rows are cut (the scale bar states distance; scan time
  and lateness live only in the "As of" line). Overlays sit on a flat, theme-aware
  scrim (no filters — the Pi's GPU can't afford them) that clears WCAG 4.5:1 over the
  worst-case echo colours in both themes; no accent on any chrome.
- **Smooth playback.** Frames are pre-decoded to bitmaps and drawn on a canvas by a
  clock-referenced animation frame — a tick does no fetch, no decode, no image-src
  write and never touches the basemap. About nine frames a second (110 ms) with a
  1.1 s hold on the newest, hard cuts between scans (a crossfade would paint a state
  the radar never observed), one 120 ms dip on the wrap. Buffering is a visible
  state ("Buffering · N of M"); partial history loops what exists and labels its true
  start; reduced-motion gets a static newest frame and a one-sweep play button. To fit
  the wider plate on a Pi the decoded loop holds the last 16 frames (~32 min on the
  2-minute feed), and the caption reports the span it actually shows.
- **Tighter, source-aware default zoom.** Auto now targets 200 km across the plate's
  height (zoom 8 at mid-latitudes — half the ground scale of before, with the same
  horizontal reach), and may go finer where the live source can render it: the US
  mosaic to 9, a single site to 10, the global fallback stays at 7 with the existing
  "closest view" note.
- **Single-site NEXRAD, switchable.** A `MOSAIC | KATX` picker on the plate swaps the
  composite for the nearest radar's own super-resolution base reflectivity (IEM's
  per-scan RIDGE tiles, animated over real scan times, ~5-minute volumes, native
  palette verified against IEM's colour curve). The picker is honest about its
  states — pending, no site in range, site not reporting, hidden entirely on the
  global fallback, a failed switch reverts with a note — the choice persists across
  restarts, and stale is judged per source (three scans, not a fixed clock).
- **The 2-minute feed now actually keeps up.** Root causes fixed, not a deadline
  raised: one TLS connection and one DNS lookup per host per pass instead of a fresh
  handshake per tile (a 9-tile frame in ~1.2 s, down from ~3 s on a good link and far
  worse on the Pi), the not-yet-rendered freshest minutes are skipped and a bad tile
  ends its candidate immediately, a failed pass keeps the last good frame instead of
  flapping to the 10-minute source, a newer scan can never be replaced by an older
  one, and every fallback and source switch is now logged with its cause. The "As
  of" line carries an age suffix only when a frame is late (≥ 2 × cadence).
- **Diagnosed why the 2-minute US feed loses to the fallback on IPv6-broken
  networks.** IEM's host advertises an IPv6 address; where IPv6 is a black hole
  (as on the test Pi — `curl -6` times out, `curl -4` answers in 0.23s), Python's
  urllib has no Happy-Eyeballs and stalls ~10s on the dead address before falling
  back to IPv4, so the IEM adapter starves and the console silently stays on the
  10-minute global source. The cure is to reach IEM over IPv4 (disable IPv6 on the
  appliance). The primary-attempt cap stays tight (25s) — a longer cap only delays
  the fallback and leaves the plate empty longer when a source is unreachable; it
  was never the real limit. (Reverts a 55s bump from earlier the same day that had
  mis-attributed the stall to wifi bandwidth.)

### Console
- **Temperature curve: the forecast now begins where the temperature is.** The
  hourly forecast line used to weld its start onto the sensor reading and then run
  to the model's first future hour — so whenever the sensor and the model disagreed
  (sensor 55°, model 53° in rain) it drew a drop-and-recover the forecast never
  predicted, and printed a phantom low label ("53° · 14:00"). A first fix anchored
  the forecast at the model's own now-value with a vertical seam; on the live panel
  that read as a cliff to the chart floor — the same false story. The forecast now
  starts at the current reading and blends onto the model over the next few hours
  (a standard nowcast bias correction: the sensor is the better guide near-term,
  the model for the rest of the day), with a smoothstep decay so there's no kink at
  the join, and a horizon that ends exactly at the model's first turning point so
  the drawn peak *is* the model's peak. Result on the day that exposed it: 55.0 →
  55.1 → 55.1 → 55.6 → 56.5, no dip, and "57° · 17:00" still agrees with HIGH 57°.
  Extreme labels print only where the drawn and model values round the same, so a
  label can never contradict the HIGH/LOW row. Observed-only rendering (no hourly
  data) is byte-identical to before. Guarded by a headless check that fails on the
  old rendering.
- **No more phantom forecast on a cold boot.** The page ships as the design
  artboard, and until the first data frame arrived it kept showing the artboard's
  sample values — 64.0°, "Rising 4.6° per hour", **Low 50° / High 82°**, "Clear &
  Sunny", a July date, sunrise 05:43 — as if they were real, behind only a small
  STALE mark. On a freshly rebooted Pi (or with the engine down) that read as a
  plausible, wildly wrong forecast. The console now paints its no-data pose
  (dashes everywhere, gauges neutral) before the first poll, using the same
  missing-value rules every panel already follows, so it never shows a number it
  hasn't received. The headless verifier now guards this (a cold load with no
  `wx.json` must show no sample values).

## 2026-09-12

### Console
- **New Radar tab.** A regional radar mosaic from RainViewer, centered on the
  station so it works anywhere on Earth (not just the US). The echoes keep
  RainViewer's true reflectivity colors with a real dBZ scale and snow shown
  distinctly from rain — the one deliberately bordered exception inside the
  four-pigment console — framed as an instrument with a console-drawn basemap
  (range rings, scale bar, station marker), light and dark both first-class.
  Honest states: fetching / no echoes shown / stale; the tab hides where there
  is no location. Zoom adapts to hold a consistent ~256 km view at any latitude
  (clamped to the free tier). The emitter keeps only the latest frame current
  when the tab is unwatched and builds the full history only when it's been
  viewed — an idle radar tab costs one frame's fetch, not thirteen. Radar runs
  off-thread and never affects engine health.
- **Radar animation loop.** While the Radar tab is open the past hour of frames
  plays as a loop (oldest → newest, hold on the latest), so motion reads as "now";
  a Play/Pause control and a relative "−N min → newest" counter sit under the plate.
  The loop touches only the echo layer — never the basemap — pauses off-tab and
  under reduced-motion, and never fetches per frame.
- **Finer cadence where it's available.** For US stations the radar now leads with
  IEM's MRMS reflectivity mosaic (~2-minute frames), falling back to RainViewer's
  10-minute global mosaic elsewhere or when the primary is unavailable — it always
  tries the finest source first. The plate shows both an "As of" scan time and a
  distinct "Updated" refresh time (RadarScope-style), plus the source's frame
  cadence, and the legend switches to match whichever source is live (IEM's native
  reflectivity table or RainViewer's Universal Blue). Source hand-offs stage
  atomically so a switch never shows a half-loaded or mislabeled frame.
- **Persistent radar zoom.** A quiet +/− stepper in the rail with a reset-to-auto,
  so you can pin the radar closer or wider than the latitude-auto default; the level
  is saved per station and survives refreshes and reboots (kept in durable state,
  not tmpfs). The control is honest about each source's real range — RainViewer's
  free tier caps at zoom 7, the US MRMS feed reaches 9 — so it never offers a step
  that does nothing, and a level set closer than the live source reaches shows that
  source's closest view while remembering your intent (a saved zoom 8 shows 7 on
  RainViewer and restores to 8 when the US feed returns). Keyboard-operable (+/−/0),
  both themes, no accent on the chrome. Zoom rides a loopback preference the emitter
  reads, so the plate re-composites server-side at the chosen level (no CSS scaling,
  no blur); scale bar, range rings, and the loop all follow, and a zoom change
  respects the same demand-gate, rate limiter, and cooldowns as every other refresh.
- **Geographic basemap under the echoes.** The radar plate now shows real geography
  — coastline and water, country and state/province borders, and major roads — as
  hairline themed lines beneath the reflectivity, RadarScope-style, so a storm reads
  against the land instead of an abstract grid. It's drawn from bundled Natural Earth
  1:10m vector data (public domain, ~3.6 MB), so it needs no API key and works
  offline, anywhere on Earth — a landlocked station shows borders and roads, a coastal
  one shows the shoreline, mid-ocean stays calm water. The emitter projects and clips
  the geography to the exact station-centered viewport (pixel-registered to the
  echoes) and writes a class-tagged SVG the console colors entirely through its own
  palette tokens, so both light and dark are correct with no accent on the map
  furniture. It's generated once per viewport (never per animation frame), only while
  the tab is watched, cached and pruned like the echo frames, and it falls back to the
  old graticule if anything is missing — radar never breaks. The abstract lat/lon grid
  gives way to the basemap when it's present. Build tooling and provenance live in
  `tools/RADAR_BASEMAP.md`.

### Core (shared with the classic console)
- Sager Weathercaster no longer fails on a clear sky: it keyed on CheckWX's
  parsed `clouds` array, which is omitted for CLR/SKC, so on a clear day the
  forecast errored with "Missing METAR cloud information." It now selects the
  nearest station whose raw METAR carries a sky group (clear codes included).

## 2026-09-08

The Pi 4's DSI panel was replaced with a 1024×600 HDMI **capacitive
touchscreen** (Elecrow 7"), which surfaced a set of touch/layout issues.

### Console
- Navigation tabs (Observations / Moon & Sky / Lightning / Sager) can be
  enabled for a touch kiosk. The tab bar is hidden by default; the launcher
  adds `tabs=1` (env `WFP_TABS=0` opts out).
- When the tab bar is shown, the Observations screen now fits the panel: the
  forecast chart yields its slack height, the barometer panel reflows so the
  value/trend and both charts keep room, and the AQI category/trend wraps to a
  full line instead of truncating ("Moderate to…"). All of it is scoped to the
  tabbed mode, so the default single-dashboard layout is unchanged.

### Kiosk
- WiFi keepalive now probes a stable LAN **peer** (the other Pi) instead of the
  default gateway. The gateway stays reachable while the box is isolated from
  the rest of the LAN, so the old probe never fired during an hour-long dropout.
  Adds a recovery cooldown, run serialization, and a post-recovery check that
  only clears the failure count once the peer actually answers.
- Display/touch setup documented for the new panel: 1024×600 is the console's
  native artboard (fit 1.0), the `vc4-kms-v3d` driver and native-mode rule
  (never force a higher mode a small panel only downscales), and the labwc
  touch mapping — labwc matches the libinput device name exactly, and a rule
  pinned to a connector or device that is later swapped out silently breaks
  touch.

## 2026-09-06

Two independent audits of the fork (an initial one, then an adversarial review
of the fixes) drove this release. The full findings and review are in
`design/almanac/AUDIT-2026-09-06.md`.

### Console
- Freshness is measured at the source. The masthead mark now reads **STALE** when
  nothing new is reaching the screen and **SILENT** when frames arrive but the
  station has stopped reporting (`obsAgeSec`, from the observation's own epoch).
  Polling is single-flight with a 4 s deadline; a hung request flags STALE
  within about 12 s instead of never.
- The hero forecast curve's forward segment follows real hourly forecast
  temperatures, with the high and low labelled at the hours they occur. It no
  longer draws a rise back to a high that already happened.
- Lightning takes the Sun & Sky slot while strikes are active; the rainfall
  panel stays visible in a storm.
- Rain volume slews toward the measured rate (columns count up fast, down slow)
  and rain fades in and out instead of switching.
- Light rain never reads as dry: a 10-minute rolling window bridges the haptic
  sensor's zero minutes between trace readings.
- AQI marker and colour bands share one scale; inHg keeps two decimals; the
  barograph plots samples by their epoch on the producer's 24 h window; long AQI
  text ellipsizes; the lightning tile follows the distance unit.
- Barometer outlook uses a compact vocabulary that fits its row; the band's
  TODAY hi/lo come from the same provider as the hero's LOW/HIGH.

### Data engine
- Non-finite numbers (NaN/inf, including formatted strings) can no longer stop
  the JSON feed.
- Every scheduled timer is registered and cancelled by `stop()`; each provider
  gets one in-flight fetch and one retry chain (a day of outage used to
  accumulate 25 retry chains). Provider results publish as one immutable
  snapshot.
- Cached NWS alerts are re-expired every emit; tomorrow's hint and the AQI
  daily trend are chosen by calendar date, never by array position.
- Payload carries `obsTs`/`obsAgeSec`, `lightningTs`, per-provider fetch ages,
  `fcHourly`, `dayStartTs`, and per-day forecast precipitation (`qpf`).
- `/health` reports `degraded` ("sensor silent") distinct from `stale` ("engine
  stalled"), and counts frames the kiosk confirmed painting.

### Core (shared with the classic console)
- WeatherFlow's websocket delivers every `obs_st` twice; the duplicate guard
  compared a list to a number and never fired, so per-minute integrators (peak
  sun, strike counts) double-counted. Fixed.
- Tempest daily-bucket rain columns were swapped: month and year were seeded
  from the rain-check corrected figure while today/yesterday used the raw
  sensor. With `nc_rain` off the year total now matches the device (34.5 in,
  not 42.5 in).
- REST seeds (daily wind average, gust max, yesterday's rain) retry every five
  minutes while missing instead of only once at boot; an echoed message no
  longer wipes the REST cache.
- Yearly rain rollover no longer doubles its baseline for one observation;
  lightning frequency averages over real coverage; sunrise/sunset use station
  midnight in explicit UTC, with polar days handled; statistics rows are
  selected by date and must carry a finite number; short websocket rows are
  shape-checked.

### Kiosk
- Launcher waits for the declared display backend (a Wayland session never
  falls back to X11), bounds every health probe, reads the 503 stale body so a
  wedged engine restarts the engine rather than the server, exits cleanly on
  TERM with children reaped, and restarts the engine once per silent-sensor
  episode.
- Pi 4: `wifi-keepalive` timer re-associates when the gateway stops answering;
  persistent journal so the next drop leaves a log. See `kiosk/PI4-SETUP.md`.

## 2026-09-01

- Wind-driven rain and blowing snow glyphs; tomorrow hint under the headline;
  MAX gust row on the wind panel; forecast-day rain amounts (from drench44).
- Rolling rain window; rain fade; rain volume slew.
- Barograph in the station's pressure unit; outlook vocabulary compacted.

## 2026-08-29 to 2026-08-31

- 7-day outlook band on one shared temperature scale, condition glyphs, snow-aware
  status, alerts and outlook coexisting, etched two-depth rain with a waving
  water surface, intensity-scaled rain gauge.
- Forecast fetch retries until first success; fit-and-finish audit fixes.

## 2026-08-03 to 2026-08-10

- NWS weather alerts and AQI (WAQI) with severity forecast; alert rotation.
- Systemd-supervised kiosk with stale-data recovery; headless mode; LAN view;
  X11/Wayland auto-detect; Pi 4 setup guide; public-station picker for
  hardware-less setup; offline test suite and CI.
