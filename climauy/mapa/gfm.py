"""Latest Copernicus GFM (Sentinel-1) flood patch over a city frame.

Runs at site-build time on a shared server every 3 hours, so it must never
fail the build: every problem comes back as GfmResult("error", ...). Needs the
`mapa` extra (rasterio, shapely, pyproj); without it the result is "error".

Polygons use the pack's coordinates: EPSG:32721 integer metres relative to the
frame origin, as a flat list of rings (outer rings and holes together, drawn
with fill-rule evenodd), each ring a flat [x, y, x, y, ...] int list.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, NamedTuple

log = logging.getLogger(__name__)

STAC_SEARCH_URL = "https://stac.eodc.eu/api/v1/search"
ASSET = "ensemble_flood_extent"
SEARCH_TIMEOUT_S = 50.0  # stac.eodc.eu search takes 15 to 54 s in practice
READ_TIMEOUT_S = 20.0
BUDGET_S = 90.0
SIMPLIFY_M = 10.0
DEFAULT_NODATA = 255


@dataclass(frozen=True)
class GfmResult:
    status: str  # "ok" | "none" | "error"
    item_id: str | None
    when_utc: datetime | None
    polys: list
    stale: bool = False  # True when served from the last-good cache after a failed fetch


class Window(NamedTuple):
    """A raster window over the frame: values, its affine transform, CRS, nodata."""
    array: object
    transform: object
    crs: object
    nodata: object


class _BudgetExceeded(Exception):
    pass


def _deps():
    try:
        import pyproj  # noqa: F401
        import rasterio  # noqa: F401
        import shapely  # noqa: F401
    except Exception:  # a broken install can fail with ValueError (numpy ABI) or OSError (GDAL), not just ImportError
        return None
    return True


def _frame_lonlat(frame_utm) -> tuple[float, float, float, float]:
    from pyproj import Transformer
    return Transformer.from_crs("EPSG:32721", "EPSG:4326", always_xy=True).transform_bounds(*frame_utm)


def _parse_dt(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def stac_search(bbox, start: datetime, end: datetime, timeout: float) -> list[dict]:
    """POST a STAC search for GFM items; returns the features as given."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    body = {
        "collections": ["GFM"],
        "bbox": list(bbox),
        "datetime": f"{start.strftime(fmt)}/{end.strftime(fmt)}",
        "limit": 50,
        "sortby": [{"field": "datetime", "direction": "desc"}],
    }
    req = urllib.request.Request(
        STAC_SEARCH_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/geo+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp).get("features", [])


