"""Offline builder of a city pack (``python -m climauy mapa-pack <slug>``).

Heavy dependencies (the ``mapa-pack`` extra) are imported here at module level,
so the CLI imports this module lazily. Raw downloads go to the cache
(``CLIMAUY_MAPA_CACHE``, default ``~/.cache/clima-uy/mapa``), never the repo.

Pipeline, ported from the reference Paysandú analysis:
DTM tiles -> one 2 m grid over the frame (EPSG:32721, average) -> river seed
(OSM water polygons plus the DTM's flattened river surface connected to them,
largest connected body) -> fill_levels -> per level: flooded cells (stats from
the raw mask), sieve < 100 m2, vectorise, simplify 3 m; WorldPop people per
2 m cell; INE 2011 segments named by the nearest OSM neighbourhood point;
OSM roads -> blocks and streets; contours every 2 m from a 6 m DTM.
"""
from __future__ import annotations

import io
import math
import os
import shutil
import urllib.parse
import urllib.request
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import osmium
import rasterio
import shapefile
import shapely
from affine import Affine
from pyproj import Geod, Transformer
from rasterio.features import rasterize, shapes, sieve
from rasterio.warp import Resampling, reproject
from scipy import ndimage as ndi
from shapely.geometry import LineString, MultiLineString, Polygon, box, shape
from shapely.ops import polygonize, unary_union
from skimage.measure import find_contours

from climauy.mapa import flood
from climauy.mapa.config import MAPA_DIR, CityConfig, load_city
from climauy.mapa.pack import PACK_NAME, write_pack

CRS = "EPSG:32721"
RES = 2.0  # metres per analysis cell
CELL_M2 = RES * RES
SIEVE_CELLS = 25  # specks and holes under 100 m2 are dropped from the drawn polygons
LEVEL_SIMPLIFY_M = 3.0
LAYER_SIMPLIFY_M = 2.0
# The seed must cover at least this share of the OSM water lying on the DTM. OSM
# outlines spill over the banks, so it is never 100%: Paysandú is at 81%; with only
# its lower flattened surface (1.673 m) the seed would cover 28%.
MIN_SEED_WATER_SHARE = 0.6
CONTOUR_RES_M = 6.0
CONTOUR_STEP_M = 2
CONTOUR_BUFFER_M = 250.0
BLOCK_AREA_M2 = (1500.0, 80000.0)
BLOCK_INSET_M = 5.0
MOSAIC_KEEP_BYTES = 100 * 1024 * 1024

OSM_URL = "https://download.geofabrik.de/south-america/uruguay-latest.osm.pbf"
WORLDPOP_URL = ("https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2026/URY/v1/100m/"
                "constrained/ury_pop_2026_CN_100m_R2025A_v1.tif")
INE_URL = "https://www5.ine.gub.uy/documents/Cartograf%C3%ADa/Vectoriales/2011/mapas%20vectoriales%202011.zip"
INE_LAYER = "ine_seg_11"  # census segments 2011, WGS 84 / UTM 21S per the INE metadata (no .prj shipped)
IDE_LICENCE_URL = "https://visualizador.ide.uy/descargas/datos/00_Licencia_de_datos_abiertos.pdf"

MAIN_ROADS = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
              "secondary", "secondary_link"}
MINOR_ROADS = {"tertiary", "tertiary_link", "residential", "unclassified", "living_street"}
PLACE_KINDS = {"neighbourhood", "suburb"}


def cache_dir() -> Path:
    return Path(os.environ.get("CLIMAUY_MAPA_CACHE") or Path.home() / ".cache" / "clima-uy" / "mapa")


def fetch(url: str, dest: Path, timeout: float = 120.0) -> Path:
    """Download url to dest once; later runs reuse the cached file."""
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "clima-uy mapa-pack"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
        expected = r.headers.get("Content-Length")
        shutil.copyfileobj(r, f, 1 << 20)
    if expected is not None and part.stat().st_size != int(expected):
        part.unlink()
        raise OSError(f"truncated download: {url}")
    part.replace(dest)
    return dest


def _basename(url: str) -> str:
    return urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])


