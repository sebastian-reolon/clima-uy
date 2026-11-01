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
from functools import lru_cache
from statistics import mean
from typing import Iterable, Sequence
from xml.sax.saxutils import escape

from ..analysis.lag import LagResult
from ..analysis.status import StationStatus
from ..catalog import Station, load_catalog
from ..store import Forecast
from ..timeutil import local_day

INK = "#1B2A33"
INK_2 = "#4F5E66"
GRID = "#C9D3CC"
SILT = "#9C7A4D"
ALERT = "#E3A21A"
EVAC = "#C62828"
LOW = "#3E6B7A"

FONT = "Barlow Condensed, Arial Narrow, sans-serif"
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


def _num(v: float, decimals: int | None = None) -> str:
    """Uruguayan number: decimal comma, dot for thousands."""
    if decimals is None:
        decimals = 0 if abs(v) >= 100 else 2
    s = f"{v:,.{decimals}f}"
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _m(v: float) -> str:
    """Meters with as many decimals as the value needs (7,5 m; 11 m; 7,66 m)."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",") + " m"


def _pct(p: float) -> str:
    return str(int(round(p)))


def _day(d: date) -> str:
    return f"{d.day}/{d.month}/{d.year}"


def _day_short(d: date) -> str:
    return f"{d.day}/{d.month}"


def _days(n: float) -> str:
    n = int(round(n))
    return f"{n} día" if n == 1 else f"{n} días"


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
          weight: int | None = None, cls: str | None = None) -> str:
    attrs = f'x="{_f(x)}" y="{_f(y)}"'
    if anchor != "start":
        attrs += f' text-anchor="{anchor}"'
    if fill:
        attrs += f' fill="{fill}"'
    if weight:
        attrs += f' font-weight="{weight}"'
    if cls:
        attrs += f' class="{cls}"'
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
    if abs(v) >= 1000 or step >= 1:
        return _num(v, 0)
    return _num(v, 1 if step * 10 == int(step * 10) else 2)


def _text_width(s: str, size: float) -> float:
    """Rough advance width in Barlow Condensed, enough to keep labels apart."""
    return len(s) * size * 0.43


def _legend(items: Sequence[tuple[str, str]], y: float, x: float = 0) -> list[str]:
    """Legend row: (svg sample markup with {x}, label)."""
    out = []
    for sample, label in items:
        out.append(sample.format(x0=_f(x), x1=_f(x + 26), xm=_f(x + 13), y=_f(y - 7)))
        out.append(_text(x + 34, y, label, fill=INK_2))
        x += 34 + _text_width(label, WIDE_FONT) + 26
    return out


def _sample_line(color: str, width: float, dash: str | None = None) -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        '<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" '
        f'stroke="{color}" stroke-width="{_f(width)}" stroke-linecap="round" '
        f'vector-effect="non-scaling-stroke"{dash_attr}/>'
    )


def _sample_alert_tick() -> str:
    return (
        f'<line x1="{{x0}}" y1="{{y}}" x2="{{x1}}" y2="{{y}}" stroke="{ALERT}" stroke-width="3" '
        'stroke-dasharray="10 6" vector-effect="non-scaling-stroke"/>'
    )


def _sample_band(color: str, opacity: float) -> str:
    return f'<rect x="{{x0}}" y="{{y}}" width="26" height="12" transform="translate(0 -6)" fill="{color}" fill-opacity="{opacity}"/>'


# ---------------------------------------------------------------- profile

P_LEFT, P_RIGHT = 46, 584
P_TOP, P_BOTTOM = 92, 400
DOTTED = "0 7"  # with round caps: a dotted line


def _city_label(station: Station, status: StationStatus | None) -> tuple[str, str]:
    """(city, how it is measured) for a station that represents a Uruguayan city."""
    city = station.represents or station.name
    if status is None or status.percentile_today is None:
        if status is not None and status.stale and status.stale_since:
            return city, f", desactualizado desde el {_day_short(status.stale_since)}"
        return city, ", sin dato hoy"
    if city == station.name:
        return city, ""
    return city, f", medido en {station.name}"


def _profile_line(station: Station, status: StationStatus | None) -> str:
    name = f"{station.represents} (medido en {station.name})" if station.represents and station.represents != station.name else station.name
    if status is None:
        return f"{name}: sin dato"
    if status.stale:
        since = f" desde el {_day(status.stale_since)}" if status.stale_since else ""
        return f"{name}: desactualizada{since}"
    if status.percentile_today is None:
        return f"{name}: sin percentil"
    parts = [f"hoy {_pct(status.percentile_today)}"]
    if status.percentile_3d_ago is not None:
        parts.append(f"hace 3 días {_pct(status.percentile_3d_ago)}")
    if status.alert_percentile is not None:
        parts.append(f"alerta en {_pct(status.alert_percentile)}")
    return f"{name}: " + ", ".join(parts)


def profile_svg(statuses: Sequence[StationStatus], catalog: Sequence[Station]) -> str:
    """Long profile of the main river: today's percentile per station, upstream to downstream.

    Stale stations and stations without a percentile break the line. The
    today path carries `data-d0`, the same path with each point at its value
    of 3 days earlier (or today's when that is missing), for morph.js.
    """
    by_id = {s.station_id: s for s in statuses}
    stations = sorted((s for s in catalog if s.tributary_of is None), key=lambda s: s.order)
    rows = [(st, by_id.get(st.id)) for st in stations]
    n = len(rows)

    def x(i: int) -> float:
        return P_LEFT + (P_RIGHT - P_LEFT) * (i / (n - 1) if n > 1 else 0.5)

    def y(p: float) -> float:
        return P_BOTTOM - (P_BOTTOM - P_TOP) * p / 100.0

    def fresh(ss: StationStatus | None, attr: str) -> float | None:
        if ss is None or ss.stale:
            return None
        return getattr(ss, attr)

    today = [fresh(ss, "percentile_today") for _, ss in rows]
    ago = [fresh(ss, "percentile_3d_ago") for _, ss in rows]

    body: list[str] = []
    body += _legend(
        [
            (_sample_line(SILT, 3.5), "hoy"),
            (_sample_line(LOW, 2.5, DOTTED), "hace 3 días"),
            (_sample_alert_tick(), "cota de alerta"),
        ],
        y=26,
    )
    body.append(_text(0, 66, "percentil de su propia historia desde 1990", fill=INK_2))

    # Grid and y axis.
    for p in (0, 25, 50, 75, 100):
        body.append(_line(P_LEFT, y(p), P_RIGHT, y(p), GRID, 1))
        body.append(_text(P_LEFT - 8, y(p) + 7, str(p), anchor="end", fill=INK_2))
    # Station ticks along the bottom, like a ruler.
    body.append('<g class="stations">')
    for i in range(n):
        body.append(_line(x(i), P_BOTTOM, x(i), P_BOTTOM + 7, INK_2, 1))
    body.append("</g>")

    # Uruguayan cities: a guide through the plot and a stepped label below it.
    cities = [(i, st, ss) for i, (st, ss) in enumerate(rows) if st.represents]
    label_y = P_BOTTOM + 42
    for row, (i, st, ss) in enumerate(cities):
        ly = label_y + row * LINE_GAP
        body.append(_line(x(i), P_TOP, x(i), ly - 18, GRID, 1, cls="city-guide"))
        city, how = _city_label(st, ss)
        body.append(
            f'<text x="{_f(x(i) + 4)}" y="{_f(ly)}" text-anchor="end">'
            f'<tspan font-weight="600">{escape(city)}</tspan>'
            f'<tspan fill="{INK_2}">{escape(how)}</tspan></text>'
        )
    # Direction hint on the last label row, where the stepped labels leave the left side free.
    body.append(_text(P_LEFT, label_y + max(len(cities) - 1, 0) * LINE_GAP, "aguas arriba", fill=INK_2))

    # Alert ticks under the lines.
    for i, (_, ss) in enumerate(rows):
        if ss is not None and ss.alert_percentile is not None:
            ay = y(ss.alert_percentile)
            body.append(_line(x(i) - 5, ay, x(i) + 5, ay, ALERT, 3, cls="alert-tick"))

    ago_runs = [[(x(i), y(ago[i])) for i in run] for run in _runs(ago)]
    if ago_runs:
        body.append(_path(_path_d(ago_runs), LOW, 2.5, dash=DOTTED, cls="ago"))

    today_idx = _runs(today)
    if today_idx:
        d = _path_d([[(x(i), y(today[i])) for i in run] for run in today_idx])
        d0 = _path_d([[(x(i), y(ago[i] if ago[i] is not None else today[i])) for i in run] for run in today_idx])
        body.append(_path(d, SILT, 3.5, cls="today", extra=f' data-d0="{d0}"'))
    else:
        body.append(_text((P_LEFT + P_RIGHT) / 2, y(50), "sin datos actuales", anchor="middle", fill=INK_2))

    desc = (
        "Qué tan alto está el río en cada estación, de aguas arriba a aguas abajo, como percentil "
        "de la historia diaria de esa estación desde 1990 (100 es el día más alto registrado). "
        + "; ".join(_profile_line(st, ss) for st, ss in rows)
        + "."
    )
    height = label_y + max(len(cities) - 1, 0) * LINE_GAP + 12
    return _svg("chart-wide chart-profile", WIDE_W, height,
                "Perfil del río Uruguay: percentil de hoy por estación", desc, body)


# ---------------------------------------------------------------- gauge

def _has_cota(station: Station) -> bool:
    return "ina_level" in station.sources


def gauge_svg(status: StationStatus, station: Station) -> str:
    """Staff gauge in the station's own meters, with alert and evacuation lines when official ones exist."""
    level = None if status.stale else status.level_m
    cotas = []
    if _has_cota(station):
        if station.alert_m is not None:
            cotas.append(("alerta", station.alert_m, ALERT, "cota alert"))
        if station.evac_m is not None:
            cotas.append(("evacuación", station.evac_m, EVAC, "cota evac"))

    marks = [v for v in [level] + [c[1] for c in cotas] if v is not None]
    hi = max(marks) if marks else 10.0
    top = math.ceil(hi + 0.5)
    bottom = 0 if top <= 14 else top - 12
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
            body.append(_text(x(v) + 3, plate_y + plate_h - 4, str(v), fill=INK_2))

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
        body.append(_line(cx, plate_y, cx, ty - 13, color, 2.5, cls=cls))
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
    "glofas": ("median", [("min", "max", 0.14), ("p25", "p75", 0.3)]),
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
    all_vals = list(obs.values()) + [v for q in per_q.values() for v in q.values()]
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
    body.append(_text(ix + 6, F_BOTTOM + 30, f"emitido el {_day_short(issue_day)}", fill=INK_2))
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
    desc = f"Pronóstico {issuer_name} emitido el {_day(issue_day)}, {axis_name} en {unit}. "
    if obs_days:
        desc += f"Último observado: {_num(obs[obs_days[-1]])} el {_day(obs_days[-1])}. "
    desc += f"Pronóstico: {fc_txt}." if fc_txt else "El pronóstico no trae valores."
    return _svg("chart-wide chart-forecast", WIDE_W, FC_H, title, desc, body)


# ---------------------------------------------------------------- seasons

SEASON_H = 420
S_LEFT, S_RIGHT, S_TOP, S_BOTTOM = 52, 584, 92, 352
SEASON_DAYS = 182
_MONTH_OFFSETS = [(0, "oct"), (31, "nov"), (61, "dic"), (92, "ene"), (123, "feb"), (151, "mar")]
_PAST_STYLES = [(LOW, None), (INK_2, "8 5"), (INK, "2 5")]


def _season_start(name: str) -> date | None:
    try:
        return date(int(name.split("-", 1)[0]), 10, 1)
    except ValueError:
        return None


def season_compare_svg(series_by_season: dict[str, list[tuple[int, float]]], alert: float | None,
                       unit: str = "m") -> str:
    """One line per season from Oct 1 (day 0) to Mar 31; the newest season is drawn in sediment."""
    title = "Esta temporada contra las crecidas de 2009, 2015 y 2023"
    seasons = {k: v for k, v in sorted(series_by_season.items()) if v}
    if not seasons:
        return _svg("chart-wide chart-seasons", WIDE_W, SEASON_H, title, "Sin datos de temporadas.",
                    [_text(WIDE_W / 2, SEASON_H / 2, "sin datos", anchor="middle", fill=INK_2)])
    current = max(seasons)
    vals = [v for s in seasons.values() for _, v in s] + ([alert] if alert is not None else [])
    lo, hi, ticks = _ticks(min(vals), max(vals), 4)

    def x(day: int) -> float:
        return S_LEFT + (S_RIGHT - S_LEFT) * day / SEASON_DAYS

    def y(v: float) -> float:
        return S_BOTTOM - (S_BOTTOM - S_TOP) * (v - lo) / (hi - lo)

    body: list[str] = []
    body.append(_text(0, 66, f"nivel diario ({unit})" if unit == "m" else f"media diaria ({unit})", fill=INK_2))
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 1
    for t in ticks:
        body.append(_line(S_LEFT, y(t), S_RIGHT, y(t), GRID, 1))
        body.append(_text(S_LEFT - 8, y(t) + 7, _tick_label(t, step), anchor="end", fill=INK_2))
    for off, name in _MONTH_OFFSETS:
        body.append(_line(x(off), S_BOTTOM, x(off), S_BOTTOM + 7, INK_2, 1))
        body.append(_text(x(off) + 4, S_BOTTOM + 30, name, fill=INK_2))
    if alert is not None:
        body.append(_line(S_LEFT, y(alert), S_RIGHT, y(alert), ALERT, 2.5, cls="cota alert"))
        body.append(_text(S_RIGHT, y(alert) - 8, f"alerta {_m(alert)}", anchor="end"))

    legend: list[tuple[str, str]] = []
    labels: list[tuple[float, float, str, str, int]] = []
    past = [k for k in seasons if k != current]
    for k, (color, dash) in zip(past, _PAST_STYLES):
        s = seasons[k]
        runs = [[(x(d), y(v)) for d, v in run] for run in _consecutive(s)]
        body.append(_path(_path_d(runs), color, 1.75, dash=dash, cls="season"))
        legend.append((_sample_line(color, 1.75, dash), k))
    cur = seasons[current]
    body.append(_path(_path_d([[(x(d), y(v)) for d, v in run] for run in _consecutive(cur)]), SILT, 3.5, cls="season current"))
    legend.append((_sample_line(SILT, 3.5), current))
    body += _legend(legend, y=26)

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


@lru_cache(maxsize=1)
def _names() -> dict[str, str]:
    return {s.id: s.name for s in load_catalog()}


def _series_name(ref: str) -> tuple[str, str]:
    sid, _, variable = ref.partition(":")
    return _names().get(sid, sid), {"level": "nivel", "discharge": "caudal"}.get(variable, variable)


def lag_svg(result: LagResult) -> str:
    """Dot strip of how many days each past flood peak took to travel downstream."""
    up, variable = _series_name(result.upstream)
    down, _ = _series_name(result.downstream)
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