def read_flood_window(href: str, frame_utm, timeout: float) -> Window | None:
    """Read the part of the flood raster that covers the frame; None if it does not overlap."""
    import rasterio
    from rasterio.errors import WindowError
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window as RWindow
    from rasterio.windows import from_bounds

    with rasterio.Env(GDAL_HTTP_TIMEOUT=str(max(1, int(timeout))), GDAL_HTTP_MAX_RETRY="1",
                      GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open(href) as src:
            bounds = transform_bounds("EPSG:32721", src.crs, *frame_utm)
            win = from_bounds(*bounds, transform=src.transform)
            # whole pixels plus one of margin, so the reprojected frame is fully covered
            col0, row0 = int(win.col_off) - 1, int(win.row_off) - 1
            col1, row1 = int(win.col_off + win.width) + 2, int(win.row_off + win.height) + 2
            win = RWindow(col0, row0, col1 - col0, row1 - row0)
            try:
                win = win.intersection(RWindow(0, 0, src.width, src.height))
            except WindowError:
                return None
            if win.width < 1 or win.height < 1:
                return None
            return Window(src.read(1, window=win), src.window_transform(win), src.crs, src.nodata)


def _has_data(w: Window) -> bool:
    import numpy as np
    nodata = DEFAULT_NODATA if w.nodata is None else w.nodata
    return bool(np.any(w.array != nodata))


def _ring(coords) -> list[int] | None:
    pts = [(int(round(x)), int(round(y))) for x, y in coords]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    out = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    if len(set(out)) < 3:
        return None
    return [v for p in out for v in p]


def _polygons(w: Window, frame_utm) -> list[list[int]]:
    """Value-1 pixels as simplified, frame-clipped rings in frame-relative metres."""
    from pyproj import CRS, Transformer
    from rasterio.features import shapes
    import numpy as np
    import shapely
    from shapely.affinity import translate
    from shapely.geometry import Polygon, box, shape
    from shapely.ops import unary_union

    mask = w.array == 1
    if not mask.any():
        return []
    geoms = [shape(g) for g, v in shapes(mask.astype("uint8"), mask=mask, transform=w.transform) if v == 1]
    if not geoms:
        return []
    union = unary_union(geoms)
    to_utm = Transformer.from_crs(CRS.from_user_input(w.crs), "EPSG:32721", always_xy=True).transform
    union = shapely.transform(union, lambda a: np.column_stack(to_utm(a[:, 0], a[:, 1])))
    x0, y0, x1, y1 = frame_utm
    union = union.intersection(box(x0, y0, x1, y1)).simplify(SIMPLIFY_M)
    union = translate(union, xoff=-x0, yoff=-y0)
    out: list[list[int]] = []
    for part in getattr(union, "geoms", [union]):
        if not isinstance(part, Polygon) or part.is_empty:
            continue
        for ring in [part.exterior, *part.interiors]:
            r = _ring(ring.coords)
            if r:
                out.append(r)
    return out


def latest_flood(frame_utm: tuple, now_utc: datetime, *,
                 search: Callable | None = None, read_window: Callable | None = None,
                 days: int = 10, budget_s: float = BUDGET_S,
                 search_timeout_s: float = SEARCH_TIMEOUT_S, read_timeout_s: float = READ_TIMEOUT_S,
                 clock: Callable[[], float] = time.monotonic) -> GfmResult:
    """The newest GFM scene in the last `days` days with data over the frame.

    search(bbox_lonlat, start, end, timeout) -> STAC items; read_window(href,
    frame_utm, timeout) -> Window or None. Never raises.
    """
    if _deps() is None:
        log.warning("gfm: rasterio/shapely/pyproj missing or broken (mapa extra); skipping the satellite scene")
        return GfmResult("error", None, None, [])
    search = search or stac_search
    read_window = read_window or read_flood_window
    t0 = clock()

    def timeout(cap: float) -> float:
        left = budget_s - (clock() - t0)
        if left <= 0:
            raise _BudgetExceeded
        return min(cap, left)

    def check() -> None:
        if clock() - t0 > budget_s:
            raise _BudgetExceeded

    try:
        start = now_utc - timedelta(days=days)
        items = search(_frame_lonlat(frame_utm), start, now_utc, timeout(search_timeout_s))
        check()
        dated = [(_parse_dt(i["properties"]["datetime"]), i) for i in items if ASSET in i.get("assets", {})]
        dated.sort(key=lambda p: p[0], reverse=True)
        for when, item in dated:
            w = read_window(item["assets"][ASSET]["href"], frame_utm, timeout(read_timeout_s))
            check()
            if w is None or not _has_data(w):
                continue
            return GfmResult("ok", item["id"], when, _polygons(w, frame_utm))
        return GfmResult("none", None, None, [])
    except _BudgetExceeded:
        log.warning("gfm: budget of %.0f s exhausted; no satellite scene this run", budget_s)
    except Exception as exc:  # the build must go on whatever the network or GDAL does
        log.warning("gfm: could not fetch the latest scene (%s: %s)", type(exc).__name__, exc)
    return GfmResult("error", None, None, [])


def _write_cache(path: Path, res: GfmResult, now_utc: datetime) -> None:
    data = {"status": res.status, "item_id": res.item_id,
            "when_utc": res.when_utc.isoformat() if res.when_utc else None,
            "cached_at": now_utc.isoformat(), "polys": res.polys}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        log.warning("gfm: could not write the cache (%s)", exc)


def _read_cache(path: Path, now_utc: datetime, days: int) -> GfmResult | None:
    """The cached result if it parses and is within `days` of now; else None."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        status = d["status"]
        if status not in ("ok", "none"):
            return None
        when = _parse_dt(d["when_utc"]) if d.get("when_utc") else None
        stamp = when if status == "ok" else _parse_dt(d["cached_at"])
        if stamp is None or now_utc - stamp > timedelta(days=days):
            return None
        polys = d.get("polys") or []
        if not all(isinstance(r, list) and all(type(v) is int for v in r) for r in polys):
            return None
        return GfmResult(status, d.get("item_id"), when, polys, stale=True)
    except Exception:  # unreadable, corrupt, or absurdly nested (RecursionError) cache
        return None


def latest_flood_cached(frame_utm: tuple, now_utc: datetime, cache_path: Path, **kw) -> GfmResult:
    """latest_flood, remembering the last "ok"/"none" so a failed fetch can show it (stale=True)."""
    try:
        res = latest_flood(frame_utm, now_utc, **kw)
        if res.status in ("ok", "none"):
            _write_cache(Path(cache_path), res, now_utc)
            return res
        cached = _read_cache(Path(cache_path), now_utc, kw.get("days", 10))
        if cached is not None:
            log.warning("gfm: serving the cached %s result after a failed fetch", cached.status)
            return cached
        return res
    except Exception as exc:  # the build must go on
        log.warning("gfm: unexpected failure (%s: %s)", type(exc).__name__, exc)
        return GfmResult("error", None, None, [])