# ------------------------------------------------------------------ grid

class Grid:
    """The analysis grid: RES metres over the config frame, north up."""

    def __init__(self, frame: tuple[float, float, float, float]):
        self.x0, self.y0, self.x1, self.y1 = frame
        self.w = int(round((self.x1 - self.x0) / RES))
        self.h = int(round((self.y1 - self.y0) / RES))
        self.transform = Affine(RES, 0, self.x0, 0, -RES, self.y1)
        self.shape = (self.h, self.w)

    def window_of(self, left, bottom, right, top):
        c0 = max(0, int(math.floor((left - self.x0) / RES)))
        c1 = min(self.w, int(math.ceil((right - self.x0) / RES)))
        r0 = max(0, int(math.floor((self.y1 - top) / RES)))
        r1 = min(self.h, int(math.ceil((self.y1 - bottom) / RES)))
        return r0, r1, c0, c1


def build_dtm(cfg: CityConfig, grid: Grid, cache: Path) -> np.ndarray:
    """2 m DTM over the frame (NaN = no data). Tiles are deleted once the mosaic is written."""
    mosaic = cache / cfg.slug / "dtm2m.tif"
    if mosaic.is_file():
        with rasterio.open(mosaic) as s:
            if s.transform == grid.transform and (s.height, s.width) == grid.shape:
                return s.read(1)
    tiles_dir = cache / cfg.slug / "dtm_tiles"
    total = np.zeros(grid.shape, "float64")
    count = np.zeros(grid.shape, "uint8")
    for i, url in enumerate(cfg.dtm_urls):
        path = fetch(url, tiles_dir / _basename(url))
        with rasterio.open(path) as s:
            r0, r1, c0, c1 = grid.window_of(*s.bounds)
            if r1 <= r0 or c1 <= c0:
                continue
            sub = np.full((r1 - r0, c1 - c0), np.nan, "float32")
            # The IDE tiles are SIRGAS-ROU98 / UTM 21S, taken as identical to WGS 84 / UTM 21S.
            reproject(s.read(1), sub, src_transform=s.transform, src_crs=CRS, src_nodata=s.nodata,
                      dst_transform=grid.transform * Affine.translation(c0, r0), dst_crs=CRS,
                      dst_nodata=np.nan, resampling=Resampling.average)
        ok = np.isfinite(sub)
        total[r0:r1, c0:c1][ok] += sub[ok]
        count[r0:r1, c0:c1][ok] += 1
        if (i + 1) % 20 == 0:
            print(f"  dtm tiles {i + 1}/{len(cfg.dtm_urls)}", flush=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.where(count > 0, total / np.maximum(count, 1), np.nan).astype("float32")
    mosaic.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(mosaic, "w", driver="GTiff", width=grid.w, height=grid.h, count=1, dtype="float32",
                       crs=CRS, transform=grid.transform, nodata=np.nan, compress="deflate", tiled=True,
                       predictor=3) as d:
        d.write(z, 1)
    shutil.rmtree(tiles_dir, ignore_errors=True)
    return z


# ------------------------------------------------------------------ OSM

class _OsmHandler(osmium.SimpleHandler):
    def __init__(self, bbox_ll):
        super().__init__()
        self.bbox = bbox_ll
        self.wkb = osmium.geom.WKBFactory()
        self.roads: list[tuple[str, bytes]] = []
        self.water: list[bytes] = []
        self.places: list[tuple[float, float, str]] = []

    def _inside(self, lon, lat) -> bool:
        b = self.bbox
        return b[0] <= lon <= b[2] and b[1] <= lat <= b[3]

    def node(self, n):
        if n.tags.get("place") in PLACE_KINDS and n.tags.get("name") and n.location.valid():
            if self._inside(n.location.lon, n.location.lat):
                self.places.append((n.location.lon, n.location.lat, n.tags["name"]))

    def way(self, w):
        hw = w.tags.get("highway")
        if hw not in MAIN_ROADS and hw not in MINOR_ROADS:
            return
        try:
            if not any(self._inside(nd.location.lon, nd.location.lat) for nd in w.nodes):
                return
            self.roads.append((hw, self.wkb.create_linestring(w)))
        except (osmium.InvalidLocationError, RuntimeError):
            return

    def area(self, a):
        t = a.tags
        if not (t.get("natural") == "water" or t.get("waterway") == "riverbank" or t.get("water") == "river"):
            return
        try:
            self.water.append(self.wkb.create_multipolygon(a))
        except (osmium.InvalidLocationError, RuntimeError):
            return


def osm_timestamp(pbf: Path) -> str:
    h = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING)
    try:
        return h.header().get("osmosis_replication_timestamp", "") or ""
    finally:
        h.close()


