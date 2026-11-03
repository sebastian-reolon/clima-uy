"""City pack: the generated, committed content/mapa/<slug>/pack.json.gz.

Format (gzip JSON, compact separators). All coordinates are EPSG:32721 integer
metres relative to the frame origin (frame[0], frame[1]), y pointing north.
A ring is a flat list [x0, y0, x1, y1, ...] of at least 3 distinct points with
no repeated closing point. A polygon layer is a flat list of rings (outer
rings and holes together): draw it with fill-rule evenodd. A line layer is a
list of flat polylines (at least 2 points).

- frame: [xmin, ymin, xmax, ymax] in EPSG:32721 metres (the config's frame_utm)
- layers: river, blocks (polygon layers); streets_main, streets_minor (line
  layers); contours: [{"m": gauge metres, "lines": [polylines]}];
  labels: [{"name", "x", "y"}]
- levels: [{"m", "area_ha", "pop", "zones": [[name, people]], "poly": [rings],
  "grid": bool}] sorted by m; grid=False for the exact config marks off the grid
- meta: sources, generation date, method, totals

Only the stdlib is needed to read a pack.
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

from climauy.mapa.config import MAPA_DIR

PACK_NAME = "pack.json.gz"
POLY_LAYERS = ("river", "blocks")
LINE_LAYERS = ("streets_main", "streets_minor")


@dataclass(frozen=True)
class Pack:
    frame: tuple[int, int, int, int]
    layers: dict[str, list]
    levels: list[dict]
    meta: dict


def _check_coords(rings, min_points: int, where: str) -> None:
    for i, r in enumerate(rings):
        if not isinstance(r, list) or any(type(v) is not int for v in r):
            raise ValueError(f"{where}[{i}]: coordinates must be int lists")
        if len(r) % 2 or len(r) < 2 * min_points:
            raise ValueError(f"{where}[{i}]: expected at least {min_points} x,y pairs")


def validate(data: dict) -> None:
    """Shape checks shared by the writer and the reader."""
    for key in ("frame", "layers", "levels", "meta"):
        if key not in data:
            raise ValueError(f"pack: missing {key}")
    layers = data["layers"]
    for name in POLY_LAYERS:
        _check_coords(layers.get(name, []), 3, f"layers.{name}")
    for name in LINE_LAYERS:
        _check_coords(layers.get(name, []), 2, f"layers.{name}")
    for j, c in enumerate(layers.get("contours", [])):
        m = c.get("m")
        if isinstance(m, bool) or not isinstance(m, (int, float)):
            raise ValueError(f"layers.contours[{j}]: m must be a number")
        if not isinstance(c.get("lines"), list):
            raise ValueError(f"layers.contours[{j}]: lines must be a list")
        _check_coords(c["lines"], 2, f"layers.contours[{j}].lines")
    for j, lv in enumerate(data["levels"]):
        for key in ("m", "area_ha", "pop", "zones", "poly", "grid"):
            if key not in lv:
                raise ValueError(f"levels[{j}]: missing {key}")
        _check_coords(lv["poly"], 3, f"levels[{j}].poly")
    if not any(lv["grid"] for lv in data["levels"]):
        raise ValueError("levels: at least one grid level is needed")
    ms = [lv["m"] for lv in data["levels"]]
    if ms != sorted(ms):
        raise ValueError("levels: must be sorted by m")


def write_pack(path: Path, data: dict) -> None:
    validate(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    # mtime=0 keeps the gzip bytes identical for identical content.
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0, filename="") as f:
        f.write(text.encode("utf-8"))


def load_pack(slug: str, root: Path = MAPA_DIR) -> Pack:
    with gzip.open(Path(root) / slug / PACK_NAME, "rt", encoding="utf-8") as f:
        data = json.load(f)
    validate(data)
    return Pack(frame=tuple(data["frame"]), layers=data["layers"], levels=data["levels"], meta=data["meta"])
