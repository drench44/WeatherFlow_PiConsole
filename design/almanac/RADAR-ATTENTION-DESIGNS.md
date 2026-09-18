# Radar acquisition: attention-aware policy, two designs compared (2026-09-17)

Question from the owner: the engine keeps fetching radar with the tab closed (measured 22 to 35 MB a
day idle, 85 MB a day in use). People look at radar on rainy days, right after they opened the tab, and
while they are touching the device. Two independent designs were written from the same brief and the
same measured numbers; the comparison and recommendation follow them.

Correction that applies to both: `fcHourly` in the payload carries temperatures, not precipitation
probability (Astra checked the code). Forecast signals must use `fcPrecipPct` and `fcDaily[].pp` unless
the forecast fetch is extended.

---

# Design A: Fable

# Fable: attention tiers for radar acquisition

## Tiers

| tier | when | fetches | approx cost (site mode) |
|---|---|---|---|
| LIVE | Radar tab open now | today's behaviour: newest at cadence, 8-frame history, zoom/mode prefetch | 3.5 MB/h (measured) |
| WARM | tab closed < 15 min ago, or a human touched the device < 15 min ago | newest at every scan; keep 4 most recent frames complete; no prefetch | ~0.95 MB/h |
| WATCH | weather says radar matters (below) and nobody has looked for > 15 min | newest frame kept ≤ 15 min old (every 2nd site scan, every 3rd Region image); no history | ~0.4 MB/h |
| REST | dry, quiet, nobody looked for > 15 min | listings every 15 min + one 4-tile sentinel at zoom 5 every 60 min (daytime) / 120 min (night) | ~0.03 MB/h |
| DORMANT | REST for ≥ 2 h during 23:00–06:00 local, or no view for 3 days | one listing per hour, no tiles | ~0.002 MB/h |

WATCH signals (any one enters; ALL must clear for 60 min to leave):
- Tempest: `rainRateMm > 0` or `rainStatus` wet within the last 60 min (the gauge reports every minute, so 3 am rain is noticed within a minute).
- `lightningActive` or a strike within 3 h.
- Echo: the newest frame's tiles record `radarVisiblePixels`; any echo within the viewed grid, OR the REST sentinel (4 tiles at zoom 5 ≈ 150 km) shows echo → WATCH.
- Forecast: `fcHourly` precip chance ≥ 30 % within the next 3 h.

Hysteresis: stepping UP is immediate on any signal. Stepping DOWN needs dwell: LIVE→WARM on tab close (WARM lasts 15 min from the last view/touch); WARM→WATCH/REST after the 15 min; WATCH→REST after 60 min with every signal clear and two consecutive echo-free scans; REST→DORMANT after 2 h of REST and night. Tier changes are logged once each.

## Arithmetic
Tile ≈ 2 KB incl. headers, listing ≈ 400 B. Site scan ≈ 43 requests ≈ 86 KB, ~8.5 scans/h. Region image ≈ 20 tiles ≈ 40 KB, 30/h.
- LIVE 3.5 MB/h measured.
- WARM: 8.5 × 86 KB + history top-up (~4 frames × 20 KB) + listings at 30 s (0.2 MB) ≈ 0.95 MB/h.
- WATCH: 4.25 × 86 KB + aligned listings (6 × 1.6 KB per scan) ≈ 0.4 MB/h. Region: 10 × 40 KB ≈ 0.4 MB/h.
- REST: 4 listings/h × 1.6 KB + sentinel 8 KB/h ≈ 15 KB/h. DORMANT ≈ 1.6 KB/h.

