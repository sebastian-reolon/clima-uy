import numpy as np
import pytest

pytest.importorskip("skimage")
pytest.importorskip("scipy")

from climauy.mapa.flood import (  # noqa: E402
    fill_levels,
    flooded,
    levels_grid,
    nearest_names,
    population_in,
    zone_population,
)


def _row(values, river_idx=0):
    dtm = np.array([values], dtype="float32")
    valid = np.ones_like(dtm, dtype=bool)
    river = np.zeros_like(valid)
    river[0, river_idx] = True
    return dtm, valid, river


def test_fill_reaches_basin_only_over_saddle():
    dtm, valid, river = _row([0, 1, 5, 2, 2, 5, 9])
    fill = fill_levels(dtm, valid, river)
    assert fill.dtype == np.float32
    assert fill[0, 0] < 0
    assert fill[0, 1:].tolist() == [1, 5, 5, 5, 5, 9]
    assert flooded(fill, river, 4.0)[0].tolist() == [False, True, False, False, False, False, False]
    assert flooded(fill, river, 5.5)[0].tolist() == [False, True, True, True, True, True, False]


def test_isolated_depression_stays_dry():
    dtm = np.full((5, 5), 6.0, dtype="float32")
    dtm[:, 1] = 0.0
    dtm[2, 3] = 0.0
    valid = np.ones((5, 5), dtype=bool)
    river = np.zeros((5, 5), dtype=bool)
    river[:, 1] = True
    fill = fill_levels(dtm, valid, river)
    assert not flooded(fill, river, 3.0)[2, 3]
    assert flooded(fill, river, 6.5)[2, 3]


def test_river_excluded():
    dtm, valid, river = _row([0, 1, 5, 2, 2, 5, 9])
    fill = fill_levels(dtm, valid, river)
    assert not flooded(fill, river, 100.0)[river].any()


def test_invalid_cells_never_flood():
    dtm, valid, river = _row([0, 1, 1, 1])
    valid[0, 2] = False
    fill = fill_levels(dtm, valid, river)
    assert np.isposinf(fill[0, 2])
    wet = flooded(fill, river, 100.0)
    assert not wet[0, 2]
    # Cell 3 is only reachable through the invalid cell, so it stays dry.
    assert wet[0].tolist() == [False, True, False, False]


def test_population_and_zones():
    mask = np.array([[True, True], [True, True]])
    density = np.full((2, 2), 2.5)
    assert population_in(mask, density) == 10.0
    zone_ids = np.array([[1, 1], [2, 2]])
    names = {1: "B", 2: "A"}
    assert zone_population(mask, density, zone_ids, names) == [("A", 5), ("B", 5)]

    small = np.array([[True, False], [False, False]])
    dens = np.array([[0.4, 0.0], [0.0, 0.0]])
    assert zone_population(small, dens, zone_ids, names) == []
    both = np.array([[True, False], [True, False]])
    dens2 = np.array([[10.0, 0.0], [0.4, 0.0]])
    assert zone_population(both, dens2, zone_ids, names) == [("B", 10)]


def test_zone_population_groups_ids_sharing_a_name_and_sorts_desc():
    mask = np.ones((1, 3), dtype=bool)
    density = np.array([[1.0, 2.0, 5.0]])
    zone_ids = np.array([[1, 2, 3]])
    names = {1: "X", 2: "X", 3: "Y"}
    assert zone_population(mask, density, zone_ids, names) == [("Y", 5), ("X", 3)]


def test_nearest_names():
    points = [(0.0, 0.0, "origin"), (10.0, 10.0, "far")]
    assert nearest_names(points, [(1.0, 1.0), (9.0, 8.0)]) == ["origin", "far"]
    assert nearest_names([], [(1.0, 1.0)]) == [""]


def test_levels_grid():
    assert levels_grid(3, 4) == [3.0, 3.25, 3.5, 3.75, 4.0]
    assert levels_grid(0.1, 0.5, 0.1) == [0.1, 0.2, 0.3, 0.4, 0.5]
