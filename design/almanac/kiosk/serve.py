#!/usr/bin/env python3
# Tiny static server for the almanac overlay (index.html + wx.json) plus a
# /health endpoint for monitoring. Replaces `python -m http.server`.
#
#   /health -> JSON {status, reason, dataAgeSec, obsAgeSec, renders, polls, ...}
#     status: "ok"        engine heartbeat AND observation both fresh
#             "stale"     wx.json older than STALE_SEC (engine stalled)
#             "degraded"  wx.json fresh, but the newest observation is older
#                         than OBS_STALE_SEC (sensor silent — the engine is
#                         faithfully republishing a dead reading)
#             "error"     wx.json missing/unreadable (engine down)
#     reason: "engine stalled" | "sensor silent" | the read error
#   HTTP 200 when ok, 503 otherwise (so a monitor can alert on non-2xx).
#
# Bind stays on 127.0.0.1 by default (chromium is local; no data leaves the box).
# Set WFP_BIND=0.0.0.0 to expose /health (and the page) to the LAN for remote
# monitoring — note that also makes wx.json LAN-readable.
import http.server, socketserver, json, math, os, time, threading, re, io, zlib
from urllib.parse import parse_qs
from decimal import Decimal

PORT      = int(os.environ.get("WFP_PORT", "8137"))
WEB       = os.environ.get("WFP_WEB", ".")
BIND      = os.environ.get("WFP_BIND", "127.0.0.1")
DATA      = os.environ.get("WFP_DATA", "/tmp/wfp_data/wx.json")
STALE_SEC = int(os.environ.get("WFP_STALE_SEC", "20"))
# A station can go silent for minutes while the engine keeps emitting. Long
# enough not to trip on one dropped Tempest report (they arrive ~60 s apart).
OBS_STALE_SEC = int(os.environ.get("WFP_OBS_STALE_SEC", "300"))

# Aligned with the emitter shared floor and highest source ceiling.
RADAR_MIN_ZOOM, RADAR_MAX_DESIRED_ZOOM = 4, 10

LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")

# The most recent private-LAN client that fetched anything, written at most every
# 30 s to <data dir>/last_viewer. The wifi keepalive sends that host a few unicast
# frames each minute: on a multi-node mesh the node bridging the Pi stopped
# forwarding wired-side traffic to it (ARP "(incomplete)" from a wired Mac while
# the Pi's own outbound worked), and the Pi's OWN frames toward a wired host are
# what re-teach the node's bridge table. Loopback and public addresses are never
# recorded; the file holds one IP and nothing else.
_LAN_VIEWER_INTERVAL = 30.0
_lan_viewer_at = 0.0
_lan_viewer_lock = threading.Lock()


def _is_private_ipv4(address):
    parts = address.split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return False
    a, b = int(parts[0]), int(parts[1])
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def _note_lan_viewer(address):
    global _lan_viewer_at
    if address in LOOPBACK or address.startswith("::ffff:"):
        address = address[7:] if address.startswith("::ffff:") else address
        if address in LOOPBACK:
            return
    if not _is_private_ipv4(address):
        return
    now = time.time()
    with _lan_viewer_lock:
        if now - _lan_viewer_at < _LAN_VIEWER_INTERVAL:
            return
        _lan_viewer_at = now
    marker = os.path.join(os.path.dirname(DATA), "last_viewer")
    tmp = f"{marker}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w") as f:
            f.write(address + "\n")
        os.replace(tmp, marker)
    except OSError:
        pass

# Two counters, and the difference matters. `polls` counts wx.json REQUESTS
# from anyone — a LAN viewer, a curl, a renderer that fetched and then threw.
# `renders` counts frames the KIOSK actually painted: the page reports the
# previous frame's success by adding r=1 to its next poll, and only a loopback
# client is believed. The watchdog reads `renders`, because a growing request
# count never proved anything reached the screen.
_polls      = 0
_renders    = 0
_count_lock = threading.Lock()