## Day in the life
| day | tiers | MB |
|---|---|---|
| dry night | DORMANT 7 h | 0.01 |
| dry day, nobody looks | REST 17 h + DORMANT 7 h | 0.3 |
| dry day, 5 min glance | + LIVE 5 min (0.3) + WARM 15 min (0.24) | 0.9 |
| rainy afternoon, glances (30 min viewing) | WATCH ~20 h (8) + LIVE 0.5 h (1.75) + WARM 1 h (0.95) | ~11 |
| storm evening, active use (2 h) | WATCH 20 h (8) + LIVE 2 h (7) + WARM 1 h (1) | ~16 |
| a week away | DORMANT after day 3 | < 0.1/day |
PNW month: 12 rainy days × ~11 MB + 18 dry days × ~0.9 MB ≈ 150 MB/month ≈ 5 MB/day, against 22–35 MB/day idle today (−80 %) and ~0.9 MB on dry days (−96 %).

## Wake-up path (tab opens)
- LIVE/WARM: newest frame is ≤ one cadence old and cached → paints instantly; history fills "frame N of 8" as today (≤ 10 s).
- WATCH: newest ≤ 15 min old → paints instantly with its true age in "As of"; the engine fetches the current scan (≈ 5 s on this network) and history (≤ 30 s). Note: "Waking · newest scan in a moment".
- REST: cached newest may be an hour old → shown immediately, marked by age; engine runs listings (1 s) then newest (5 s) then 4 then 8 frames (≤ 45 s). Note: "Waking radar · showing 8:12 PM while the newest scan loads".
- DORMANT: same as REST plus a listing first; ≤ 60 s to a full loop. If the cached frame is > 6 h old the plate shows the existing starting state ("Radar waking · fetching the first scan") instead of a stale image.
The engine's existing `radar.starting`, `refresh.state` and "Refreshing · frame N of 8" copy carry all of this; only the tier word is new.

## Implementation sketch
- `lib/radar_attention.py`: `class Attention` with a pure `decide(signals, now, state) -> (tier, reason)`; `signals` is a small dataclass filled from the payload the emitter already builds (rainRateMm, rainStatus, lightningSinceSec, fcHourly, newest-frame echo flag from tile metadata, viewed-marker age, last intent age, last LAN viewer age, local hour). Table-driven tests for every transition and dwell.
- Emitter: compute the tier in `_emit` after `_build_payload`; publish `radar.attention = {tier, reason, since, nextScanCheckAt}`; `_radar_arm_discovery` picks the interval from the tier (30 s / aligned / 15 min / 60 min); `_do_radar` reads the tier for `target` frames (8 / 4 / 1 / 0) and for whether tiles are fetched at all; the REST sentinel is a tiny `_radar_sentinel()` pass (4 mosaic tiles at zoom 5, `radarVisiblePixels` summed) that never touches the manifest. Tier changes log one line: `radar attention LIVE -> WATCH (rain 1.2 mm/h)`.
- Page: `rad-status` prefix by tier off-tab is invisible; ON the Observations screen the Radar tab gets a small dot for WATCH/LIVE ("radar has weather") and the tab's aria-label carries the tier; on the radar screen the caption's trailing segment reads "Live", "Watching for rain", "Waking".
- Health: `/health.radar.attention` with tier history and per-tier byte totals (so the real MB/day is measured, not modelled).

## Risks
- Rain at 3 am while DORMANT: the Tempest gauge promotes to WATCH within a minute; first frame within one scan (≤ 7 min). Acceptable: nobody is looking; the tab wakes warm by morning.
- Rain 30 km away, gauge dry: caught by the sentinel (≤ 60 min in daytime) or by the hourly forecast chance. Worst case an approaching band is seen an hour late while nobody is looking; the moment someone opens the tab, LIVE fetches everything anyway (≤ 45 s).
- A LAN browser left open on the Radar tab all night looks like LIVE: keep today's `radar_viewed` TTL logic (page must actually poll with view=radar), and cap LIVE for an unattended LAN viewer at 2 h without an intent post → WARM.
- Tier flapping around thresholds: dwell + logging; the health history proves it.
- Site "not reporting" and network outages: unchanged; the fetch layer's backoff already sits under every tier.

## Would not do
- Predictive models or learned schedules: the four signals we already have are direct evidence.
- Turning radar off at night regardless of weather: night rain is exactly when a glance matters.
- Region mosaic every 2 minutes off-tab: the mosaic's value is the loop, which only exists on-tab.
- Any change to the on-tab (LIVE) experience: it is verified and the owner is happy with it.


