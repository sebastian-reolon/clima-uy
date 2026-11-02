"""SVG charts for the public site. Pure functions: data in, `<svg>` string out.

Every chart carries role="img" with <title> and <desc> as its first children,
so the data is readable without seeing the picture. No ids are emitted, so
many charts can share one page.

Wide charts (profile, forecast, seasons, lag) share a 600-unit-wide viewBox
and a 23-unit font: at 390 px (358 px of chart) that is 13.7 px. site.css
lowers the font size on wider screens so text does not grow with the chart.
Strokes use non-scaling-stroke, so their widths are in screen pixels.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean
from typing import Iterable, Sequence
from xml.sax.saxutils import escape

from ..analysis.lag import LagResult
from ..analysis.status import StationStatus
from ..catalog import Station
from ..fmt import day_numeric as _day, day_short as _day_short, days as _days
from ..fmt import is_record, record_text, since_year
from ..fmt import meters as _m, num as _num, pct as _pct
from ..store import Forecast
from ..timeutil import local_day

# Palette: sRGB hex of the OKLCH tokens in design.md (static/v2/css/site.css).
# Colour only marks a state: blue = water or rising, red = alert, the rest ink.
PAPER = "#F7F8F9"  # --paper  oklch(97.8% 0.002 250)
INK = "#15191E"  # --ink    oklch(21% 0.012 255)
INK_2 = "#54585F"  # --ink-2  oklch(46% 0.012 255)
GRID = "#D2D8DE"  # --grid-2 oklch(88% 0.01 245), chart grid
WATER = "#0062A1"  # --water  oklch(48% 0.13 245); --water-2 is this at opacity .12
ALERT = "#C92420"  # --alert  oklch(54% 0.2 28)
# Older names kept so the /rio/ charts did not change shape: today's line and
# water fills (was sediment brown) are water, the 3-days-ago line and past
# seasons (was a muted teal) are ink-2. design.md has a single red, dark
# enough for thin lines, so alert lines use it and the evacuation line is told
# apart by its dash and its label.
SILT = WATER
LOW = INK_2
ALERT_LINE = ALERT
EVAC = ALERT
EVAC_DASH = "6 3"

FONT = "Barlow, sans-serif"
WIDE_W = 600
WIDE_FONT = 23
GAUGE_W = 320
GAUGE_FONT = 14
LINE_GAP = 27  # baseline-to-baseline distance for WIDE_FONT text

UNITS = {"level": "m", "discharge": "m³/s"}


# ---------------------------------------------------------------- helpers

def _f(v: float) -> str:
    """Compact coordinate."""
    s = f"{v:.1f}"
    return s[:-2] if s.endswith(".0") else s


def _svg(kind: str, width: float, height: float, title: str, desc: str, body: Iterable[str],
         font_size: float = WIDE_FONT) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="chart {kind}" role="img" '
        f'viewBox="0 0 {_f(width)} {_f(height)}" width="100%" '
        f'font-family="{FONT}" font-size="{_f(font_size)}" fill="{INK}">'
        f"<title>{escape(title)}</title><desc>{escape(desc)}</desc>"
        + "".join(body)
        + "</svg>"
    )


def _text(x: float, y: float, s: str, anchor: str = "start", fill: str | None = None,
          weight: int | None = None, cls: str | None = None, extra: str = "") -> str:
    attrs = f'x="{_f(x)}" y="{_f(y)}"'
    if anchor != "start":
        attrs += f' text-anchor="{anchor}"'
    if fill:
        attrs += f' fill="{fill}"'
    if weight:
        attrs += f' font-weight="{weight}"'
    if cls:
        attrs += f' class="{cls}"'
    if extra:
        attrs += " " + extra
    return f"<text {attrs}>{escape(s)}</text>"


def _line(x1, y1, x2, y2, stroke: str, width: float = 1, dash: str | None = None, cls: str | None = None) -> str:
    extra = f' stroke-dasharray="{dash}"' if dash else ""
    extra += f' class="{cls}"' if cls else ""
    return (
        f'<line x1="{_f(x1)}" y1="{_f(y1)}" x2="{_f(x2)}" y2="{_f(y2)}" stroke="{stroke}" '
        f'stroke-width="{_f(width)}" vector-effect="non-scaling-stroke"{extra}/>'
    )


def _path_d(runs: Sequence[Sequence[tuple[float, float]]]) -> str:
    """One subpath per run. A single-point run repeats its point so a round cap draws a dot."""
    parts = []
    for run in runs:
        pts = list(run) if len(run) > 1 else [run[0], run[0]]
        parts.append("M" + " L".join(f"{_f(x)} {_f(y)}" for x, y in pts))
    return " ".join(parts)


def _path(d: str, stroke: str, width: float, dash: str | None = None, cls: str | None = None,
          extra: str = "") -> str:
    attrs = (
        f'd="{d}" fill="none" stroke="{stroke}" stroke-width="{_f(width)}" '
        'stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"'
    )
    if dash:
        attrs += f' stroke-dasharray="{dash}"'
    if cls:
        attrs += f' class="{cls}"'
    return f"<path {attrs}{extra}/>"


def _runs(values: Sequence[float | None]) -> list[list[int]]:
    """Index runs of consecutive non-None values."""
    runs: list[list[int]] = []
    cur: list[int] = []
    for i, v in enumerate(values):
        if v is None:
            if cur:
                runs.append(cur)
            cur = []
        else:
            cur.append(i)
    if cur:
        runs.append(cur)
    return runs


def _nice_step(span: float, target: int) -> float:
    raw = span / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _ticks(lo: float, hi: float, target: int = 4) -> tuple[float, float, list[float]]:
    """Nice axis bounds and ticks covering [lo, hi]."""
    if hi <= lo:
        pad = abs(lo) * 0.1 or 1.0
        lo, hi = lo - pad, hi + pad
    step = _nice_step(hi - lo, target)
    start = math.floor(lo / step) * step
    end = math.ceil(hi / step) * step
    n = int(round((end - start) / step))
    return start, end, [start + i * step for i in range(n + 1)]


def _tick_label(v: float, step: float) -> str:
    """Axis number with the decimals the step needs: a 2.5 step reads 2,5 / 5,0 / 7,5, never 2 / 8."""
    if abs(v) < 1e-9:
        return "0"
    if abs(step - round(step)) < 1e-9:
        return _num(v, 0)
    return _num(v, 1 if abs(step * 10 - round(step * 10)) < 1e-9 else 2)


def _text_width(s: str, size: float) -> float:
    """Rough advance width in Barlow, enough to keep labels apart.

    Barlow runs 0,43 to 0,45 em per character on Spanish words, 0,5 on
    digits and up to 0,55 on words heavy in m, d, b, o (measured on the
    self-hosted woff2). 0,5 keeps a margin on most labels and still lets two
    rows of names fit under the old profile.
    """
    return len(s) * size * 0.5


WIDE_FONT_DESKTOP = 14  # .chart-wide text size at >= 56em in site.css


def _legend_row(items: Sequence[tuple[str, str]], y: float, size: float) -> list[str]:
    out = []
    x = 0.0
    for sample, label in items:
        out.append(sample.format(x0=_f(x), x1=_f(x + 26), xm=_f(x + 13), xa=_f(x + 16), xb=_f(x + 26), y=_f(y - 7)))
        out.append(_text(x + 34, y, label, fill=INK_2))
        x += 34 + _text_width(label, size) + 24
    return out


def _legend(items: Sequence[tuple[str, str]], y: float) -> list[str]:
    """Legend row of (sample markup with {x0} {x1} {xm} {y}, label).

    Laid out twice: for the phone text size (shown by default) and for the
    smaller desktop size (hidden until site.css swaps them at >= 56em), so the
    gaps stay even on both.
    """
    return (
        ['<g class="legend-narrow">'] + _legend_row(items, y, WIDE_FONT) + ["</g>"]
        + ['<g class="legend-wide" display="none">'] + _legend_row(items, y, WIDE_FONT_DESKTOP) + ["</g>"]
    )


def _sample_line(color: str, width: float, dash: str | None = None) -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        '<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" '
        f'stroke="{color}" stroke-width="{_f(width)}" stroke-linecap="round" '
        f'vector-effect="non-scaling-stroke"{dash_attr}/>'
    )


def _sample_alert_tick() -> str:
    """A single short tick, as drawn at each station."""
    return (
        '<line x1="{xa}" y1="{y}" x2="{xb}" y2="{y}" '
        f'stroke="{ALERT_LINE}" stroke-width="3" vector-effect="non-scaling-stroke"/>'
    )


def _sample_band(color: str, opacity: float) -> str:
    return f'<rect x="{{x0}}" y="{{y}}" width="26" height="12" transform="translate(0 -6)" fill="{color}" fill-opacity="{opacity}"/>'


# ---------------------------------------------------------------- profile

# P_LEFT leaves room for "mediana" in Barlow at the wide font (about 87 units).
P_LEFT, P_RIGHT = 98, 584
P_TOP, P_BOTTOM = 160, 424
DOTTED = "0 7"  # with round caps: a dotted line
# Exceedance axis: e = 100 - percentile on a log scale, from the median (e = 50,
# bottom) to rarer than 99% of days (e = 1, top). Values above 99 sit on the top
# edge; values below the median are not on the axis and get an open marker.
E_MIN, E_MAX = 1.0, 50.0
P_GRID = [(50, "mediana"), (75, "75"), (90, "90"), (95, "95"), (99, "99")]
# Stations named along the stem when present: id -> short public name.
ANCHORS = {
    "ana_73200080": "Itá",
    "ina_67": "Garruchos",
    "ina_72": "Paso de los Libres",
    "ina_77": "Salto Grande",
    "ina_79": "Concordia",
    "ina_1699": "Nueva Palmira",
}
# Placement priority for the names under the axis; lower first.
_ANCHOR_PRIORITY = ["ina_72", "ina_77", "ina_67", "ana_73200080", "ina_79", "ina_1699"]
HALO = f'stroke="{PAPER}" stroke-width="6" stroke-linejoin="round" paint-order="stroke"'


def profile_y(p: float) -> float:
    """Vertical position of percentile p on the profile (clamped to the plot)."""
    e = min(max(100.0 - p, E_MIN), E_MAX)
    return P_TOP + (P_BOTTOM - P_TOP) * math.log10(e / E_MIN) / math.log10(E_MAX / E_MIN)


def _station_display(station: Station) -> str:
    """'Salto (medido en Concordia)' for a Uruguayan city, else the station name."""
    if station.represents and station.represents != station.name:
        return f"{station.represents} (medido en {station.name})"
    return station.represents or station.name


def _city_label(station: Station, status: StationStatus | None) -> tuple[str, str, str | None]:
    """(city, how it is measured, state line or None) for a station that represents a Uruguayan city."""
    city = station.represents or station.name
    how = f", medido en {station.name}" if city != station.name else ""
    if status is not None and not status.stale and status.percentile_today is not None:
        return city, how, None
    if status is not None and status.stale and status.stale_since:
        return city, how + ":", f"desactualizado desde el {_day_short(status.stale_since)}"
    if status is not None and not status.stale:
        return city, how + ":", "sin percentil hoy"
    return city, how + ":", "sin estación con datos al día"


def _profile_line(station: Station, status: StationStatus | None) -> str:
    name = _station_display(station)
    if status is None:
        return f"{name}: sin dato"
    if status.stale:
        since = f" desde el {_day(status.stale_since)}" if status.stale_since else ""
        return f"{name}: desactualizada{since}"
    if status.percentile_today is None:
        return f"{name}: sin percentil"
    parts = [f"hoy {_pct(status.percentile_today)}"]
    if status.percentile_today < 50:
        parts[0] += ", por debajo de la mediana"
    if status.percentile_3d_ago is not None:
        parts.append(f"hace 3 días {_pct(status.percentile_3d_ago)}")
    if status.alert_percentile is not None and status.variable_used == "level":
        below = ", por debajo de la mediana" if status.alert_percentile < 50 else ""
        parts.append(f"alerta en {_pct(status.alert_percentile)}{below}")
    return f"{name}: " + ", ".join(parts)


def _place_rows(items: Sequence[tuple[float, float, str, str]], rows: int, gap: float = 14) -> list[tuple[int, float, str, str]]:
    """Greedy placement of (start, width, text, anchor-key) labels on up to `rows` rows; drops what does not fit."""
    taken: list[list[tuple[float, float]]] = [[] for _ in range(rows)]
    out = []
    for start, w, s, key in items:
        for r in range(rows):
            if all(start + w + gap <= a or start >= b + gap for a, b in taken[r]):
                taken[r].append((start, start + w))
                out.append((r, start, s, key))
                break
    return out


def profile_svg(statuses: Sequence[StationStatus], catalog: Sequence[Station]) -> str:
    """Long profile of the main river: how high each station is against its own history, upstream to downstream.

    The y axis is the share of days since 1990 that were lower (see profile_y).
    Stale stations, stations without a percentile and stations below their
    median break the line; the last ones get an open marker on the bottom edge.
    The today path carries `data-d0`, the same path with each point at its
    value of 3 days earlier (or today's when that is missing), for morph.js.
    """
    by_id = {s.station_id: s for s in statuses}
    stations = sorted((s for s in catalog if s.tributary_of is None), key=lambda s: s.order)
    rows = [(st, by_id.get(st.id)) for st in stations]
    n = len(rows)

    def x(i: int) -> float:
        return P_LEFT + (P_RIGHT - P_LEFT) * (i / (n - 1) if n > 1 else 0.5)

    def fresh(ss: StationStatus | None, attr: str) -> float | None:
        if ss is None or ss.stale:
            return None
        return getattr(ss, attr)

    raw_today = [fresh(ss, "percentile_today") for _, ss in rows]
    today = [p if p is not None and p >= 50 else None for p in raw_today]
    raw_ago = [fresh(ss, "percentile_3d_ago") for _, ss in rows]
    ago = [p if p is not None and p >= 50 else None for p in raw_ago]

    body: list[str] = []
    body += _legend(
        [
            (_sample_line(SILT, 3.5), "hoy"),
            (_sample_line(LOW, 2.5, DOTTED), "hace 3 días"),
            (_sample_alert_tick(), "cota de alerta"),
        ],
        y=26,
    )
    body.append(_text(0, 66, "% de los días desde 1990 con el río más bajo", fill=INK_2))

    # Grid and y axis.
    for p, label in P_GRID:
        body.append(_line(P_LEFT, profile_y(p), P_RIGHT, profile_y(p), GRID, 1))
        body.append(_text(P_LEFT - 8, profile_y(p) + 7, label, anchor="end", fill=INK_2))
    # Station ticks along the bottom, like a ruler.
    body.append('<g class="stations">')
    for i in range(n):
        body.append(_line(x(i), P_BOTTOM, x(i), P_BOTTOM + 7, INK_2, 1))
    body.append("</g>")

    # Names along the stem: ends first, then anchor stations, on two rows.
    name_y = P_BOTTOM + 34
    candidates: list[tuple[float, float, str, str]] = []
    if n:
        candidates.append((P_LEFT, _text_width("aguas arriba", WIDE_FONT), "aguas arriba", "start"))
        w = _text_width("desembocadura", WIDE_FONT)
        candidates.append((P_RIGHT - w, w, "desembocadura", "end"))
    index = {st.id: i for i, (st, _) in enumerate(rows)}
    for sid in _ANCHOR_PRIORITY:
        if sid in index:
            label = ANCHORS[sid]
            w = _text_width(label, WIDE_FONT)
            start = min(max(x(index[sid]) - w / 2, 0), WIDE_W - w)
            candidates.append((start, w, label, "anchor"))
    placed = _place_rows(candidates, rows=2)
    names = []
    for r, start, label, key in placed:
        # Anchored at the point it names, so it stays in place when CSS shrinks the text.
        if key == "end":
            pos = f'x="{_f(P_RIGHT)}" text-anchor="end"'
        elif key == "start":
            pos = f'x="{_f(start)}"'
        else:
            pos = f'x="{_f(start + _text_width(label, WIDE_FONT) / 2)}" text-anchor="middle"'
        names.append(f'<text {pos} y="{_f(name_y + r * LINE_GAP)}" fill="{INK_2}" {HALO} class="stem-name">'
                     f"{escape(label)}</text>")
    names_rows = 1 + max((r for r, *_ in placed), default=0)

    # Uruguayan cities: a guide through the plot and a stepped label below the names.
    cities = [(i, st, ss) for i, (st, ss) in enumerate(rows) if st.represents]
    ly = name_y + names_rows * LINE_GAP + 14
    guides: list[str] = []
    city_texts: list[str] = []
    for i, st, ss in cities:
        city, how, state = _city_label(st, ss)
        guides.append(_line(x(i), P_TOP, x(i), ly - 19, GRID, 1, cls="city-guide"))
        for bold, rest in [(city, how)] + ([("", state)] if state else []):
            w = _text_width(bold + rest, WIDE_FONT)
            end = x(i) + 4
            pos = f'x="{_f(end)}" text-anchor="end"' if end - w >= 0 else 'x="0"'
            spans = (f'<tspan font-weight="600">{escape(bold)}</tspan>' if bold else "") + (
                f'<tspan fill="{INK_2}">{escape(rest)}</tspan>' if rest else "")
            city_texts.append(f'<text {pos} y="{_f(ly)}" class="city">{spans}</text>')
            ly += LINE_GAP
    # Guides run under the names, which carry a paper halo.
    body += guides + names + city_texts
    height = (ly - LINE_GAP if cities else name_y + (names_rows - 1) * LINE_GAP) + 12

    # Alert ticks under the lines, only where the station has data today and the line is
    # its level: the alert percentile is among levels, not comparable with a discharge one.
    for i, (_, ss) in enumerate(rows):
        if (raw_today[i] is not None and ss is not None and ss.alert_percentile is not None
                and ss.variable_used == "level"):
            ay = profile_y(ss.alert_percentile)
            if ss.alert_percentile < 50:
                # Off the axis: an open (broken) tick on the bottom edge, like today's open marker.
                body.append(_line(x(i) - 5, ay, x(i) + 5, ay, ALERT_LINE, 3, dash="2 2",
                                  cls="alert-tick below-median"))
            else:
                body.append(_line(x(i) - 5, ay, x(i) + 5, ay, ALERT_LINE, 3, cls="alert-tick"))

    ago_runs = [[(x(i), profile_y(ago[i])) for i in run] for run in _runs(ago)]
    if ago_runs:
        body.append(_path(_path_d(ago_runs), LOW, 2.5, dash=DOTTED, cls="ago"))

    today_idx = _runs(today)
    if today_idx:
        d = _path_d([[(x(i), profile_y(today[i])) for i in run] for run in today_idx])
        d0 = _path_d([[(x(i), profile_y(raw_ago[i] if raw_ago[i] is not None else today[i])) for i in run]
                      for run in today_idx])
        body.append(_path(d, SILT, 3.5, cls="today", extra=f' data-d0="{d0}"'))
    for i, p in enumerate(raw_today):
        if p is not None and p < 50:
            body.append(f'<circle class="below-median" cx="{_f(x(i))}" cy="{_f(P_BOTTOM)}" r="4.5" fill="{PAPER}" '
                        f'stroke="{SILT}" stroke-width="2" vector-effect="non-scaling-stroke"/>')
    if not any(p is not None for p in raw_today):
        body.append(_text((P_LEFT + P_RIGHT) / 2, (P_TOP + P_BOTTOM) / 2, "sin datos actuales", anchor="middle", fill=INK_2))

    # The crest, named directly.
    ranked = [(p, i) for i, p in enumerate(raw_today) if p is not None]
    if ranked:
        p, i = max(ranked, key=lambda t: (t[0], -t[1]))
        line1 = f"pico hoy: {_station_display(rows[i][0])}"
        crest = rows[i][1]
        line2 = (record_text(since_year(crest.history_start_year)) if is_record(p)
                 else f"más alto que el {_pct(p)}% de los días")
        w = max(_text_width(line1, WIDE_FONT), _text_width(line2, WIDE_FONT))
        cx = min(max(x(i), w / 2), WIDE_W - w / 2)
        body.append(_line(x(i), 136, x(i), profile_y(max(p, 50)) - 6, INK, 1, cls="crest-leader"))
        body.append(
            f'<text x="{_f(cx)}" y="104" text-anchor="middle" class="crest">'
            f'<tspan font-weight="600">{escape(line1)}</tspan>'
            f'<tspan x="{_f(cx)}" dy="{LINE_GAP}">{escape(line2)}</tspan></text>'
        )

    desc = (
        "Qué tan alto está el río en cada estación, de aguas arriba a aguas abajo, comparado con la "
        "historia diaria de esa misma estación desde 1990: un valor de 90 quiere decir más alto que el 90% "
        "de los días. La escala va de la mediana (abajo) a 99 (arriba); las estaciones por debajo de la "
        "mediana quedan marcadas en el borde inferior. "
        + "; ".join(_profile_line(st, ss) for st, ss in rows)
        + "."
    )
    return _svg("chart-wide chart-profile", WIDE_W, height,
                "Perfil del río Uruguay: qué tan alto está cada estación hoy", desc, body)


# ---------------------------------------------------------------- plan profile (home)
#
# The home's drawing: x = great-circle km along the main river, y = percentile
# on a linear axis. Styled by the `.perfil` rules of static/v2/css/site.css
# (class names below), not inline, like the approved mockup.

MAIN_RIVER = "Uruguay"
PLAN_WIDE_W = 1100
# Ruling 6: the Uruguayan stretch starts this many wide-variant units upstream
# of Bella Unión's station. Turned into km once, so the shading starts at the
# same point of the river in every variant.
PLAN_UY_LEAD = 14
PLAN_UY_CITY = "Bella Unión"
# Ruling 7: the phone variant starts here, or 60 km above the first callout.
PHONE_START_KM = 620.0
PHONE_CALLOUT_LEAD_KM = 60.0
_EARTH_KM = 6371.0


def _plan_margins(variant: str) -> tuple[int, int, int, int]:
    """Left, right, top, bottom margins in viewBox units."""
    return (64, 24, 36, 150) if variant == "wide" else (34, 8, 36, 240)


def _great_circle_km(a: Station, b: Station) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_KM * math.asin(math.sqrt(h))


def river_km(catalog: Sequence[Station]) -> dict[str, float]:
    """Cumulative great-circle km of each main-river station from the first one, in catalogue order (Ruling 5).

    A station at the very same point as the previous one is a twin gauge
    (Salto Grande Arriba and Abajo, upstream and downstream of the dam): only
    the first, Arriba, is kept.
    """
    km: dict[str, float] = {}
    acc = 0.0
    prev: Station | None = None
    for st in sorted(catalog, key=lambda s: s.order):
        if st.river != MAIN_RIVER:
            continue
        if prev is not None:
            step = _great_circle_km(prev, st)
            if step < 1e-6:
                continue
            acc += step
        km[st.id] = acc
        prev = st
    return km


def _plan_points(statuses: Sequence[StationStatus], catalog: Sequence[Station],
                 start_km: float = 0.0) -> list[tuple[float, StationStatus, Station]]:
    """(km, status, station) of each main-river station with a percentile today, downstream."""
    by_id = {s.station_id: s for s in statuses}
    stations = {s.id: s for s in catalog}
    out = []
    for sid, k in river_km(catalog).items():
        ss = by_id.get(sid)
        if ss is None or ss.percentile_today is None or k < start_km:
            continue
        out.append((k, ss, stations[sid]))
    return out


def _is_callout(ss: StationStatus, st: Station) -> bool:
    """Ruling 7: Uruguayan cities on the main river, plus any other main-river station above alert."""
    return st.represents is not None or bool(ss.above_alert)


def phone_start_km(statuses: Sequence[StationStatus], catalog: Sequence[Station]) -> float:
    """Where the phone variant starts: 620 km, or 60 km above the first callout when that is further up."""
    callouts = [k for k, ss, st in _plan_points(statuses, catalog) if _is_callout(ss, st)]
    start = PHONE_START_KM if not callouts else min(PHONE_START_KM, callouts[0] - PHONE_CALLOUT_LEAD_KM)
    return max(0.0, start)


def _floor_pct(p: float) -> int:
    return min(100, math.floor(p + 1e-9))


def _plan_label(ss: StationStatus, st: Station) -> tuple[str, str, bool]:
    """(name, value line, alert style) of a callout."""
    if ss.level_m is not None:
        reading = f"{_num(ss.level_m, 2)} m"
    elif ss.variable_used == "discharge" and ss.latest_value is not None:
        reading = f"{_num(ss.latest_value, 0)} m³/s"
    else:
        reading = None
    if st.represents is not None:
        tail = f"P{_floor_pct(ss.percentile_today)}"
        name, alert = st.represents, bool(ss.above_alert)
    else:
        tail = "sobre alerta"
        name, alert = st.name, True
    return name, (f"{reading} · {tail}" if reading else tail), alert


Slot = tuple[int, float, bool]  # row, box left edge, box right of its leader


def _place_labels(xs: Sequence[float], widths: Sequence[float], prefer_start: Sequence[bool],
                  width: float, one_per_row: bool, order: Sequence[int],
                  required: Sequence[bool]) -> list[Slot | None]:
    """Row, box left edge and side of each label; None for a label left out.

    Leaders drop straight down from each point to its box, so two rules keep
    every label readable: boxes in one row do not touch, and no box covers the
    leader of a label in a row below it. Greedy over `order`: a label joins an
    existing row (wide only; it starts staggered over two rows like the
    mockup) or a new row inserted wherever both rules hold. Phone labels get a
    row each and first try not to cover the leaders still to come, which gives
    the mockup's cascade. A label with no such place goes to a new bottom row
    when `required`, else is left out. Rows are ranked at the end.
    """
    gap, pad = 8.0, 2.0
    placed: list[tuple[float, float, float, float]] = []  # row, left, right, leader x

    def box(i: int, start: bool) -> tuple[float, float]:
        left = xs[i] + 1 if start else xs[i] - 1 - widths[i]
        left = min(max(left, pad), width - pad - widths[i])
        return left, left + widths[i]

    def covers(left: float, right: float, x: float) -> bool:
        return left - pad <= x <= right + pad

    def fits(i: int, row: float, left: float, right: float, ahead: Sequence[float]) -> bool:
        if any(covers(left, right, x) for x in ahead):
            return False
        for r, l2, r2, x2 in placed:
            if r == row and (one_per_row or (left < r2 + gap and l2 < right + gap)):
                return False
            if r < row and covers(l2, r2, xs[i]):
                return False
            if r > row and covers(left, right, x2):
                return False
        return True

    def candidates(k: int) -> list[float]:
        rows = sorted({r for r, *_ in placed})
        if not rows:
            return [0.0]
        below, above = rows[-1] + 1, rows[0] - 1
        between = [(a + b) / 2 for a, b in zip(rows, rows[1:])]
        if one_per_row:
            return [below] + between[::-1] + [above]
        stagger = k % 2
        first = [rows[stagger]] if stagger < len(rows) else [below]
        return first + [r for r in rows if r not in first] + [below] + between + [above]

    slots: dict[int, tuple[float, float, bool]] = {}
    for k, i in enumerate(order):
        sides = (prefer_start[i], not prefer_start[i])
        to_come = [xs[j] for j in order[k + 1:]]
        choice = None
        for ahead in (to_come, ()):
            for row in candidates(k):
                for start in sides:
                    left, right = box(i, start)
                    if fits(i, row, left, right, ahead):
                        choice = (row, left, start)
                        break
                if choice:
                    break
            if choice:
                break
        if choice is None:
            if not required[i]:
                continue
            # No place keeps every leader clear: a new bottom row at least keeps boxes apart.
            bottom = max((r for r, *_ in placed), default=-1.0) + 1
            choice = (bottom, box(i, prefer_start[i])[0], prefer_start[i])
        placed.append((choice[0], choice[1], choice[1] + widths[i], xs[i]))
        slots[i] = choice
    rank = {r: n for n, r in enumerate(sorted({r for r, _, _ in slots.values()}))}
    return [(rank[slots[i][0]], slots[i][1], slots[i][2]) if i in slots else None for i in range(len(xs))]


def profile_plan_svg(statuses: Sequence[StationStatus], catalog: Sequence[Station], width: int = 1100,
                     height: int = 500, start_km: float = 0.0, variant: str = "wide") -> str:
    """The river's longitudinal profile as a technical drawing (home page, Rulings 4 to 7).

    x: km from Machadinho along straight lines between stations. y: today's
    percentile of each station against its own history, linear from 100 down
    to 50, or lower when a plotted value is lower. Callouts name the
    Uruguayan cities and any other station above its alert level. `variant`
    "movil" is the cropped phone drawing (class `perfil movil`, start it at
    `phone_start_km`; height 560 gives the mockup's plot). `height` sets the
    plot area: the drawing ends below the last callout row.
    """
    big = variant == "wide"
    L, R, T, B = _plan_margins(variant)
    pw, ph = width - L - R, height - T - B
    km = river_km(catalog)
    k_max = max(km.values(), default=0.0) or 1.0
    k0 = min(max(0.0, start_km), k_max - 1.0)
    pts = _plan_points(statuses, catalog, k0)

    values = [ss.percentile_today for _, ss, _ in pts] + [
        ss.percentile_3d_ago for _, ss, _ in pts if ss.percentile_3d_ago is not None]
    low = min(values, default=100.0)
    p_min = 50 if low >= 50 else max(0, math.floor(low / 10) * 10)

    def X(k: float) -> float:
        return L + (k - k0) / (k_max - k0) * pw

    def Y(p: float) -> float:
        return T + (100 - min(p, 100.0)) / (100 - p_min) * ph

    o: list[str] = []
    for p in range(p_min, 101, 10):
        o.append(f'<line x1="{L}" x2="{width - R}" y1="{_f(Y(p))}" y2="{_f(Y(p))}" class="g{" g2" if p == 90 else ""}"/>')
        o.append(f'<text x="{L - 8}" y="{_f(Y(p) + 4)}" class="ax" text-anchor="end">{p}</text>')
    o.append(f'<text x="{L}" y="{T - 14}" class="ax">percentil</text>')
    for k in range(int(math.ceil(k0 / 200) * 200), int(k_max) + 1, 200):
        o.append(f'<line x1="{_f(X(k))}" x2="{_f(X(k))}" y1="{_f(T + ph)}" y2="{_f(T + ph + 6)}" class="tick"/>')
        o.append(f'<text x="{_f(X(k))}" y="{_f(T + ph + 20)}" class="ax" text-anchor="middle">{_num(k, 0)}</text>')
    caption = ("km desde Machadinho, en línea recta entre estaciones (aprox.)" if big
               else "km desde Machadinho (aprox.)")
    o.append(f'<text x="{L}" y="{_f(T + ph + 40)}" class="ax">{escape(caption)}</text>')

    uy = next((s for s in catalog if s.represents == PLAN_UY_CITY and s.id in km), None)
    if uy is not None:
        wide_l, wide_r, _, _ = _plan_margins("wide")
        lead_km = PLAN_UY_LEAD * k_max / (PLAN_WIDE_W - wide_l - wide_r)
        ub = max(L, X(km[uy.id] - lead_km))
        o.append(f'<rect x="{_f(ub)}" y="{T}" width="{_f(width - R - ub)}" height="{_f(ph)}" class="uy"/>')
        o.append(f'<text x="{_f(ub + 6)}" y="{T + 14}" class="axl">tramo con costa uruguaya</text>')

    xy = [(X(k), Y(ss.percentile_today), ss, st) for k, ss, st in pts]
    ago = [(x, Y(ss.percentile_3d_ago)) for x, _, ss, _ in xy if ss.percentile_3d_ago is not None]
    if ago:
        o.append('<polyline class="l3" fill="none" points="' + " ".join(f"{_f(x)},{_f(y)}" for x, y in ago) + '"/>')
    if xy:
        o.append('<polyline class="l0" fill="none" points="' + " ".join(f"{_f(x)},{_f(y)}" for x, y, _, _ in xy) + '"/>')
    for x, y, ss, st in xy:
        city = st.represents is not None
        cls = "pa" if ss.above_alert else ("pc" if city else "p")
        o.append(f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{4.5 if city else 2.6}" class="{cls}"/>')

    # Callouts: leaders first, then the paper boxes, then the text, so a box
    # never hides another label's text.
    calls = [(x, y, *_plan_label(ss, st)) for x, y, ss, st in xy if _is_callout(ss, st)]
    is_city = [st.represents is not None for x, y, ss, st in xy if _is_callout(ss, st)]
    # Per-character widths: 0,55 em of the bold name and 0,5 em of the value at
    # the sizes site.css sets (14 / 12,5 wide, 13 / 11,5 phone), above Barlow's
    # measured 0,45 em, so the paper box always clears the text.
    name_em, value_em = (7.7, 6.25) if big else (7.15, 5.75)
    widths = [max(len(name) * name_em, len(value) * value_em) + 6 for _, _, name, value, _ in calls]
    prefer_start = [x < width * (0.4 if big else 0.5) for x, *_ in calls]
    args = ([c[0] for c in calls], widths, prefer_start, width, not big)
    slots = _place_labels(*args, order=range(len(calls)), required=[False] * len(calls))
    if None in slots:
        # Too many callouts to keep every leader clear (a flood across the
        # basin): cities first and always, then the others that still fit.
        # Those left out keep their alert dot.
        order = [i for i in range(len(calls)) if is_city[i]] + [i for i in range(len(calls)) if not is_city[i]]
        slots = _place_labels(*args, order=order, required=is_city)
    base = T + ph + (52 if big else 64)
    row_h = 44 if big else 38
    # A top-row box under the km caption would hide it: drop the rows below it.
    caption_right = L + len(caption) * 6.0
    if any(slot and slot[0] == 0 and slot[1] < caption_right and base - 14 < T + ph + 44 for slot in slots):
        base = T + ph + 44 + 14
    leaders, boxes, texts = [], [], []
    bottom = 0.0
    for (x, y, name, value, alert), w, slot in zip(calls, widths, slots):
        if slot is None:
            continue
        row, left, start = slot
        ty = base + row * row_h
        bottom = max(bottom, ty + 22)
        tx, anchor = (left + 3, "start") if start else (left + w - 3, "end")
        leaders.append(f'<line x1="{_f(x)}" y1="{_f(y + 6)}" x2="{_f(x)}" y2="{_f(ty - 14)}" class="lead"/>')
        boxes.append(f'<rect x="{_f(left)}" y="{_f(ty - 14)}" width="{_f(w)}" height="36" class="lbg"/>')
        texts.append(f'<text x="{_f(tx)}" y="{_f(ty)}" class="cn{" ca" if alert else ""}" '
                     f'text-anchor="{anchor}">{escape(name)}</text>')
        texts.append(f'<text x="{_f(tx)}" y="{_f(ty + 16)}" class="cv" text-anchor="{anchor}">{escape(value)}</text>')
    # Height follows the callout rows: taller when they need more, trimmed when fewer.
    total_h = math.ceil(bottom + 12) if calls else math.ceil(T + ph + 52)

    title = "Perfil del río Uruguay hoy: cada estación comparada con su propia historia"
    desc = (
        f"Percentil de hoy en cada estación con dato, de aguas arriba a aguas abajo, en una escala de "
        f"{p_min} a 100: "
        + "; ".join(f"{st.name} {_floor_pct(ss.percentile_today)}" for _, ss, st in pts)
        + "."
    ) if pts else "Ninguna estación del río tiene dato al día."
    cls = "perfil" if big else "perfil movil"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="{cls}" viewBox="0 0 {width} {total_h}" '
        f'role="img" aria-label="{escape(title)}" font-family="{FONT}">'
        f"<title>{escape(title)}</title><desc>{escape(desc)}</desc>"
        + "".join(o + leaders + boxes + texts)
        + "</svg>"
    )


# ---------------------------------------------------------------- gauge

def _has_cota(station: Station) -> bool:
    return "ina_level" in station.sources


def gauge_svg(status: StationStatus, station: Station) -> str:
    """Staff gauge in the station's own meters, with alert and evacuation lines when official ones exist."""
    level = None if status.stale else status.level_m
    cotas = []
    if _has_cota(station):
        if station.alert_m is not None:
            cotas.append(("alerta", station.alert_m, ALERT_LINE, "cota alert"))
        if station.evac_m is not None:
            cotas.append(("evacuación", station.evac_m, EVAC, "cota evac"))

    marks = [v for v in [level] + [c[1] for c in cotas] if v is not None]
    hi = max(marks) if marks else 10.0
    top = math.ceil(hi + 0.5)
    bottom = 0 if top <= 14 else top - 12
    if level is not None:
        # The reading is always on the plate, even far below high cotas.
        bottom = min(bottom, max(0, math.floor(level) - 1))
    left, right = 8, GAUGE_W - 8
    plate_y, plate_h = 30, 28

    def x(v: float) -> float:
        return left + (right - left) * (v - bottom) / (top - bottom)

    span = top - bottom
    per_m = (right - left) / span
    body: list[str] = [
        f'<rect x="{left}" y="{plate_y}" width="{right - left}" height="{plate_h}" fill="#FFFFFF" '
        f'stroke="{GRID}" vector-effect="non-scaling-stroke"/>'
    ]
    if level is not None:
        lx = x(max(bottom, min(top, level)))
        body.append(
            f'<rect class="water" x="{left}" y="{plate_y}" width="{_f(lx - left)}" height="{plate_h}" '
            f'fill="{SILT}" fill-opacity="0.45"/>'
        )
    minor = 0.1 if span <= 6 else 0.5
    steps = int(round(span / minor))
    for k in range(steps + 1):
        v = bottom + k * minor
        is_m = abs(v - round(v)) < 1e-6
        is_half = abs(v * 2 - round(v * 2)) < 1e-6
        h = 14 if is_m else (9 if is_half else 5)
        body.append(_line(x(v), plate_y, x(v), plate_y + h, INK, 1.25 if is_m else 1))
    label_every = 1 if per_m >= 24 else 2
    # Numbers a cota or level line would cross are left out.
    lines_x = [x(c[1]) for c in cotas] + ([x(level)] if level is not None else [])
    for v in range(bottom, top + 1):
        if (v - bottom) % label_every or v == top:
            continue
        x0, x1 = x(v) + 1, x(v) + 4 + _text_width(str(v), GAUGE_FONT)
        if not any(x0 <= lx <= x1 for lx in lines_x):
            body.append(_text(x(v) + 3, plate_y + plate_h - 4, str(v), fill=INK))

    if level is not None:
        lx = x(max(bottom, min(top, level)))
        body.append(_line(lx, plate_y - 4, lx, plate_y + plate_h, INK, 2.5, cls="level"))
        label = _m(level)
        w = _text_width(label, GAUGE_FONT + 2)
        tx = min(max(lx, left + w / 2), right - w / 2)
        body.append(f'<text x="{_f(tx)}" y="22" text-anchor="middle" font-weight="600" font-size="{GAUGE_FONT + 2}">'
                    f"{escape(label)}</text>")
    else:
        body.append(_text(left, 22, "sin dato actual", fill=INK_2))

    # Alert label to the left of its line, evacuation to the right; a second
    # row when they would still touch.
    row_y = plate_y + plate_h + 22
    prev_end = -math.inf
    rows_used = 0
    for name, v, color, cls in cotas:
        cx = x(v)
        label = f"{name} {_m(v)}"
        w = _text_width(label, GAUGE_FONT)
        if name == "alerta":
            start = min(max(cx - w, left), right - w)
        else:
            start = max(min(cx, right - w), left)
        row = 1 if start < prev_end + 8 else 0
        rows_used = max(rows_used, row + 1)
        ty = row_y + row * 18
        body.append(_line(cx, plate_y, cx, ty - 13, color, 2.5, dash=EVAC_DASH if name != "alerta" else None,
                          cls=cls))
        body.append(_text(start, ty, label, fill=INK))
        prev_end = start + w

    if level is not None:
        reading = f"Nivel actual: {_m(level)} en la escala de {station.name}."
    elif status.stale and status.stale_since:
        reading = f"Sin dato actual: desactualizada desde el {_day(status.stale_since)}."
    else:
        reading = "Sin dato actual."
    cota_txt = " ".join(f"Cota de {name}: {_m(v)}." for name, v, _, _ in cotas)
    if cotas and station.cota_source:
        cota_txt += f" Fuente de las cotas: {station.cota_source}."
    elif not cotas:
        cota_txt = "Esta estación no tiene cotas oficiales."
    desc = f"{reading} {cota_txt} Escala de {bottom} a {top} m."
    height = plate_y + plate_h + 8 + (rows_used * 18 + 4 if cotas else 0)
    return _svg("chart-gauge", GAUGE_W, height, f"Escala de {station.name}", desc, body, font_size=GAUGE_FONT)


# ---------------------------------------------------------------- sparkline

SPARK_W, SPARK_H = 120, 32


def sparkline_svg(daily: dict[date, float], days: int = 30) -> str:
    """Last `days` daily means ending on the newest day; missing days break the line."""
    if not daily:
        return _svg("chart-spark", SPARK_W, SPARK_H, f"Últimos {days} días", "Sin datos.", [])
    end = max(daily)
    start = end - timedelta(days=days - 1)
    series = [daily.get(start + timedelta(days=k)) for k in range(days)]
    vals = [v for v in series if v is not None]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        lo, hi = lo - 1, hi + 1

    def x(k: int) -> float:
        return 2 + (SPARK_W - 6) * k / max(days - 1, 1)

    def y(v: float) -> float:
        return SPARK_H - 3 - (SPARK_H - 6) * (v - lo) / (hi - lo)

    runs = [[(x(k), y(series[k])) for k in run] for run in _runs(series)]
    last_k = max(k for k, v in enumerate(series) if v is not None)
    body = [
        _path(_path_d(runs), INK_2, 1.5),
        f'<circle cx="{_f(x(last_k))}" cy="{_f(y(series[last_k]))}" r="2.5" fill="{SILT}"/>',
    ]
    desc = (
        f"Media diaria del {_day(start)} al {_day(end)}: mínimo {_num(min(vals))}, "
        f"máximo {_num(max(vals))}, último {_num(series[last_k])}."
    )
    return _svg("chart-spark", SPARK_W, SPARK_H, f"Últimos {days} días", desc, body)


# ---------------------------------------------------------------- forecast band

FC_H = 380
F_LEFT, F_RIGHT, F_TOP, F_BOTTOM = 64, 584, 96, 316
OBS_DAYS_BEFORE = 14
_BANDS = {
    "ina": ("main", [("inferior", "superior", 0.3)]),
    # GloFAS: the central half of the ensemble (p25 to p75); min and max stay out.
    "glofas": ("median", [("p25", "p75", 0.3)]),
}


def forecast_band_svg(obs_daily: dict[date, float], forecast: list[Forecast]) -> str:
    """Observed daily means before the latest run, then that run's forecast with its range.

    Forecast values are averaged per local day of valid time, as in the
    verification, so the chart and the verification read the same numbers.
    """
    title = "Pronóstico y observado"
    if not forecast:
        days = sorted(obs_daily)[-OBS_DAYS_BEFORE:]
        desc = "No hay pronóstico disponible."
        if days:
            desc += f" Último observado: {_num(obs_daily[days[-1]])} el {_day(days[-1])}."
        return _svg("chart-wide chart-forecast", WIDE_W, FC_H, title, desc,
                    [_text(WIDE_W / 2, FC_H / 2, "sin pronóstico disponible", anchor="middle", fill=INK_2)])

    issued = max(f.issued_utc for f in forecast)
    run = [f for f in forecast if f.issued_utc == issued]
    issuer, variable = run[0].issuer, run[0].variable
    unit = UNITS.get(variable, "")
    main_q, bands = _BANDS.get(issuer, ("main", []))
    issue_day = local_day(issued)

    cells: dict[str, dict[date, list[float]]] = defaultdict(lambda: defaultdict(list))
    for f in run:
        cells[f.qualifier][local_day(f.valid_utc)].append(f.value)
    per_q = {q: {d: mean(vs) for d, vs in by_day.items()} for q, by_day in cells.items()}
    fc_days = sorted(per_q.get(main_q, {}))
    last_fc = max(fc_days) if fc_days else issue_day

    first = issue_day - timedelta(days=OBS_DAYS_BEFORE)
    obs = {d: v for d, v in obs_daily.items() if first <= d <= last_fc}
    shown_q = [main_q] + [q for band in bands for q in band[:2]]
    all_vals = list(obs.values()) + [v for q in shown_q for v in per_q.get(q, {}).values()]
    lo, hi, ticks = _ticks(min(all_vals), max(all_vals), 4)
    total = (last_fc - first).days or 1

    def x(d: date) -> float:
        return F_LEFT + (F_RIGHT - F_LEFT) * ((d - first).days + 0.5) / (total + 1)

    def y(v: float) -> float:
        return F_BOTTOM - (F_BOTTOM - F_TOP) * (v - lo) / (hi - lo)

    body: list[str] = []
    band_items = [(_sample_band(SILT, 0.3), "rango del pronóstico")] if bands else []
    body += _legend([(_sample_line(INK, 2), "observado"), (_sample_line(SILT, 3), "pronóstico")] + band_items, y=26)
    axis_name = {"level": "nivel", "discharge": "caudal"}.get(variable, variable)
    body.append(_text(0, 66, f"{axis_name} ({unit}), media diaria", fill=INK_2))

    step = ticks[1] - ticks[0] if len(ticks) > 1 else 1
    for t in ticks:
        body.append(_line(F_LEFT, y(t), F_RIGHT, y(t), GRID, 1))
        body.append(_text(F_LEFT - 8, y(t) + 7, _tick_label(t, step), anchor="end", fill=INK_2))

    ix = x(issue_day)
    body.append(_line(ix, F_TOP - 6, ix, F_BOTTOM, INK_2, 1, dash="3 3"))
    # GloFAS has no run time: its stamp is when it was downloaded.
    issued_word = "bajado" if issuer == "glofas" else "emitido"
    body.append(_text(ix + 6, F_BOTTOM + 30, f"{issued_word} el {_day_short(issue_day)}", fill=INK_2))
    body.append(_text(F_LEFT, F_BOTTOM + 30, _day_short(first), fill=INK_2))
    body.append(_text(F_RIGHT, F_BOTTOM + 30, _day_short(last_fc), anchor="end", fill=INK_2))

    for lo_q, hi_q, opacity in bands:
        lows, highs = per_q.get(lo_q, {}), per_q.get(hi_q, {})
        ds = [d for d in fc_days if d in lows and d in highs]
        if ds:
            pts = [f"{_f(x(d))},{_f(y(highs[d]))}" for d in ds] + [f"{_f(x(d))},{_f(y(lows[d]))}" for d in reversed(ds)]
            body.append(f'<polygon points="{" ".join(pts)}" fill="{SILT}" fill-opacity="{opacity}"/>')

    obs_days = sorted(obs)
    if obs_days:
        series = [obs.get(first + timedelta(days=k)) for k in range(total + 1)]
        runs = [[(x(first + timedelta(days=k)), y(series[k])) for k in r] for r in _runs(series)]
        body.append(_path(_path_d(runs), INK, 2, cls="observed"))
    main = per_q.get(main_q, {})
    if fc_days:
        body.append(_path(_path_d([[(x(d), y(main[d])) for d in fc_days]]), SILT, 3, cls="forecast"))

    issuer_name = {"ina": "INA", "glofas": "GloFAS"}.get(issuer, issuer)
    fc_txt = "; ".join(
        f"{_day_short(d)}: {_num(main[d])}"
        + (f" (entre {_num(per_q[bands[-1][0]][d])} y {_num(per_q[bands[-1][1]][d])})"
           if bands and d in per_q.get(bands[-1][0], {}) and d in per_q.get(bands[-1][1], {}) else "")
        for d in fc_days
    )
    desc = f"Pronóstico {issuer_name} {issued_word} el {_day(issue_day)}, {axis_name} en {unit}. "
    if obs_days:
        desc += f"Último observado: {_num(obs[obs_days[-1]])} el {_day(obs_days[-1])}. "
    desc += f"Pronóstico: {fc_txt}." if fc_txt else "El pronóstico no trae valores."
    return _svg("chart-wide chart-forecast", WIDE_W, FC_H, title, desc, body)


# ---------------------------------------------------------------- seasons

SEASON_H = 420
S_LEFT, S_RIGHT, S_TOP, S_BOTTOM = 52, 530, 72, 352
SEASON_DAYS = 182
_MONTH_OFFSETS = [(0, "oct"), (31, "nov"), (61, "dic"), (92, "ene"), (123, "feb"), (151, "mar")]
# (colour, dash, width) for past seasons, in key order. Each also gets its year
# written at the line end, so they never depend on colour alone.
_PAST_STYLES = [(LOW, None, 2.25), (INK_2, "9 5", 2), (INK, "2 4", 1.75), (LOW, "12 4 2 4", 1.5)]
MAX_PAST_SEASONS = len(_PAST_STYLES)


def _season_start(name: str) -> date | None:
    try:
        return date(int(name.split("-", 1)[0]), 10, 1)
    except ValueError:
        return None


def _join_es(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


def _spread(ys: list[float], gap: float, lo: float, hi: float) -> list[float]:
    """Nudge label baselines (in input order) so no two are closer than `gap`, inside [lo, hi]."""
    order = sorted(range(len(ys)), key=lambda k: ys[k])
    out = list(ys)
    prev = -math.inf
    for k in order:
        out[k] = max(out[k], prev + gap, lo)
        prev = out[k]
    overflow = prev - hi
    if overflow > 0:
        for k in order:
            out[k] -= overflow
    return out


def season_compare_svg(series_by_season: dict[str, list[tuple[int, float]]], alert: float | None,
                       unit: str = "m") -> str:
    """One line per season from Oct 1 (day 0) to Mar 31; the newest season is drawn in sediment.

    Supports up to MAX_PAST_SEASONS seasons besides the newest one and raises
    ValueError beyond that, since each past season needs its own line style.
    """
    seasons = {k: v for k, v in sorted(series_by_season.items()) if v}
    if not seasons:
        return _svg("chart-wide chart-seasons", WIDE_W, SEASON_H, "Temporadas de crecida", "Sin datos de temporadas.",
                    [_text(WIDE_W / 2, SEASON_H / 2, "sin datos", anchor="middle", fill=INK_2)])
    current = max(seasons)
    past = [k for k in seasons if k != current]
    if len(past) > MAX_PAST_SEASONS:
        raise ValueError(f"season_compare_svg supports up to {MAX_PAST_SEASONS} past seasons, got {len(past)}")
    years = [k.split("-", 1)[0] for k in past]
    title = f"Esta temporada contra las crecidas de {_join_es(years)}" if past else f"Temporada {current}"
    vals = [v for s in seasons.values() for _, v in s] + ([alert] if alert is not None else [])
    lo, hi, ticks = _ticks(min(vals), max(vals), 4)

    def x(day: int) -> float:
        return S_LEFT + (S_RIGHT - S_LEFT) * day / SEASON_DAYS

    def y(v: float) -> float:
        return S_BOTTOM - (S_BOTTOM - S_TOP) * (v - lo) / (hi - lo)

    body: list[str] = []
    body.append(_text(0, 40, f"nivel diario ({unit})" if unit == "m" else f"media diaria ({unit})", fill=INK_2))
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 1
    for t in ticks:
        body.append(_line(S_LEFT, y(t), S_RIGHT, y(t), GRID, 1))
        body.append(_text(S_LEFT - 8, y(t) + 7, _tick_label(t, step), anchor="end", fill=INK_2))
    for off, name in _MONTH_OFFSETS:
        body.append(_line(x(off), S_BOTTOM, x(off), S_BOTTOM + 7, INK_2, 1))
        body.append(_text(x(off) + 4, S_BOTTOM + 30, name, fill=INK_2))
    if alert is not None:
        body.append(_line(S_LEFT, y(alert), S_RIGHT, y(alert), ALERT_LINE, 2, cls="cota alert"))
        body.append(_text(S_RIGHT, y(alert) - 8, f"alerta {_m(alert)}", anchor="end", extra=HALO))

    end_labels: list[tuple[float, str, str]] = []
    for k, year, (color, dash, width) in zip(past, years, _PAST_STYLES):
        s = seasons[k]
        runs = [[(x(d), y(v)) for d, v in run] for run in _consecutive(s)]
        body.append(_path(_path_d(runs), color, width, dash=dash, cls="season"))
        end_labels.append((y(max(s)[1]), year, color))
    cur = seasons[current]
    body.append(_path(_path_d([[(x(d), y(v)) for d, v in run] for run in _consecutive(cur)]), SILT, 3.5,
                      cls="season current"))
    last_day, last_v = max(cur)
    # Right of the last point, unless that would run into the year column at
    # the right edge late in the season; then left of it, raised off the line.
    if x(last_day) + 8 + _text_width(current, WIDE_FONT) > S_RIGHT:
        body.append(_text(x(last_day) - 8, y(last_v) - 10, current, anchor="end", weight=600, extra=HALO,
                          cls="current-label"))
    else:
        body.append(_text(x(last_day) + 8, y(last_v) + 7, current, weight=600, extra=HALO, cls="current-label"))

    spread = _spread([ly + 7 for ly, _, _ in end_labels], LINE_GAP - 4, S_TOP, S_BOTTOM + 7)
    for ty, (_, year, color) in zip(spread, end_labels):
        body.append(_text(S_RIGHT + 8, ty, year, fill=color, weight=600))

    parts = []
    for k, s in seasons.items():
        peak_day, peak = max(s, key=lambda t: (t[1], -t[0]))
        start = _season_start(k)
        when = f" el {_day(start + timedelta(days=peak_day))}" if start else ""
        parts.append(f"{k}: máximo {_num(peak)} {unit}{when}")
    desc = (
        "Media diaria desde el 1 de octubre hasta el 31 de marzo, por temporada. " + "; ".join(parts) + "."
        + (f" Cota de alerta: {_m(alert)}." if alert is not None else "")
    )
    return _svg("chart-wide chart-seasons", WIDE_W, SEASON_H, title, desc, body)


def _consecutive(series: list[tuple[int, float]]) -> list[list[tuple[int, float]]]:
    runs: list[list[tuple[int, float]]] = []
    for d, v in sorted(series):
        if runs and d == runs[-1][-1][0] + 1:
            runs[-1].append((d, v))
        else:
            runs.append([(d, v)])
    return runs


# ---------------------------------------------------------------- lag

LAG_H = 330
L_LEFT, L_RIGHT, L_TOP, L_BOTTOM = 20, 580, 104, 268
LAG_MAX_DAYS = 15


def lag_svg(result: LagResult, upstream_name: str, downstream_name: str) -> str:
    """Dot strip of how many days each past flood peak took to travel downstream.

    Display names come from the caller (the result only carries station ids).
    """
    up, down = upstream_name, downstream_name
    variable = {"level": "nivel", "discharge": "caudal"}.get(result.upstream.partition(":")[2], "")
    title = f"Lo que tardó el pico en llegar de {up} a {down}"
    events = sorted((date.fromisoformat(k), v) for k, v in result.per_event.items())
    if not events:
        desc = f"No hay crecidas para medir el rezago entre {up} y {down} ({variable}). {result.reason}"
        return _svg("chart-wide chart-lag", WIDE_W, LAG_H, title, desc,
                    [_text(WIDE_W / 2, LAG_H / 2, "sin crecidas para medir", anchor="middle", fill=INK_2)])

    def x(days: float) -> float:
        return L_LEFT + (L_RIGHT - L_LEFT) * days / LAG_MAX_DAYS

    counts: dict[int, int] = defaultdict(int)
    tallest = max(sum(1 for _, v in events if int(round(v)) == k) for k in {int(round(v)) for _, v in events})
    gap = min(17.0, (L_BOTTOM - L_TOP - 10) / max(tallest, 1))
    r = min(7.0, gap / 2 - 0.5)

    body: list[str] = []
    body.append(_text(0, 26, f"días entre el pico en {up} y el pico en {down}", fill=INK_2))
    body.append(_text(0, 26 + LINE_GAP, "cada punto es una crecida pasada", fill=INK_2))
    body.append(_line(L_LEFT, L_BOTTOM, L_RIGHT, L_BOTTOM, INK_2, 1))
    for k in range(0, LAG_MAX_DAYS + 1):
        body.append(_line(x(k), L_BOTTOM, x(k), L_BOTTOM + (8 if k % 5 == 0 else 4), INK_2, 1))
        body.append(_text(x(k), L_BOTTOM + 32, str(k), anchor="middle", fill=INK_2))

    if result.lag_days_median is not None:
        mx = x(result.lag_days_median)
        body.append(_line(mx, L_TOP - 12, mx, L_BOTTOM, INK, 1.5, dash="4 3", cls="median"))
        body.append(_text(mx + 6, L_TOP - 14, f"mediana: {_days(result.lag_days_median)}", weight=600))

    for d, v in events:
        k = int(round(v))
        cy = L_BOTTOM - 2 - r - counts[k] * gap
        counts[k] += 1
        body.append(f'<circle cx="{_f(x(k))}" cy="{_f(cy)}" r="{_f(r)}" fill="{LOW}"/>')

    lo_hi = result.lag_days_range
    span = f", entre {_days(lo_hi[0])} y {_days(lo_hi[1])}" if lo_hi else ""
    median = f"mediana {_days(result.lag_days_median)}" if result.lag_days_median is not None else "sin mediana"
    listing = "; ".join(f"{_day(d)}: {_days(v)}" for d, v in events)
    desc = (
        f"Rezago del pico de {variable} de {up} a {down} en {len(events)} crecidas: {median}{span}. "
        f"Por crecida, según la fecha del pico aguas abajo: {listing}. {result.reason}"
    )
    return _svg("chart-wide chart-lag", WIDE_W, LAG_H, title, desc, body)
