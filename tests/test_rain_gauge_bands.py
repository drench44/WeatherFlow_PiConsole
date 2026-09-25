"""The rate gauge's band graduations stay inside the tube walls."""
import re
from pathlib import Path

HTML = Path('design/almanac/console_live.html').read_text()


def test_band_lines_end_inside_the_walls():
    block = HTML[HTML.index('var svg = $("raingauge");'):HTML.index('rainPtr = el("g"')]
    walls = re.findall(r'el\("line", \{ class: "tick", x1: (\w+),', block)
    assert walls == ['x1', 'x2']
    band = re.search(r'el\("line", \{ class: "rain-band", x1: ([^,]+), y1: y, x2: ([^,]+), y2: y \}', block)
    assert band, 'band graduation line not found'
    left, right = (s.replace(' ', '') for s in band.groups())
    # left edge at or inside the left wall; right edge inside the right wall
    assert left in ('x1+1', 'x1') and right in ('x2-1', 'x2'), (left, right)