---

# Design B: Astra (gpt-6-astra, read-only design run)

Use four acquisition tiers—**Resting, Watching, Ready, and Live**—with separate controls for discovery, history, and prefetch. Reserve bandwidth for opening Radar before spending it on background work.

This design follows the existing engine, discovery scheduler, server markers, contract, and page. No files were modified.

**1. Policy**

Two code details matter:

- `_radar_is_viewed()` means “viewed within 15 minutes,” not “open now.” `radar_viewing:{since,last}` provides the live-session signal.
- `fcHourly` currently contains **temperatures**, not precipitation probabilities. Start with `fcPrecipPct`, today’s `fcDaily.pp`, and conditions text. Hourly precipitation would require extending the existing forecast response and contract.

Evaluate cached signals every two seconds. Keep policy evaluation separate from network scheduling.

| Tier | Acquisition | Entry conditions | Healthy wake target |
|---|---|---|---|
| **Resting** | One listing-and-newest-image check every 120 minutes; retain cached imagery | Dry, previously clear, no Radar attention for ≥2 hours, outside 06:00–22:00; also unattended dry periods after 24 hours | Current complete image ≤60 seconds |
| **Watching** | Listing plus newest frame every 30 minutes; every 10 minutes when wet, echo-positive, or forecast-positive | Default daytime; weather interest without recent attention | Current complete image ≤60 seconds |
| **Ready** | Newest frame at native scan cadence; retain existing history, but do not backfill it | First 15 minutes after a dry visit; wet conditions with attention 45–120 minutes ago; lightning without recent attention | Cached current image ≤5 seconds; catch-up ≤60 seconds |
| **Live** | Native cadence; build newest → four → eight available frames, then slide the loop | Radar visibly open; or wet/echo-positive with attention within 45 minutes; lightning with attention within two hours | Cached current image ≤1 second; newly fetchable scans targeted within 30 seconds |

These are acceptance targets for a healthy provider, validated cache, unchanged camera, and available budget—not guarantees already demonstrated on the Pi.

**Signal definitions and hysteresis**

- **Wet:** fresh observation with `rainRateMm > 0`, or a recognized precipitation-bearing `rainStatus`. A nonzero trace counts.
- **Echo:** any positive `radarVisiblePixels` in the newest validated footprint. Positive evidence promotes immediately. “Clear” requires complete coverage; missing tiles mean unknown.
- **Forecast-positive:** precipitation probability ≥50%, or current conditions naming rain, showers, snow, or thunderstorms. Exit below 30% with no supporting conditions for 60 minutes. Ignore expired forecast evidence.
- **Lightning:** `lightningActive`, or a strike within 30 minutes. Every new strike extends that hold.
- **Attention:** a new visible Radar session or accepted user camera/source action. Heartbeats, ownership claims, and automatic recentering do not count as repeated human actions.
- `last_viewer` is weak evidence: it can justify Watching, never Live. Continuous LAN polling does not prove someone is present.
- Promote immediately. Demote only after ten minutes in a tier and all stronger holds expire. Clearing a wet/echo hold requires no sensor rain for 30 minutes and two complete echo-free scans at least ten minutes apart.
- After 24 hours without Radar attention, disable recent-use boosts and use 30-minute Watching for ordinary wet weather. Lightning still promotes to Ready. This is an acquisition heuristic, not an occupancy claim.
- Treat observations older than five minutes as unknown, not dry. Use Watching while evidence is unavailable.

Keep the same daytime window throughout the week initially. Wind and temperature alone do not promote acquisition; normalize units before any future combined weather rule.

In Ready/Live, preserve `DiscoverySchedule` alignment, including MRMS `READY_LAG=300`, six fast polls, then 120-second backoff. Resting/Watching make one discovery attempt per scheduled check; an unchanged listing ends the pass. Do not replay skipped scans.

