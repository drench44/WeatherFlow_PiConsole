#!/usr/bin/env python3
"""Evaluate a JavaScript expression inside the running kiosk page.

Run ON the Pi (the debug port is loopback only):

    python3 design/almanac/kiosk/tools/cdp_probe.py 'radarReady().length'
    python3 design/almanac/kiosk/tools/cdp_probe.py '({mem:radarMemory(),cap:RAD_MEMORY_CAP})'

Needs Chromium started with --remote-debugging-port=9222 (the kiosk unit does)
and the `websocket-client` package (present on the Pi 4's system Python). The
result is printed as JSON. Promises are awaited. Use it to A/B a page change
against the live counters (radarMemory, radarReserved, radarTiles.size,
radarView.loaded bitmaps) before calling it done: the Mac harness has no
Pi-sized memory pressure.
"""
import json
import sys
import urllib.request

import websocket  # websocket-client

PORT = 9222


def main(expression):
    targets = json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json', timeout=5))
    page = next(t for t in targets if t['type'] == 'page' and '8137' in t['url'])
    ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=30)
    try:
        ws.send(json.dumps(dict(id=1, method='Runtime.evaluate',
                                params=dict(expression=expression, returnByValue=True, awaitPromise=True))))
        while True:
            msg = json.loads(ws.recv())
            if msg.get('id') != 1:
                continue
            result = msg.get('result', {})
            if 'exceptionDetails' in result:
                print(json.dumps(result['exceptionDetails'], indent=1))
                return 1
            value = result.get('result', {})
            print(json.dumps(value.get('value', value), indent=1))
            return 0
    finally:
        ws.close()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'document.title'))
