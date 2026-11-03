"""SVG drawings for the city flood map: the «Plancheta» plan and the gauge staff.

Ported from the approved mockup (el-nino-uy/docs/mockups/gen_mapa.py). Only markup is
produced here: class names match the mockup and the CSS arrives with the page.

Pack coordinates are integer metres relative to the frame origin, y pointing north;
SVG y grows downward, so y_svg = (frame ymax - y) / unit.
"""
from __future__ import annotations

import math
from html import escape

from climauy.mapa.config import CityConfig, Mark
from climauy.mapa.pack import Pack

WIDE_UNIT_M = 10.0  # metres per SVG unit on the wide plan
PHONE_UNITS_WIDE = 480  # the phone crop is drawn about this many units wide
LABEL_GAP = 60  # minimum distance between contour labels, in SVG units
BARRIO_INSET = 60  # barrio labels stay this far from the left and right edges
STAFF_MERGE_M = 0.1  # marks closer than this share one staff label
STAFF_LABEL_GAP = 13  # minimum distance between staff label baselines

Frame = tuple[float, float, float, float]


def _fmt(v: float, nd: int = 2) -> str:
    return f"{v:.{nd}f}".replace(".", ",")


def _bbox_outside(flat: list, frame: Frame) -> bool:
    xs, ys = flat[0::2], flat[1::2]
    return max(xs) < frame[0] or min(xs) > frame[2] or max(ys) < frame[1] or min(ys) > frame[3]


def _points(flat: list, frame: Frame, unit: float) -> list[str]:
    xmin, _, _, ymax = frame
    out: list[str] = []
    for i in range(0, len(flat) - 1, 2):
        p = f"{(flat[i] - xmin) / unit:.1f},{(ymax - flat[i + 1]) / unit:.1f}"
        if not out or p != out[-1]:
            out.append(p)
    return out


def ring_path(rings: list, frame: Frame, unit: float) -> str:
    """Closed subpaths for flat [x, y, ...] rings; draw with fill-rule evenodd.
    Rings fully outside the frame and degenerate rings are skipped."""
    parts = []
    for r in rings:
        if len(r) < 6 or _bbox_outside(r, frame):
            continue
        pts = _points(r, frame, unit)
        if len(pts) > 2:
            parts.append("M" + "L".join(pts) + "Z")
    return "".join(parts)


def line_path(lines: list, frame: Frame, unit: float) -> str:
    parts = []
    for ln in lines:
        if len(ln) < 4 or _bbox_outside(ln, frame):
            continue
        pts = _points(ln, frame, unit)
        if len(pts) > 1:
            parts.append("M" + "L".join(pts))
    return "".join(parts)


def _polyline_point(flat: list, frac: float) -> tuple[float, float]:
    segs = [(flat[i], flat[i + 1], flat[i + 2], flat[i + 3]) for i in range(0, len(flat) - 3, 2)]
    lengths = [math.hypot(x2 - x1, y2 - y1) for x1, y1, x2, y2 in segs]
    target = sum(lengths) * frac
    for (x1, y1, x2, y2), ln in zip(segs, lengths):
        if target <= ln and ln > 0:
            t = target / ln
            return x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
        target -= ln
    return flat[-2], flat[-1]


def _length(flat: list) -> float:
    return sum(math.hypot(flat[i + 2] - flat[i], flat[i + 3] - flat[i + 1]) for i in range(0, len(flat) - 3, 2))


def _contour_labels(contours: list, frame: Frame, unit: float, w: float, h: float) -> list[str]:
    placed: list[tuple[float, float]] = []
    out = []
    for c in contours:
        lines = [ln for ln in c["lines"] if len(ln) >= 4]
        if not lines:
            continue
        longest = max(lines, key=_length)
        for frac in (0.35, 0.5, 0.65, 0.2, 0.8):
            x, y = _polyline_point(longest, frac)
            sx, sy = (x - frame[0]) / unit, (frame[3] - y) / unit
            if not (20 < sx < w - 20 and 20 < sy < h - 10):
                continue
            if all(math.hypot(sx - a, sy - b) >= LABEL_GAP for a, b in placed):
                placed.append((sx, sy))
                out.append(f'<text class="isol" x="{sx:.1f}" y="{sy:.1f}">{_fmt(c["m"], 0)} m</text>')
                break
    return out