Disable deep history beyond eight frames. Permit optional prefetch only in Live, following a genuine action within two minutes, after the loop is complete, with ≥90% success over the last 20 attempts and ample budget. Cap it at **0.25 MB/hour**, included in the total budget.

**Traffic arithmetic and limits**

Use decimal MB and the supplied approximate sizes. Conservatively charge all 43 site requests as tile-sized, plus one listing:

- Site check: `43 × 0.002 + 0.0004 = 0.0864 MB`.
- Region check: `20 × 0.002 + 0.0004 = 0.0404 MB`.

| Sustained tier | Site MB/day | Region MB/day |
|---|---:|---:|
| Resting: 12 checks | 1.04 | 0.48 |
| Watching: 48 checks | 4.15 | 1.94 |
| Watching, interested: 144 checks | 12.44 | 5.82 |
| Ready: native cadence | `1440/7 × .0864 = 17.77` | `720 × .0404 = 29.09` |
| Live planning allowance | 84 | 84 |

Live uses the measured `3.5 MB/hour × 24` as a conservative planning rate. Keeping eight frames does **not** multiply steady traffic by eight: cached frames slide forward. A cold seven-frame backfill adds approximately **0.605 MB site / 0.283 MB Region**.

Extra discovery polls cost 0.0004 MB per listing; retries, additional site layers, and changed cameras increase costs.

Set hard acquisition limits of **24 MB per rolling 24 hours and 6 MB per rolling hour**, retaining the existing **240 requests/minute** cap. Reserve **4 MB/day and 1 MB/hour** exclusively for user-triggered newest-frame acquisition; ordinary work therefore stops at 20 MB/day or 5 MB/hour.

Use atomic byte reservations across every request, retry, hedge, and fallback. Count request/response headers and partial bodies; limit reads before exceeding the allowance. The current completed-body counter is insufficient. Never assume 2 KB is a maximum response size. Persist reservations conservatively so restarting cannot reset the budget.

These are hard **accounted HTTP-byte** limits. ISP totals also contain TCP/TLS overhead and retransmissions; an exact ISP-byte ceiling requires OS/router accounting.

As allowances tighten, remove prefetch, then history backfill, then delay background checks. Publish the actual limited policy and next eligible time. Never bypass the cap silently.

**2. Day in the life**

Approximate segment costs before contingency:

| Situation | Behavior | Site / Region MB |
|---|---|---:|
| Dry night, eight hours | Resting | 0.35 / 0.16 |
| Dry day, nobody looks, 16 hours | Watching every 30 minutes | 2.76 / 1.29 |
| Rainy afternoon, glances throughout four hours | Live; retain eight-frame loop between glances | 14 / 14 |
| Storm evening, two hours of active use | Live; newest takes priority over history and prefetch | 7 / 7 |
| Dry week away | Resting after the unattended threshold | Approximately 7.3 / 3.4, plus first-day transition |

Wet periods during an absence use Watching every 30 minutes, rather than maintaining an unused loop. Long storm days can reach the cap and visibly enter limited service.

For a reproducible monthly estimate, viewing minutes alone are insufficient; assume:

- **Dry day:** eight hours Resting, 15 hours 40 minutes Watching, 15 minutes Ready, five minutes Live.
- **Rainy day:** eight dry nighttime hours Resting, seven hours ordinary Watching, six hours interested Watching, one hour Ready, two hours Live encompassing the specified 30 minutes of clustered glances.

Add one cold history fill per day and 20% contingency:

- Dry day: **5.0 MB site / 2.8 MB Region**.
- Rainy day: **15.6 / 12.8 MB**.
- With 40% rainy days: `0.6 × dry + 0.4 × rainy` = **9.2 / 6.8 MB/day**, or approximately **277 / 204 MB per 30-day month**.

More scattered glances extend Live holds and raise consumption; the caps still apply.

**3. Wake-up path**

Opening Radar must work even when no image exists.

