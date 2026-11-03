"""Pure numeric core of the city flood map.

scipy and scikit-image are optional (``mapa-pack`` extra) and imported lazily.
"""
from __future__ import annotations

import numpy as np

_BIG = 1000.0  # finite stand-in for "unreached / invalid" during reconstruction
_RIVER_LOW = -1000.0  # river cells are always wet at any realistic water level


def fill_levels(dtm: np.ndarray, valid: np.ndarray, river: np.ndarray) -> np.ndarray:
    """Lowest flat water-surface elevation at which water spreading from the
    river reaches each cell (grayscale reconstruction by erosion).

    River cells get a very low value; invalid (or NaN) or unreachable cells get
    +inf. Connectivity is 8-neighbour (skimage's default footprint), the same
    as the reference pipeline.
    """
    from skimage.morphology import reconstruction

    river = np.asarray(river, dtype=bool)
    valid = np.asarray(valid, dtype=bool) & ~np.isnan(dtm)
    surf = np.where(valid, dtm, _BIG).astype("float64")
    surf[river] = _RIVER_LOW
    marker = np.full(surf.shape, _BIG, dtype="float64")
    marker[river] = _RIVER_LOW
    fill = reconstruction(marker, surf, method="erosion")
    out = fill.astype("float32")
    out[(~valid & ~river) | (fill >= _BIG)] = np.inf
    return out


def flooded(fill: np.ndarray, river: np.ndarray, wse: float) -> np.ndarray:
    """Cells wetted at water-surface elevation ``wse``, river excluded."""
    return (fill < wse) & ~river.astype(bool)


def population_in(mask: np.ndarray, density: np.ndarray) -> float:
    """Total people in ``mask``; ``density`` is already people per cell."""
    return float(density[mask].sum(dtype="float64"))


def zone_population(
    mask: np.ndarray,
    density: np.ndarray,
    zone_ids: np.ndarray,
    names: dict[int, str],
) -> list[tuple[str, int]]:
    """People affected per named zone: rounded, at least 1, largest first.

    Ids sharing a name are summed; ties are ordered by name.
    """
    ids, inv = np.unique(zone_ids[mask], return_inverse=True)
    sums = np.bincount(inv, weights=density[mask].astype("float64"), minlength=len(ids))
    totals: dict[str, float] = {}
    for zid, total in zip(ids.tolist(), sums.tolist()):
        name = names.get(zid)
        if name is not None:
            totals[name] = totals.get(name, 0.0) + total
    rows = [(name, int(round(v))) for name, v in totals.items()]
    rows = [r for r in rows if r[1] >= 1]
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows


def nearest_names(
    points: list[tuple[float, float, str]], targets: list[tuple[float, float]]
) -> list[str]:
    """Name of the closest point for each target (empty string if no points)."""
    if not points:
        return [""] * len(targets)
    px = np.array([p[0] for p in points], dtype="float64")
    py = np.array([p[1] for p in points], dtype="float64")
    out = []
    for x, y in targets:
        out.append(points[int(np.argmin((px - x) ** 2 + (py - y) ** 2))][2])
    return out


def levels_grid(lo: float, hi: float, step: float = 0.25) -> list[float]:
    """Inclusive grid of water levels, rounded to 2 decimals (no float drift)."""
    n = int(round((hi - lo) / step))
    if abs(lo + n * step - hi) > 1e-9:
        raise ValueError(f"range {lo}..{hi} is not a multiple of step {step}")
    return [round(lo + i * step, 2) for i in range(n + 1)]