def _place_labels(cfg: CityConfig, origin: tuple[float, float], frame: Frame, unit: float,
                  w: float, h: float) -> str:
    """Configured names at their UTM positions; those outside this variant's frame are skipped."""
    out = []
    for pl in cfg.place_labels:
        sx = (pl.x - origin[0] - frame[0]) / unit
        sy = (frame[3] - (pl.y - origin[1])) / unit
        if not (0 < sx < w and 0 < sy < h):
            continue
        if pl.kind == "rio":
            rot = f' transform="rotate({pl.rotate:g} {sx:.1f} {sy:.1f})"' if pl.rotate else ""
            out.append(f'<text class="pl" x="{sx:.1f}" y="{sy:.1f}" text-anchor="middle"{rot}>{escape(pl.name)}</text>')
        else:
            out.append(f'<text class="pl2" x="{sx:.1f}" y="{sy:.1f}" text-anchor="middle">{escape(pl.name)}</text>')
    return "".join(out)


def variant_frame(pack: Pack, cfg: CityConfig, variant: str = "wide") -> tuple[Frame, float]:
    """(frame in pack coordinates, metres per SVG unit) of a plan variant: the whole pack
    frame for «wide», cfg.phone_frame_utm drawn about PHONE_UNITS_WIDE units wide for «movil»."""
    ox, oy = pack.frame[0], pack.frame[1]
    if variant == "movil":
        px0, py0, px1, py1 = cfg.phone_frame_utm
        return (px0 - ox, py0 - oy, px1 - ox, py1 - oy), (px1 - px0) / PHONE_UNITS_WIDE
    return (0.0, 0.0, float(pack.frame[2] - ox), float(pack.frame[3] - oy)), WIDE_UNIT_M


def same_drawing(pack: Pack, cfg: CityConfig) -> bool:
    """True when the phone plan maps metres to SVG units exactly as the wide one (same left
    edge, top edge and unit), so one level path serves both drawings."""
    (wf, wu), (mf, mu) = variant_frame(pack, cfg, "wide"), variant_frame(pack, cfg, "movil")
    return (wf[0], wf[3], wu) == (mf[0], mf[3], mu)


def level_path(pack: Pack, cfg: CityConfig, level: dict, variant: str = "wide") -> str:
    """SVG path of a level's area for a variant. When both variants share their drawing
    (same_drawing), the phone gets the wide path too: the page's data then needs one path."""
    if variant == "movil" and same_drawing(pack, cfg):
        variant = "wide"
    frame, unit = variant_frame(pack, cfg, variant)
    return ring_path(level.get("poly", []), frame, unit)