# A separate session marker preserves radar_viewed's 15-minute demand hint.
# The engine admits deep history only during a continuous, live view session.
RADAR_VIEW_POLL_GAP_SEC = 5


def _write_radar_viewing(viewed):
    marker = os.path.join(os.path.dirname(DATA), 'radar_viewing')
    tmp = f'{marker}.tmp.{os.getpid()}'
    try:
        if not viewed:
            try:
                os.unlink(marker)
            except FileNotFoundError:
                pass
            return
        now = time.time()
        since = now
        try:
            with open(marker) as f:
                previous = json.load(f)
            if (0 <= now-previous['last'] < RADAR_VIEW_POLL_GAP_SEC
                    and previous['since'] <= previous['last']):
                since = previous['since']
        except (OSError, ValueError, TypeError, KeyError):
            pass
        with open(tmp, 'w') as f:
            json.dump(dict(since=since, last=now), f)
        os.replace(tmp, marker)
    except OSError:
        pass
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _write_radar_zoom(values):
    return _write_radar_preference('radar_zoom', values)


def _write_radar_center(values):
    return _write_radar_preference('radar_center', values)


def _write_radar_source(values):
    return _write_radar_preference('radar_source', values)


def _write_radar_preference(name, values):
    """Caller holds _count_lock and has checked loopback. Polling cannot fail here."""
    if name not in ('radar_zoom', 'radar_source', 'radar_center', 'radar_smooth') or len(values) != 1:
        return
    value = values[0]
    if name == 'radar_center':
        if value != 'station':
            if not re.fullmatch(r'-?\d{1,3}(\.\d+)?,-?\d{1,3}(\.\d+)?', value, re.ASCII):
                return
            lat, lon = map(float, value.split(','))
            if not (-85.05112878 <= lat <= 85.05112878 and -180 <= lon <= 180):
                return
            # Keep canonical floats in the decimal grammar (str() can emit 1e-10).
            value = ','.join(format(Decimal(str(n)), 'f') for n in (lat, lon))
    elif name == 'radar_smooth':
        if value not in ('on', 'off'):
            return
    elif name == 'radar_source':
        if value not in ('mosaic', 'site'):
            return
    elif value != 'auto':
        if not re.fullmatch(r'[0-9]{1,2}', value):
            return
        level = int(value)
        if not RADAR_MIN_ZOOM <= level <= RADAR_MAX_DESIRED_ZOOM:
            return
        value = str(level)
    # The kiosk links this sibling to durable station storage before startup.
    # Resolve the link so replacement updates its target, not the link.
    marker = os.path.join(os.path.dirname(DATA), name)
    # Pan belongs to tmpfs. Never follow a durable link, even if one was
    # accidentally installed: atomic replacement replaces the link itself.
    if name != 'radar_center':
        marker = os.path.realpath(marker)
    tmp = f"{marker}.tmp.{os.getpid()}"
    try:
        try:
            with open(marker) as f:
                limit = 1024 if name == 'radar_center' else 128
                current = f.read(limit)
                if (len(current) < limit and current.strip() == value
                        and not (name == 'radar_center' and os.path.islink(marker))):
                    return
        except (OSError, UnicodeError):
            pass
        with open(tmp, 'w') as f:
            f.write(value + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, marker)
    except OSError:
        pass
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _read_radar_intent():
    try:
        with open(os.path.join(os.path.dirname(DATA), 'radar_intent')) as stream:
            record = json.load(stream)
        if not isinstance(record, dict): record = {'seq': int(record)}
        if type(record.get('seq')) is not int or not 0 <= record['seq'] <= 999999999999:
            return {'seq': 0}
        return record
    except (OSError, ValueError, TypeError):
        return {'seq': 0}