| Time after opening | Action and copy |
|---|---|
| **0 seconds** | Paint cached imagery or the local map immediately. Retain the scan’s actual timestamp: “Radar resting · last image 02:14 · waking…” |
| **0–2 seconds** | Send immediate `poll(true)` with `view=radar`; recognize the session, promote demand, cancel the idle timer, and prioritize newest acquisition. |
| **By 1 second, Live** | Show the cached current image; display “Live” only while freshness and acquisition health support it. |
| **By 5 seconds, Ready** | Render the cached current frame. If stale or the camera changed, say “Fetching latest scan…” and use the cold target. |
| **2–25 seconds, cold path** | Validate inventory/listings and acquire newest tiles first. Publish progress immediately; show the complete newest image before waiting for history. |
| **25–60 seconds** | Continue bounded recovery if needed. After newest succeeds: “Refreshing · frame N of 8.” |
| **At 60 seconds without success** | “Still refreshing · showing [timestamp] · next attempt [time].” For exhausted allowance: “Radar data limit reached · next refresh [time].” |

A full site loop may require a second minute: eight uncached frames can exceed 240 requests. The 60-second target covers the **first current complete image**, not all history.

“Current” means a sufficiently fresh provider scan, not imagery of the present instant. Keep existing age/staleness thresholds and show the date for old cached images.

**4. Implementation sketch**

In [lib/almanac_emit.py](/Users/garyneitzke/projects/WeatherFlow_PiConsole/lib/almanac_emit.py), add a small policy state object evaluated from existing snapshots. Preserve `_radar_is_viewed()` compatibility, but introduce explicit current-view, recent-attention, target-frame-count, and prefetch permissions instead of overloading `ctx['viewed']`.

Apply admission to `_check_radar`, `_radar_arm_discovery`, `_check_radar_discovery`, retries, and boot acquisition. `_check_radar_zoom` handles immediate wake. `_radar_checkpoint` stops newly disallowed history/prefetch at tile boundaries; recompute `_radar_pending` so intentional deferral cannot create retry loops.

Extend `_radar_request_gate` and transport accounting. Prioritize validation of requested newest tiles over background cache inventory during cold wake.

Publish through `_radar_payload`:

`radar.acquisition = {tier, reason, sinceTs, nextCheckTs, targetFrames, prefetchAllowed, limited, wakeState, bytes24h, bytes1h, budgetNextTs}`

Keep `observedTs`, `acquiredTs`, `fetchedAt`, and observation freshness semantics unchanged.

Update the contract and `renderRadar`, `radarStartingRender`, `radarNoteRender`, `radarState`, and `schedulePoll`. Resting/no-image Radar remains accessible. Keep `radar.starting` for actual startup; use `wakeState` for later acquisition. Do not let four-frame source staging block a first current still.

Log transitions and bounded hourly usage summaries through existing radar logging and health fields.

Write deterministic tests for thresholds, hysteresis, stale/unknown signals, forecast shape, discovery alignment, wake during an active worker, pending-work suppression, concurrent byte admission, failed/partial/hedged responses, restart persistence, clock changes, and cap exhaustion. Browser/Pi tests should measure first current image and verify honest copy in both themes.

**5. Risks and failure modes**

Rain starting at 3 a.m. promotes within two seconds **after the engine receives a wet observation**. Sensor reporting, delivery, and drizzle detection add latency; a two-second engine tick is not a two-second rain detector.

Echoes 30 km away are discovered only when their area lies within the monitored footprint: nominally within 120 minutes while Resting, 30 while Watching, or ten when interested, plus provider publication delay. A panned or tightly zoomed footprint cannot establish conditions around home; treat home echo status as unknown and use home coverage for unattended checks without changing saved camera preferences.

Clutter can keep echo-positive acquisition elevated. Missing coverage, snow under-detection, and stale observations must never establish “dry.”

A visible unattended Radar tab is indistinguishable from someone glancing without touching. It remains Live until bounded by the budget.

No finite budget guarantees unlimited fresh wakes. Reserved capacity protects ordinary returns; exhaustion and provider outages require explicit delayed-service copy.