def plan_svg(pack: Pack, cfg: CityConfig, level: dict, gfm: list | None, ident: str,
             variant: str = "wide") -> str:
    """The city plan: blocks, streets, contours, the level's hatched area, river and the
    satellite's flood extent. `level` is a pack level; `gfm` is a list of rings in the
    same frame coordinates (or None). variant «movil» crops to cfg.phone_frame_utm."""
    ox, oy = pack.frame[0], pack.frame[1]
    frame, unit = variant_frame(pack, cfg, variant)
    w, h = (frame[2] - frame[0]) / unit, (frame[3] - frame[1]) / unit
    layers = pack.layers

    streets = []
    for name, cls in (("streets_minor", "st2"), ("streets_main", "st1")):
        d = line_path(layers.get(name, []), frame, unit)
        if d:
            streets.append(f'<path class="{cls}" d="{d}"/>')
    contour_d = "".join(
        f'<path class="iso" d="{d}"/>' for c in layers.get("contours", [])
        if (d := line_path(c["lines"], frame, unit)))
    clabels = _contour_labels(layers.get("contours", []), frame, unit, w, h)

    labs = []
    for lb in layers.get("labels", []):
        sx, sy = (lb["x"] - frame[0]) / unit, (frame[3] - lb["y"]) / unit
        if BARRIO_INSET < sx < w - BARRIO_INSET and 0 < sy < h:
            labs.append(f'<text class="bl" x="{sx:.1f}" y="{sy:.1f}">{escape(lb["name"])}</text>')

    km = 1000.0 / unit
    sb_x, sb_y = w - km - 50, h - 30
    deco = (f'<g class="deco"><line x1="{sb_x:g}" y1="{sb_y:g}" x2="{sb_x + km:g}" y2="{sb_y:g}"/>'
            f'<line x1="{sb_x:g}" y1="{sb_y - 5:g}" x2="{sb_x:g}" y2="{sb_y + 5:g}"/>'
            f'<line x1="{sb_x + km / 2:g}" y1="{sb_y - 3:g}" x2="{sb_x + km / 2:g}" y2="{sb_y + 3:g}"/>'
            f'<line x1="{sb_x + km:g}" y1="{sb_y - 5:g}" x2="{sb_x + km:g}" y2="{sb_y + 5:g}"/>'
            f'<text x="{sb_x + km / 2:g}" y="{sb_y - 10:g}" text-anchor="middle">1 km</text>'
            f'<line x1="{w - 40:g}" y1="70" x2="{w - 40:g}" y2="30"/>'
            f'<path d="M{w - 46:g},40L{w - 40:g},26L{w - 34:g},40Z"/>'
            f'<text x="{w - 40:g}" y="88" text-anchor="middle">N</text></g>')

    # ticks at every 1 km of absolute UTM, on all four edges
    ticks = []
    x_abs = math.ceil((frame[0] + ox) / 1000) * 1000
    while x_abs <= frame[2] + ox:
        sx = (x_abs - ox - frame[0]) / unit
        ticks.append(f'<line class="tk" x1="{sx:.1f}" y1="0" x2="{sx:.1f}" y2="8"/>'
                     f'<line class="tk" x1="{sx:.1f}" y1="{h:g}" x2="{sx:.1f}" y2="{h - 8:g}"/>')
        x_abs += 1000
    y_abs = math.ceil((frame[1] + oy) / 1000) * 1000
    while y_abs <= frame[3] + oy:
        sy = (frame[3] - (y_abs - oy)) / unit
        ticks.append(f'<line class="tk" x1="0" y1="{sy:.1f}" x2="8" y2="{sy:.1f}"/>'
                     f'<line class="tk" x1="{w:g}" y1="{sy:.1f}" x2="{w - 8:g}" y2="{sy:.1f}"/>')
        y_abs += 1000

    est_d = level_path(pack, cfg, level, variant)
    obs_d = ring_path(gfm or [], frame, unit)
    title = escape(f"Plano de {cfg.name} con el agua por debajo de la altura elegida")
    ev = 'fill-rule="evenodd"'
    return f'''<svg class="mapa{" movil" if variant == "movil" else ""}" id="{ident}" viewBox="0 0 {w:.0f} {h:.0f}" role="img" aria-labelledby="{ident}-t">
<title id="{ident}-t">{title}</title>
<defs><pattern id="{ident}-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" class="hl"/></pattern>
<pattern id="{ident}-dots" width="7" height="7" patternUnits="userSpaceOnUse"><circle cx="1.5" cy="1.5" r=".8" class="dl"/></pattern></defs>
<rect class="paper" width="{w:.0f}" height="{h:.0f}"/>
<path class="blk" d="{ring_path(layers.get("blocks", []), frame, unit)}" {ev}/>
<g class="streets">{"".join(streets)}</g>
<g>{contour_d}</g>
<path class="est" d="{est_d}" fill="url(#{ident}-hatch)" {ev}/>
<path class="estl" d="{est_d}" {ev}/>
<path class="rio" d="{ring_path(layers.get("river", []), frame, unit)}" {ev}/>
<path class="obs" d="{obs_d}" {ev}/>
{"".join(clabels)}
{"".join(labs)}
{_place_labels(cfg, (ox, oy), frame, unit, w, h)}{deco}{"".join(ticks)}
<rect class="frame" width="{w:.0f}" height="{h:.0f}"/>
</svg>'''