def _write_radar_intent(params):
    """One validated transaction; duplicate generations never mutate preferences."""
    keys = ('radarSeq','radarZoom','radarSource','radarCenter')
    if any(len(params.get(k, [])) != 1 for k in keys): return
    seq, zoom, source, center = (params[k][0] for k in keys)
    if not re.fullmatch(r'[0-9]{1,12}', seq, re.ASCII): return
    seq = int(seq)
    if seq <= _read_radar_intent()['seq']: return
    if zoom != 'auto':
        if not re.fullmatch(r'[0-9]{1,2}', zoom, re.ASCII): return
        zoom = int(zoom)
        if not RADAR_MIN_ZOOM <= zoom <= RADAR_MAX_DESIRED_ZOOM: return
    if source not in ('site','mosaic'): return
    if center != 'station':
        if not re.fullmatch(r'-?\d{1,3}(\.\d+)?,-?\d{1,3}(\.\d+)?', center, re.ASCII): return
        lat, lon = map(float, center.split(','))
        if not (-85.05112878 <= lat <= 85.05112878 and -180 <= lon <= 180): return
        center = dict(lat=lat, lon=lon)
    record = dict(seq=seq, zoom=zoom, source=source, center=center)
    marker = os.path.join(os.path.dirname(DATA), 'radar_intent')
    tmp = f'{marker}.tmp.{os.getpid()}'
    try:
        with open(tmp, 'w') as stream:
            json.dump(record, stream); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, marker)
        # Durable preferences are persistence only once a runtime intent exists.
        _write_radar_zoom([str(zoom)])
        _write_radar_source([source])
    except OSError: pass
    finally:
        try: os.unlink(tmp)
        except OSError: pass


_camera_persist_timer = None


# A reload claims ownership with compare-and-swap against the acknowledged owner.
# Generation zero only reconciles; it never commits a restored camera.
_radar_owner = None

def _camera_transaction(activity, params):
    global _radar_owner
    session = params.get('radarSession', [''])[0]
    generation = params.get('radarGeneration', [''])[0]
    if not re.fullmatch(r'[A-Za-z0-9-]{16,64}', session) or not re.fullmatch(r'[0-9]{1,9}', generation):
        return False
    generation = int(generation)
    heartbeat = params.get('radarHeartbeat', ['0'])[0]
    if not re.fullmatch(r'[0-9]{1,12}', heartbeat):
        return False
    heartbeat = int(heartbeat)
    old = _read_radar_intent()
    if _radar_owner is None:
        _radar_owner = dict(session=old.get('session', ''), generation=old.get('generation', 0))
    if generation == 0:
        if session != _radar_owner['session'] and params.get('radarClaim') == [_radar_owner['session']]:
            _radar_owner = dict(session=session, generation=0)
        if session != _radar_owner['session'] or _radar_owner['generation'] != 0:
            return False
    elif session != _radar_owner['session'] or generation < _radar_owner['generation']:
        return False
    # Reload ownership is read-only for camera intent, but its activity still
    # needs the same ordinal fence as committed generations. A delayed moving
    # heartbeat must not overwrite a newer settled view during reconciliation.
    if heartbeat and heartbeat <= _radar_owner.get('heartbeat', 0):
        return False
    if params.get('radarCommit') == ['1']:
        if generation > _radar_owner['generation']:
            if activity.get('moving') or 'zoom' not in activity or 'center' not in activity:
                return False
            if params.get('radarSource') not in (['site'], ['mosaic']) or params.get('radarPolicy') not in (['auto'], ['manual']):
                return False
            if not _write_settled_camera(activity, params):
                return False
            _radar_owner = dict(session=session, generation=generation)
    elif generation != _radar_owner['generation']:
        return False
    if heartbeat:
        _radar_owner['heartbeat'] = heartbeat
    return True