**6. What I would not do**

I would not use listing-only checks to infer rain, fetch eight frames on every scan, warm every source/zoom while idle, interpret polling as human presence, or learn household routines initially. I would not change freshness timestamps to make cached imagery look current. A deterministic policy with visible reasons is cheaper, testable, and easier to tune from a month of actual usage.

---

# Comparison

| | Fable | Astra |
|---|---|---|
| tiers | LIVE, WARM, WATCH, REST, DORMANT | Live, Ready, Watching, Resting |
| dry-day baseline | REST: listings only every 15 min + a 4-tile zoom-5 "sentinel" hourly (~15 KB/h) | Watching: a full newest-frame check every 30 min (~4 MB/day) |
| dry day, nobody looks | ~0.9 MB | ~5.0 MB |
| rainy day with glances | ~11 MB | ~15.6 MB |
| PNW month (40 % rainy) | ~150 MB, ~5 MB/day | ~277 MB, ~9.2 MB/day |
| approaching rain, gauge dry | sentinel or forecast, ≤ 60 min in daytime | Watching footprint, ≤ 30 min (10 when interested) |
| budget enforcement | tiers only; measure per-tier bytes in /health first | hard caps (24 MB/day, 6 MB/h) with byte reservations across retries/hedges, persisted, plus a reserve for user wakes |
| signal rigor | rain, lightning, echo, forecast, view, touch | same, plus: stale observation = unknown not dry; "clear" needs complete tile coverage; panned camera cannot vouch for home; clutter caveat; LAN poller is weak evidence |
| demotion | dwell per tier (15 min / 60 min / 2 h) | 10-minute dwell plus every stronger hold expiring |
| wake path | reuses `radar.starting` and existing notes; ≤ 45 s cold | detailed second-by-second table; explicit "data limit reached" copy; ≤ 60 s cold for the first image |
| engineering size | small: one pure policy module, a sentinel pass, tier-driven intervals | larger: policy module plus a byte-accounting admission layer through the transport |

## Recommendation

Take Fable's ladder for the cost floor and Astra's rigor for the rules.

- Use Fable's REST (listings only) and DORMANT tiers and the zoom-5 sentinel. Astra's dry-day baseline
  of a full newest-frame check every 30 minutes is what makes its dry day cost five times more, and it
  buys little: nobody is looking, and the sentinel plus the Tempest gauge, lightning and forecast cover
  the "rain is coming" case.
- Adopt Astra's signal definitions wholesale: an observation older than five minutes is unknown, not dry;
  echo-clear requires complete coverage; a panned or zoomed camera cannot establish home conditions (the
  sentinel should always look at home at zoom 5); a LAN poller never proves presence; lightning holds
  extend with each strike. Adopt its 10-minute demotion dwell inside Fable's longer tier dwells.
- Use the `radar_viewing` session marker for "open now" and `radar_viewed` for "recent", as Astra noted,
  and count only intent posts (taps, zooms, source switches) as human action.
- Defer Astra's byte-reservation admission layer. It is real engineering with a new failure surface, and
  today nothing measures per-tier bytes at all. Publish per-tier byte totals in `/health.radar` first,
  run the tiers for a month, then decide whether a hard daily cap is needed and what number. Keep the
  existing 240 requests/minute gate, which already bounds the worst hour.
- Keep both wake-path promises: an image on screen within a second whenever a cached frame exists,
  marked by its true age, and a current image within 60 seconds from cold. Astra's "Radar data limit
  reached · next refresh at" copy is only needed if a cap is added later.

## Decisions for the owner

1. Dry-day baseline: listings-only REST (Fable, ~1 MB/day) or a 30-minute newest check (Astra, ~5 MB/day)?
2. Night: DORMANT with one listing an hour, or keep REST behaviour around the clock?
3. Is a hard daily byte cap wanted now, or measure first?
4. Should the Radar tab show a small dot when weather is present (WATCH or above) while on other screens?