def read_osm(pbf: Path, frame, margin_m: float = 1500.0):
    """Roads (UTM lines with class), water (UTM polygon) and neighbourhood points (UTM)."""
    to_ll = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    to_utm = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
    x0, y0, x1, y1 = frame
    lons, lats = to_ll.transform([x0 - margin_m, x1 + margin_m, x0 - margin_m, x1 + margin_m],
                                 [y0 - margin_m, y0 - margin_m, y1 + margin_m, y1 + margin_m])
    h = _OsmHandler((min(lons), min(lats), max(lons), max(lats)))
    h.apply_file(str(pbf), locations=True)

    def utm(g):
        return shapely.transform(g, lambda c: np.column_stack(to_utm.transform(c[:, 0], c[:, 1])))

    area = box(x0 - margin_m, y0 - margin_m, x1 + margin_m, y1 + margin_m)
    roads = [(hw, utm(shapely.from_wkb(w))) for hw, w in h.roads]
    water_geoms = []
    for w in h.water:
        g = utm(shapely.make_valid(shapely.from_wkb(w)))
        if g.intersects(area):
            water_geoms.append(g.intersection(area))
    water = unary_union(water_geoms) if water_geoms else Polygon()
    places = []
    for lon, lat, name in h.places:
        x, y = to_utm.transform(lon, lat)
        places.append((x, y, name))
    return roads, water, places


def place_label(name: str) -> str:
    return name[len("Barrio "):] if name.startswith("Barrio ") else name


# ------------------------------------------------------------------ river, people, zones

def river_seed(z: np.ndarray, valid: np.ndarray, water_mask: np.ndarray,
               flat_tol_m: float) -> tuple[np.ndarray, float, float]:
    """The river as one connected body: the DTM's hydro-flattened river surface,
    plus OSM water where the DTM has no data; the largest 8-connected component.

    Returns (seed, flat threshold, share of the OSM water on the DTM that the
    seed covers). The flat height is the median DTM height inside the OSM water
    polygons plus the city's ``river.flat_tol_m``. The global DTM minimum is not
    used: in Paysandú it is a 1.36 m pit, below the 1.67 / 1.84 m river. Where
    the DTM has data, OSM water above that height is not river: OSM outlines
    spill over the banks (18.6 ha of Paysandú land up to 10 m high), and
    counting them as river hid that ground from every flood level.

    A DTM can flatten the river at several heights. If the median falls on a
    lower one and the tolerance does not reach the others, the seed silently
    loses river; that shows as a low share and raises ValueError below
    MIN_SEED_WATER_SHARE.
    """
    inside = water_mask & valid
    if not inside.any():
        raise ValueError("no OSM water inside the DTM: cannot seed the river")
    thr = float(np.median(z[inside])) + flat_tol_m
    cand = (water_mask & ~valid) | (valid & (np.nan_to_num(z, nan=np.inf) <= thr))
    lab, _ = ndi.label(cand, structure=np.ones((3, 3), bool))
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    seed = lab == int(sizes.argmax())
    share = float((seed & inside).sum()) / float(inside.sum())
    if share < MIN_SEED_WATER_SHARE:
        raise ValueError(
            f"river seed covers {share:.0%} of the OSM water on the DTM (minimum {MIN_SEED_WATER_SHARE:.0%}) "
            f"at flat height {thr:.2f} m: the DTM may flatten the river at several heights; "
            "raise river.flat_tol_m in the city config to reach the highest one")
    return seed, thr, share