def staff_groups(cfg: CityConfig, today: float | None) -> list[tuple[float, str, float]]:
    """(value, text, data-m) per staff label, ascending. The text carries the heights: today
    absorbs marks within 0.1 m («hoy, y alerta 4,38»); other marks within 0.1 m of each other
    share a label that keeps each one's height («2009 9,07 y 2015/16 9,12»), and data-m is the first."""
    lo, hi = cfg.range_m
    marks = sorted((mk for mk in cfg.marks if lo <= mk.m <= hi), key=lambda mk: mk.m)
    out: list[tuple[float, str, float]] = []
    if today is not None and lo <= today <= hi:
        near = [mk for mk in marks if abs(mk.m - today) <= STAFF_MERGE_M]
        marks = [mk for mk in marks if mk not in near]
        label = "hoy" + "".join(f", y {near[0].short}" if i == 0 else f" y {mk.short}"
                                for i, mk in enumerate(near))
        out.append((today, f"{label} {_fmt(today)}", today))
    groups: list[list[Mark]] = []
    for mk in marks:
        if groups and mk.m - groups[-1][0].m < STAFF_MERGE_M - 1e-9:
            groups[-1].append(mk)
        else:
            groups.append([mk])
    for g in groups:
        out.append((g[0].m, " y ".join(f"{mk.short} {_fmt(mk.m)}" for mk in g), g[0].m))
    return sorted(out, key=lambda t: t[0])


def staff_svg(cfg: CityConfig, today: float | None, cursor: float | None = None) -> str:
    """Vertical gauge staff over cfg.range_m: ticks every 0.25 m, whole-metre numbers,
    one leader and label per mark group. The `.cur` cursor starts at `cursor` (clamped to
    the range) so the staff reads without JavaScript; the page script moves it."""
    lo, hi = cfg.range_m
    top, bot, x = 20, 620, 70

    def y(m: float) -> float:
        return bot - (m - lo) / (hi - lo) * (bot - top)

    t = [f'<rect class="stf" x="{x - 10}" y="{top}" width="20" height="{bot - top}"/>']
    steps = int(math.floor((hi - lo) * 4 + 1e-6))
    for i in range(steps + 1):
        m = lo + i * 0.25
        whole = abs(m - round(m)) < 1e-6
        half = abs(m * 2 - round(m * 2)) < 1e-6
        ln = 20 if whole else (12 if half else 6)
        t.append(f'<line class="stt" x1="{x - 10}" y1="{y(m):.1f}" x2="{x - 10 + ln}" y2="{y(m):.1f}"/>')
        if whole:
            t.append(f'<text class="stn" x="{x - 16}" y="{y(m) + 4:.1f}" text-anchor="end">{int(round(m))}</text>')
    used: list[float] = []
    for v, label, data_m in staff_groups(cfg, today):
        ty = y(v) + 4
        while any(abs(ty - u) < STAFF_LABEL_GAP for u in used):
            ty -= STAFF_LABEL_GAP
        used.append(ty)
        cls = "mkt hoy" if label.startswith("hoy") else "mkt"
        t.append(f'<line class="mk" x1="{x + 10}" y1="{y(v):.1f}" x2="{x + 22}" y2="{ty - 4:.1f}"/>'
                 f'<text class="{cls}" x="{x + 26}" y="{ty:.1f}" data-m="{data_m:g}">{escape(label)}</text>')
    cur = "" if cursor is None else f' transform="translate(0,{y(min(max(cursor, lo), hi)):.1f})"'
    t.append(f'<g class="cur"{cur}><line x1="{x - 18}" x2="{x + 10}" y1="0" y2="0"/>'
             f'<path d="M{x - 26},-6L{x - 18},0L{x - 26},6Z"/></g>')
    aria = escape(f"Escala del río en {cfg.name}, de {lo:g} a {hi:g} metros")
    return (f'<svg class="staff" viewBox="0 0 215 640" role="img" aria-label="{aria}" '
            f'data-lo="{lo:g}" data-hi="{hi:g}" data-top="{top}" data-bot="{bot}">'
            f'{"".join(t)}<rect class="hit" x="0" y="{top}" width="215" height="{bot - top}"/></svg>')