def _write_settled_camera(activity, params):
    """Activity is the only live camera input; durable zoom is an output."""
    global _camera_persist_timer
    if activity.get('moving') or 'zoom' not in activity or 'center' not in activity:
        return
    old = _read_radar_intent()
    sources = params.get('radarSource', [])
    source = sources[0] if len(sources) == 1 and sources[0] in ('site', 'mosaic') else old.get('source')
    if source is None:
        try:
            source = open(os.path.join(os.path.dirname(DATA), 'radar_source')).read().strip()
        except OSError:
            source = 'mosaic'
    if source not in ('site', 'mosaic'):
        source = 'mosaic'
    record = dict(zoom=activity['zoom'], center=activity['center'], source=source, camera=True)
    if 'radarSession' in params:
        record.update(session=params['radarSession'][0], generation=int(params['radarGeneration'][0]),
                      zoomPolicy=params['radarPolicy'][0], acceptedAt=time.time())
    if all(old.get(k) == v for k, v in record.items()):
        return True
    record['seq'] = min(999999999999, max(old['seq']+1, int(time.time()*100)))
    marker = os.path.join(os.path.dirname(DATA), 'radar_intent')
    tmp = marker+'.tmp'
    try:
        with open(tmp, 'w') as stream:
            json.dump(record, stream)
        os.replace(tmp, marker)
    except OSError:
        return
    # Runtime intent is immediate. SD-card persistence follows a settled quiet
    # interval; a newer report cancels the pending older write.
    if _camera_persist_timer is not None:
        _camera_persist_timer.cancel()
    def persist():
        with _count_lock:
            if _read_radar_intent() == record:
                _write_radar_zoom(['auto' if record.get('zoomPolicy') == 'auto' else str(record['zoom'])])
                _write_radar_source([record['source']])
    _camera_persist_timer = threading.Timer(.25, persist)
    _camera_persist_timer.daemon = True
    _camera_persist_timer.start()
    return True


def _radar_activity(params):
    record=dict(at=time.time(),theme=params['radarTheme'][0],moving=params.get('radarMoving')==['1'])
    centers,zooms=params.get('radarGeoCenter',[]),params.get('radarGeoZoom',[])
    if len(centers)==len(zooms)==1:
        if (re.fullmatch(r'-?\d{1,3}(\.\d+)?,-?\d{1,3}(\.\d+)?',centers[0],re.ASCII)
                and re.fullmatch(r'[0-9]{1,2}',zooms[0],re.ASCII)):
            lat,lon=map(float,centers[0].split(','));zoom=int(zooms[0])
            if -85.05112878<=lat<=85.05112878 and -180<=lon<=180 and 4<=zoom<=10:
                record.update(center=dict(lat=lat,lon=lon),zoom=zoom)
    return record