def river_layer_mask(seed: np.ndarray, water_mask: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Cells drawn as river on the plan: the seed, plus OSM water only where the
    DTM has no data (the same rule as the seed, so the plan never draws river
    over ground the analysis counts as dry)."""
    return seed | (water_mask & ~valid)


def people_per_cell(wp_path: Path, grid: Grid) -> np.ndarray:
    """WorldPop people spread evenly over each 100 m cell, then summed per 2 m cell."""
    to_ll = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    geod = Geod(ellps="WGS84")
    with rasterio.open(wp_path) as w:
        lons, lats = to_ll.transform([grid.x0, grid.x1, grid.x0, grid.x1], [grid.y0, grid.y0, grid.y1, grid.y1])
        win = w.window(min(lons) - 0.01, min(lats) - 0.01, max(lons) + 0.01, max(lats) + 0.01).round_offsets().round_lengths()
        wp = w.read(1, window=win, boundless=True, fill_value=0).astype("float64")
        wtr = w.window_transform(win)
        nd = w.nodata
    if nd is not None:
        wp[wp == nd] = 0
    wp[~np.isfinite(wp) | (wp < 0)] = 0
    cell_area = np.empty(wp.shape[0])
    for r in range(wp.shape[0]):
        ax, ay = wtr * (0, r)
        bx, by = wtr * (1, r + 1)
        cell_area[r] = abs(geod.polygon_area_perimeter([ax, bx, bx, ax], [ay, ay, by, by])[0])
    dens = np.zeros(grid.shape, "float32")
    cols = np.arange(grid.w) + 0.5
    for r0 in range(0, grid.h, 512):
        rr = np.arange(r0, min(r0 + 512, grid.h)) + 0.5
        cc, rg = np.meshgrid(cols, rr)
        x, y = grid.transform * (cc, rg)
        lon, lat = to_ll.transform(x, y)
        wc = np.floor((lon - wtr.c) / wtr.a).astype(int)
        wr = np.floor((lat - wtr.f) / wtr.e).astype(int)
        ok = (wr >= 0) & (wr < wp.shape[0]) & (wc >= 0) & (wc < wp.shape[1])
        d = np.zeros(wr.shape, "float32")
        d[ok] = wp[wr[ok], wc[ok]] * CELL_M2 / cell_area[wr[ok]]
        dens[r0:r0 + len(rr)] = d
    return dens


def read_segments(zip_path: Path, frame) -> list[tuple[str, object]]:
    """INE 2011 census segments (code, UTM polygon) whose box touches the frame."""
    fb = box(*frame)
    out = []
    with zipfile.ZipFile(zip_path) as z:
        shp = z.read(f"{INE_LAYER}.shp")
        shx = z.read(f"{INE_LAYER}.shx")
        dbf = z.read(f"{INE_LAYER}.dbf")
    r = shapefile.Reader(shp=io.BytesIO(shp), shx=io.BytesIO(shx), dbf=io.BytesIO(dbf), encoding="latin-1")
    fields = [f[0] for f in r.fields[1:]]
    ci = fields.index("CODSEG")
    for i in range(len(r)):
        bb = r.shape(i).bbox
        if bb[2] < frame[0] or bb[0] > frame[2] or bb[3] < frame[1] or bb[1] > frame[3]:
            continue
        g = shapely.make_valid(shape(r.shape(i).__geo_interface__))
        if g.intersects(fb):
            out.append((str(r.record(i)[ci]), g))
    return out


# ------------------------------------------------------------------ vector output

def _ring(coords, ox: float, oy: float) -> list[int] | None:
    flat: list[int] = []
    last = None
    for x, y in coords:
        p = (int(round(x - ox)), int(round(y - oy)))
        if p != last:
            flat += p
            last = p
    if len(flat) >= 4 and flat[:2] == flat[-2:]:
        flat = flat[:-2]
    if len({(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)}) < 3:
        return None
    return flat


def poly_rings(geom, ox: float, oy: float) -> list[list[int]]:
    out = []
    for p in getattr(geom, "geoms", [geom]):
        if hasattr(p, "geoms"):
            out += poly_rings(p, ox, oy)
            continue
        if not isinstance(p, Polygon) or p.is_empty:
            continue
        for ring in [p.exterior, *p.interiors]:
            r = _ring(ring.coords, ox, oy)
            if r:
                out.append(r)
    return out


def line_rings(geom, ox: float, oy: float) -> list[list[int]]:
    out = []
    for ln in getattr(geom, "geoms", [geom]):
        if hasattr(ln, "geoms"):
            out += line_rings(ln, ox, oy)
            continue
        if not isinstance(ln, LineString) or ln.is_empty:
            continue
        flat: list[int] = []
        last = None
        for x, y in ln.coords:
            p = (int(round(x - ox)), int(round(y - oy)))
            if p != last:
                flat += p
                last = p
        if len(flat) >= 4:
            out.append(flat)
    return out


def mask_polygons(mask: np.ndarray, grid: Grid, simplify_m: float):
    """Vectorise a boolean mask after removing pieces under SIEVE_CELLS cells."""
    rows = np.flatnonzero(mask.any(axis=1))
    if rows.size == 0:
        return []
    cols = np.flatnonzero(mask.any(axis=0))
    r0, r1 = max(rows[0] - 1, 0), min(rows[-1] + 2, grid.h)
    c0, c1 = max(cols[0] - 1, 0), min(cols[-1] + 2, grid.w)
    sub = sieve(mask[r0:r1, c0:c1].astype("uint8"), size=SIEVE_CELLS, connectivity=8)
    tr = grid.transform * Affine.translation(c0, r0)
    out = []
    for g, v in shapes(sub, mask=sub.astype(bool), transform=tr, connectivity=8):
        if v == 1:
            out.append(shape(g).simplify(simplify_m, preserve_topology=True))
    return out


def build_blocks(roads, river_geom, frame_poly):
    net = unary_union([g for _, g in roads])
    out = []
    for p in polygonize(net):
        if BLOCK_AREA_M2[0] < p.area < BLOCK_AREA_M2[1] and frame_poly.contains(p.representative_point()):
            b = p.buffer(-BLOCK_INSET_M)
            if not b.is_empty and not b.intersects(river_geom):
                out.append(b.simplify(LAYER_SIMPLIFY_M, preserve_topology=True))
    return out


def build_contours(z: np.ndarray, grid: Grid, zero: float, lo: float, hi: float, clip_geom):
    k = int(round(CONTOUR_RES_M / RES))
    hh, ww = grid.h // k * k, grid.w // k * k
    blocks = z[:hh, :ww].reshape(hh // k, k, ww // k, k)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN blocks
        z6 = np.nanmean(blocks, axis=(1, 3))
    valid6 = np.isfinite(z6)
    filled = np.where(valid6, z6, -9999.0)
    out = []
    g = math.ceil(lo / CONTOUR_STEP_M) * CONTOUR_STEP_M
    while g <= hi + 1e-9:
        lines = []
        for c in find_contours(filled, zero + g, mask=valid6):
            if len(c) < 2:
                continue
            # (row, col) of cell centres on the 6 m grid -> UTM
            xs = grid.x0 + (c[:, 1] + 0.5) * CONTOUR_RES_M
            ys = grid.y1 - (c[:, 0] + 0.5) * CONTOUR_RES_M
            lines.append(LineString(np.column_stack([xs, ys])))
        if lines:
            clipped = MultiLineString(lines).intersection(clip_geom).simplify(LAYER_SIMPLIFY_M)
            rings = line_rings(clipped, grid.x0, grid.y0)
            if rings:
                out.append({"m": float(g), "lines": rings})
        g += CONTOUR_STEP_M
    return out


# ------------------------------------------------------------------ levels

def level_list(cfg: CityConfig) -> list[tuple[float, bool]]:
    grid = flood.levels_grid(*cfg.range_m)
    out = {m: True for m in grid}
    for mk in cfg.marks:
        if not any(abs(mk.m - g) < 1e-6 for g in grid):
            out[round(mk.m, 2)] = False
    return sorted(out.items())


def build_pack(slug: str, root: Path = MAPA_DIR, extra_levels: tuple[float, ...] = ()) -> dict:
    cfg = load_city(slug, root)
    cache = cache_dir()
    grid = Grid(cfg.frame_utm)
    frame_poly = box(*cfg.frame_utm)
    ox, oy = grid.x0, grid.y0

    print(f"[{slug}] DTM {grid.w}x{grid.h} cells of {RES:g} m", flush=True)
    z = build_dtm(cfg, grid, cache)
    valid = np.isfinite(z)

    print(f"[{slug}] OSM", flush=True)
    pbf = fetch(OSM_URL, cache / "osm" / _basename(OSM_URL))
    osm_ts = osm_timestamp(pbf)
    roads, water, places = read_osm(pbf, cfg.frame_utm)
    water_mask = np.zeros(grid.shape, bool)
    if not water.is_empty:
        water_mask = rasterize([(water, 1)], out_shape=grid.shape, transform=grid.transform,
                               fill=0, dtype="uint8").astype(bool)
    river, flat_thr, seed_share = river_seed(z, valid, water_mask, cfg.river.flat_tol_m)
    print(f"[{slug}] river seed {river.sum() * CELL_M2 / 1e4:.0f} ha, flat river <= {flat_thr:.2f} m, "
          f"covers {seed_share:.0%} of the OSM water on the DTM", flush=True)

    fill = flood.fill_levels(z, valid, river)

    print(f"[{slug}] WorldPop and INE", flush=True)
    wp = fetch(WORLDPOP_URL, cache / "worldpop" / _basename(WORLDPOP_URL))
    dens = people_per_cell(wp, grid)
    dens[river | ~valid] = 0
    ine = fetch(INE_URL, cache / "ine" / _basename(INE_URL))
    segs = read_segments(ine, cfg.frame_utm)
    zone_ids = rasterize(((g, i + 1) for i, (_, g) in enumerate(segs)), out_shape=grid.shape,
                         transform=grid.transform, fill=0, dtype="int32") if segs else np.zeros(grid.shape, "int32")
    pts = [(x, y, place_label(n)) for x, y, n in places]
    reps = [g.representative_point() for _, g in segs]
    seg_names = flood.nearest_names(pts, [(p.x, p.y) for p in reps])
    names = {i + 1: n for i, n in enumerate(seg_names) if n}

    print(f"[{slug}] levels", flush=True)
    levels = []
    for m, on_grid in level_list(cfg):
        mask = flood.flooded(fill, river, cfg.zero_m + m)
        polys = mask_polygons(mask, grid, LEVEL_SIMPLIFY_M)
        rings = []
        for p in polys:
            rings += poly_rings(p, ox, oy)
        levels.append({
            "m": m, "grid": on_grid,
            "area_ha": round(float(mask.sum()) * CELL_M2 / 1e4, 1),
            "pop": int(round(flood.population_in(mask, dens))),
            "zones": [[n, p] for n, p in flood.zone_population(mask, dens, zone_ids, names)],
            "poly": rings,
        })
    checks = []
    for m in extra_levels:
        mask = flood.flooded(fill, river, cfg.zero_m + m)
        checks.append((m, round(float(mask.sum()) * CELL_M2 / 1e4, 1), int(round(flood.population_in(mask, dens)))))

    print(f"[{slug}] plan layers", flush=True)
    river_polys = mask_polygons(river_layer_mask(river, water_mask, valid), grid, LAYER_SIMPLIFY_M)
    river_geom = unary_union(river_polys).intersection(frame_poly)
    blocks = build_blocks(roads, river_geom, frame_poly)
    main = [g for hw, g in roads if hw in MAIN_ROADS]
    minor = [g for hw, g in roads if hw in MINOR_ROADS]

    def lines(gs):
        if not gs:
            return []
        parts = shapely.get_parts(unary_union(gs).intersection(frame_poly))
        # join the segments that the union split at every crossing
        merged = shapely.line_merge(MultiLineString([g for g in parts if isinstance(g, LineString)]))
        return line_rings(merged.simplify(LAYER_SIMPLIFY_M), ox, oy)

    top = flood.flooded(fill, river, cfg.zero_m + cfg.range_m[1])
    top_geom = unary_union(mask_polygons(top, grid, LEVEL_SIMPLIFY_M)).buffer(CONTOUR_BUFFER_M)
    contours = build_contours(z, grid, cfg.zero_m, cfg.range_m[0], cfg.range_m[1], top_geom.intersection(frame_poly))
    labels = sorted(({"name": n, "x": int(round(x - ox)), "y": int(round(y - oy))}
                     for x, y, n in pts if frame_poly.contains(shapely.Point(x, y))), key=lambda d: d["name"])

    block_rings = []
    for b in blocks:
        block_rings += poly_rings(b, ox, oy)
    pack = {
        "frame": [int(round(v)) for v in cfg.frame_utm],
        "layers": {
            "river": poly_rings(river_geom, ox, oy),
            "blocks": block_rings,
            "streets_main": lines(main),
            "streets_minor": lines(minor),
            "contours": contours,
            "labels": labels,
        },
        "levels": levels,
        "meta": {
            "slug": slug,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "crs": CRS,
            "units": "integer metres from the frame origin (xmin, ymin); y north",
            "zero_m": cfg.zero_m,
            "method": (
                "Flat water surface at zero_m + level (no slope along the river). Flooded = 2 m DTM cells whose "
                "river-connected fill level (8-neighbour priority flood) is below that surface; the river itself "
                "is excluded. River = largest connected body of the DTM's hydro-flattened river surface (at most "
                f"{cfg.river.flat_tol_m:g} m above the median DTM height inside OSM water) plus OSM water where the "
                "DTM has no data. Area and people "
                "come from the raw 2 m mask; drawn polygons drop pieces under 100 m2 and are simplified "
                f"{LEVEL_SIMPLIFY_M:g} m. People: WorldPop spread evenly over each 100 m cell. Zones: INE 2011 "
                "census segments named after the nearest OSM neighbourhood or suburb point."
            ),
            "totals": {
                "people_in_frame": int(round(float(dens.sum(dtype="float64")))),
                "river_ha": round(float(river.sum()) * CELL_M2 / 1e4, 1),
                "flat_river_m": round(flat_thr, 2),
                "seed_share_of_osm_water_on_dtm": round(seed_share, 3),
                "segments": len(segs),
                "zones": sorted(set(names.values())),
            },
            "sources": [
                {"what": "IDE Uruguay, MDT 1 m", "url": cfg.dtm_urls[0].rsplit("/", 1)[0] + "/",
                 "tiles": len(cfg.dtm_urls), "licence": "Licencia de Datos Abiertos de Uruguay",
                 "licence_url": IDE_LICENCE_URL, "note": "resampled to 2 m (average) and processed"},
                {"what": "OpenStreetMap (Geofabrik extract)", "url": OSM_URL, "date": osm_ts,
                 "licence": "ODbL, (c) OpenStreetMap contributors"},
                {"what": "WorldPop 2026 constrained 100 m, release R2025A", "url": WORLDPOP_URL,
                 "licence": "CC BY 4.0"},
                {"what": "INE, segmentos censales 2011", "url": INE_URL,
                 "licence": "libre, citando como fuente al INE"},
            ],
        },
    }
    write_pack(Path(root) / slug / PACK_NAME, pack)
    mosaic = cache / slug / "dtm2m.tif"
    if mosaic.is_file() and mosaic.stat().st_size > MOSAIC_KEEP_BYTES:
        mosaic.unlink()
    pack["_checks"] = checks
    return pack


def summary_lines(pack: dict, cfg: CityConfig) -> list[str]:
    by_m = {lv["m"]: lv for lv in pack["levels"]}
    out = []
    for mk in cfg.marks:
        lv = by_m.get(round(mk.m, 2))
        if lv is None:
            continue
        out.append(f"{mk.m:6.2f} m  {lv['area_ha']:8.1f} ha  {lv['pop']:7d} people  {mk.label}")
    for m, ha, pop in pack.get("_checks", []):
        out.append(f"{m:6.2f} m  {ha:8.1f} ha  {pop:7d} people  (check)")
    return out