class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def __init__(self, *a, **k):
        super().__init__(*a, directory=WEB, **k)

    def do_POST(self):
        # The engine alone owns tile eviction. A page may report corruption or
        # an unexpected 404; bounded hints are validated against its own index.
        if self.path != '/radar-bad-tile' or self.client_address[0] not in LOOPBACK:
            self.send_error(403)
            return
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 256:
                raise ValueError('report length')
            path = self.rfile.read(length).decode('ascii').lstrip('/')
            if not re.fullmatch(r'radar/t/[a-f0-9]{12}/(?:iem-mrms-lcref|iem-nexrad-n0b|rainviewer)/(?:-|[A-Z0-9]{4})/[0-9]{12}/[0-9]{1,2}/[0-9]{1,4}/[0-9]{1,4}\.png',path,re.ASCII):
                raise ValueError('tile path')
            with _count_lock:
                marker = os.path.join(os.path.dirname(DATA),'radar_bad_tiles')
                try:
                    with open(marker) as f: paths = json.load(f)
                    if not isinstance(paths,list): paths=[]
                except (OSError,ValueError): paths=[]
                paths = [p for p in paths[-127:] if p != path]+[path]
                with open(marker+'.tmp','w') as f: json.dump(paths,f)
                os.replace(marker+'.tmp',marker)
            self.send_response(204);self.send_header('Content-Length','0');self.end_headers()
        except (OSError,ValueError,UnicodeError):
            self.send_error(400)

    def do_GET(self):
        self._immutable_radar = False
        path, _, query = self.path.partition("?")
        _note_lan_viewer(self.client_address[0])
        if path == "/health":
            return self._health()
        if path == "/wx.json":
            global _polls, _renders
            rendered = "r=1" in query.split("&") and self.client_address[0] in LOOPBACK
            viewed_radar = "view=radar" in query.split("&") and self.client_address[0] in LOOPBACK
            with _count_lock:
                _polls += 1
                if rendered:
                    _renders += 1
                if self.client_address[0] in LOOPBACK:
                    params = parse_qs(query, keep_blank_values=True)
                    camera_report = viewed_radar and params.get('radarTheme',[''])[0] in ('paper','night')
                    ordered = 'radarSession' in params
                    accepted = _camera_transaction(_radar_activity(params), params) if ordered and camera_report else not ordered and _radar_owner is None
                    if accepted:
                        _write_radar_preference('radar_smooth', params.get('radarSmooth', []))
                        _write_radar_viewing(viewed_radar)
                    if camera_report and accepted:
                        marker=os.path.join(os.path.dirname(DATA),'radar_activity');tmp=marker+'.tmp'
                        try:
                            with open(tmp,'w') as f:json.dump(_radar_activity(params),f)
                            os.replace(tmp,marker)
                        except OSError:pass
                    if camera_report and accepted and not ordered:
                        _write_settled_camera(_radar_activity(params), params)
                    elif not ordered and accepted and not _read_radar_intent().get('camera') and 'radarSeq' in params:
                        _write_radar_intent(params)  # older pages, until the first camera report
                    elif not ordered and accepted and set(_read_radar_intent()) == {'seq'}:
                        _write_radar_zoom(params.get('radarZoom', []))
                        _write_radar_center(params.get('radarCenter', []))
                        _write_radar_source(params.get('radarSource', []))
                if viewed_radar and accepted:
                    # Share only a timestamp with the emitter. Serialize writers
                    # and replace atomically so it never reads a partial epoch.
                    marker = os.path.join(os.path.dirname(DATA), "radar_viewed")
                    tmp = f"{marker}.tmp.{os.getpid()}"
                    try:
                        with open(tmp, "w") as f:
                            f.write(str(time.time()))
                        os.replace(tmp, marker)
                    except OSError:
                        pass  # an optional demand hint must never break polling
                    finally:
                        try:
                            os.unlink(tmp)
                        except OSError:
                            pass
        return super().do_GET()

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError,ConnectionResetError):
            pass  # ordinary tab closure/cancel, not an unbounded server traceback

    def send_head(self):
        path = self.path.split('?')[0]
        local = self.translate_path(path)
        tile=re.fullmatch(r'/radar/t/([a-f0-9]{12})/(iem-mrms-lcref|iem-nexrad-n0b|rainviewer)/(-|[A-Z0-9]{4})/[0-9]{12}/([0-9]{1,2})/([0-9]{1,4})/([0-9]{1,4})\.png',path,re.ASCII)
        geo=re.fullmatch(r'/radar/geo/([a-f0-9]{12})/(paper|night)/([0-9]{1,2})/([0-9]{1,4})/([0-9]{1,4})\.png',path,re.ASCII)
        sites=re.fullmatch(r'/radar/sites-([a-f0-9]{12})\.json',path,re.ASCII)
        def revision(kind,value):
            try:
                with open(os.path.join(WEB,'radar','.'+kind+'-revision')) as f:return f.read()==value
            except OSError:return False
        immutable=bool(tile and (revision('tile',tile[1]) or revision('smooth',tile[1])) and 4<=int(tile[4])<=10 and int(tile[5])<2**int(tile[4]) and int(tile[6])<2**int(tile[4]) or
                       geo and revision('geo',geo[1]) and 4<=int(geo[3])<=10 and int(geo[4])<2**int(geo[3]) and int(geo[5])<2**int(geo[3]) or
                       sites and revision('sites',sites[1]))
        self._immutable_radar=immutable and os.path.isfile(local)
        if path.startswith(('/radar/t/','/radar/geo/','/radar/sites')) and not self._immutable_radar:
            self.send_error(404,'File not found');return None
        if self._immutable_radar:
            stat=os.stat(local);os.utime(local,ns=(time.time_ns(),stat.st_mtime_ns))
        return super().send_head()

    def end_headers(self):
        if self.path.split('?')[0] == '/wx.json' and self.client_address[0] in LOOPBACK:
            with _count_lock:
                record = _read_radar_intent()
                owner = _radar_owner or dict(session=record.get('session', ''), generation=record.get('generation', 0))
                try:
                    with open(os.path.join(os.path.dirname(DATA), 'radar_smooth')) as stream:
                        smooth = stream.read(128).strip() == 'on'
                except (OSError, UnicodeError):
                    smooth = False
                self.send_header('X-Radar-Smooth', 'on' if smooth else 'off')
                self.send_header('X-Radar-Intent', json.dumps(dict(intent=record, acceptedGeneration=owner['generation'], **owner), separators=(',', ':')))
        if getattr(self, '_immutable_radar', False):
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
        super().end_headers()

    def log_error(self,format,*args):
        if self.path.startswith(('/radar/t/','/radar/geo/')):return
        super().log_error(format,*args)

    def log_request(self, code="-", size="-"):
        # drop the ~2s wx.json/index poll churn (it grew the log unbounded on
        # tmpfs). Keep errors and any other path so real problems still surface.
        p = self.path.split("?")[0]
        if p.startswith(("/radar/t/", "/radar/geo/")): return
        if str(code) in ("200", "304") and p in ("/wx.json", "/index.html", "/health", "/"):
            return
        super().log_request(code, size)

    def _health(self):
        h = {"status": "ok", "polls": _polls, "renders": _renders}
        try:
            with open(DATA) as f:
                d = json.load(f)
            age = time.time() - float(d.get("ts", 0))
            h["dataAgeSec"]      = round(age, 1)
            h["radar"] = (d.get("radar") or {}).get("health", dict(lastSuccessTs=None,
                successRate60s=None, hedges=0, retries=0, breaker="closed", lastError=None))
            h["station"]         = d.get("station")
            h["temp"]            = d.get("temp")
            h["updateAvailable"] = d.get("updateAvailable")
            obs_age = d.get("obsAgeSec")
            # a bool is not an age, nor is NaN/inf: treat those as absent
            if isinstance(obs_age, bool) or not isinstance(obs_age, (int, float)) or not math.isfinite(obs_age):
                obs_age = None
            else:
                obs_age = float(obs_age) + max(age, 0.0)
            h["obsAgeSec"] = round(obs_age, 1) if obs_age is not None else None
            # Order matters: a stalled engine is the bigger fault, and its stale
            # file makes every observation in it look old too.
            if age > STALE_SEC:
                h["status"], h["reason"] = "stale", "engine stalled"
            elif obs_age is not None and obs_age > OBS_STALE_SEC:
                h["status"], h["reason"] = "degraded", "sensor silent"
        except Exception as e:                                            # noqa: BLE001
            h["status"] = "error"
            h["reason"] = h["error"] = str(e)
        try:
            body = json.dumps(h, allow_nan=False).encode()
        except ValueError as e:                                          # a non-finite crept in
            h["status"] = "error"
            body = json.dumps({"status": "error", "reason": str(e)}).encode()
        self.send_response(200 if h["status"] == "ok" else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server((BIND, PORT), Handler) as httpd:
        httpd.serve_forever()
