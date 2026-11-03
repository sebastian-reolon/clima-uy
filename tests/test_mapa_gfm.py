import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from climauy.mapa import gfm
from climauy.mapa.gfm import GfmResult, latest_flood

FIXTURE = Path(__file__).parent / "fixtures" / "gfm" / "search.json"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
FRAME = (393500.0, 6419500.0, 400300.0, 6430900.0)


def _items():
    return json.loads(FIXTURE.read_text())["features"]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_fixture_is_three_real_items_newest_first():
    items = _items()
    assert len(items) == 3
    assert all("ensemble_flood_extent" in i["assets"] for i in items)
    assert items[0]["properties"]["datetime"] > items[1]["properties"]["datetime"]


def test_picks_newest_with_data_skipping_all_nodata():
    items = _items()
    seen = []

    def read(href, frame, timeout):
        seen.append(href)
        if href == items[0]["assets"]["ensemble_flood_extent"]["href"]:
            return None  # the newest only touches the frame with nodata
        return gfm.Window(*_blank_window(frame))

    res = latest_flood(FRAME, NOW, search=lambda *a, **k: list(reversed(items)), read_window=read)
    assert res.status == "ok"
    assert res.item_id == items[1]["id"]
    assert res.when_utc == datetime(2026, 11, 2, 9, 6, 11, tzinfo=timezone.utc)
    assert res.polys == []  # data but no flood pixel: the normal case
    assert len(seen) == 2  # stopped at the first scene with data


def test_none_when_no_items():
    res = latest_flood(FRAME, NOW, search=lambda *a, **k: [], read_window=lambda *a, **k: pytest.fail("read"))
    assert res == GfmResult("none", None, None, [])


def test_none_when_no_item_has_data():
    res = latest_flood(FRAME, NOW, search=lambda *a, **k: _items(), read_window=lambda *a, **k: None)
    assert res.status == "none"


def test_error_when_search_raises():
    def boom(*a, **k):
        raise OSError("down")

    assert latest_flood(FRAME, NOW, search=boom) == GfmResult("error", None, None, [])


def test_error_when_read_raises():
    def boom(*a, **k):
        raise RuntimeError("vsicurl")

    res = latest_flood(FRAME, NOW, search=lambda *a, **k: _items(), read_window=boom)
    assert res.status == "error"


def test_error_when_budget_exhausted_after_slow_search():
    clock = Clock()

    def slow(*a, **k):
        clock.t += 61
        return _items()

    res = latest_flood(FRAME, NOW, search=slow, read_window=lambda *a, **k: pytest.fail("read"),
                       clock=clock, budget_s=60)
    assert res.status == "error"


def test_error_when_budget_runs_out_between_reads():
    clock = Clock()

    def slow_read(href, frame, timeout):
        clock.t += 25
        return None

    res = latest_flood(FRAME, NOW, search=lambda *a, **k: _items(), read_window=slow_read,
                       clock=clock, budget_s=60)
    assert res.status == "error"


def test_request_timeout_is_capped_by_remaining_budget():
    clock = Clock()
    clock.t = 0
    timeouts = []

    def search(bbox, start, end, timeout):
        timeouts.append(timeout)
        clock.t += 50
        return _items()

    def read(href, frame, timeout):
        timeouts.append(timeout)
        return None

    latest_flood(FRAME, NOW, search=search, read_window=read, clock=clock, budget_s=60)
    assert timeouts[0] == 50
    assert timeouts[1] == pytest.approx(10)


def test_search_gets_lonlat_bbox_and_ten_day_window():
    got = {}

    def search(bbox, start, end, timeout):
        got.update(bbox=bbox, start=start, end=end)
        return []

    latest_flood(FRAME, NOW, search=search)
    w, s, e, n = got["bbox"]
    assert -58.2 < w < e < -58.0 and -32.4 < s < n < -32.2
    assert got["end"] == NOW and got["start"] == NOW - timedelta(days=10)


def test_missing_extra_is_an_error_not_a_crash(monkeypatch, caplog):
    monkeypatch.setattr(gfm, "_deps", lambda: None)
    with caplog.at_level("WARNING"):
        res = latest_flood(FRAME, NOW, search=lambda *a, **k: pytest.fail("search"))
    assert res == GfmResult("error", None, None, [])
    assert len([r for r in caplog.records if "gfm" in r.getMessage().lower()]) == 1


def _blank_window(frame):
    import numpy as np
    from rasterio.transform import from_origin
    tr = from_origin(frame[0], frame[3], 20, 20)
    a = np.zeros((int((frame[3] - frame[1]) / 20), int((frame[2] - frame[0]) / 20)), dtype="uint8")
    return a, tr, "EPSG:32721", 255


# ---- real raster path (needs the mapa extra) ----

def _write_tif(path, crs, bounds, flood_box, size=8, nodata=255, nodata_all=False):
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds
    a = np.zeros((size, size), dtype="uint8")
    if nodata_all:
        a[:] = nodata
    else:
        r0, r1, c0, c1 = flood_box
        a[r0:r1, c0:c1] = 1
    with rasterio.open(path, "w", driver="GTiff", height=size, width=size, count=1, dtype="uint8",
                       crs=crs, transform=from_bounds(*bounds, size, size), nodata=nodata) as dst:
        dst.write(a, 1)


def _item_for(href, when="2026-11-02T09:06:36Z"):
    return {"id": "X", "properties": {"datetime": when},
            "assets": {"ensemble_flood_extent": {"href": str(href)}}}


def _inside(polys, frame):
    w, h = frame[2] - frame[0], frame[3] - frame[1]
    return all(0 <= v <= w for r in polys for v in r[0::2]) and all(0 <= v <= h for r in polys for v in r[1::2])


def test_flood_polygons_in_frame_metres_utm_raster(tmp_path):
    pytest.importorskip("rasterio")
    pytest.importorskip("shapely")
    pytest.importorskip("pyproj")
    # 8x8 raster of 1 km cells wider than the frame; the flood block sits inside it
    bounds = (390000, 6416000, 404000, 6434000)
    tif = tmp_path / "f.tif"
    _write_tif(tif, "EPSG:32721", bounds, (3, 5, 3, 5))
    res = latest_flood(FRAME, NOW, search=lambda *a, **k: [_item_for(tif)])
    assert res.status == "ok" and res.item_id == "X"
    assert res.polys, "flood block should vectorise"
    assert all(type(v) is int for r in res.polys for v in r)
    assert all(len(r) % 2 == 0 and len(r) >= 6 for r in res.polys)
    assert _inside(res.polys, FRAME)
    # cells col 3..5 -> x 395750..., rows 3..5 -> y; the ring spans about 2.25 km
    xs = [v for r in res.polys for v in r[0::2]]
    assert max(xs) - min(xs) == pytest.approx(2 * 1750, abs=40)


def test_polygons_clipped_to_frame_and_reprojected_from_wgs84(tmp_path):
    pytest.importorskip("rasterio")
    pytest.importorskip("shapely")
    pytest.importorskip("pyproj")
    # whole raster is flood and far larger than the frame, in lon/lat
    tif = tmp_path / "g.tif"
    _write_tif(tif, "EPSG:4326", (-58.5, -32.6, -57.7, -32.0), (0, 8, 0, 8))
    res = latest_flood(FRAME, NOW, search=lambda *a, **k: [_item_for(tif)])
    assert res.status == "ok" and len(res.polys) == 1
    assert _inside(res.polys, FRAME)
    r = res.polys[0]
    assert (min(r[0::2]), min(r[1::2])) == (0, 0)
    assert (max(r[0::2]), max(r[1::2])) == (6800, 11400)


def test_all_nodata_window_is_skipped_and_data_without_flood_is_ok(tmp_path):
    pytest.importorskip("rasterio")
    pytest.importorskip("shapely")
    pytest.importorskip("pyproj")
    nod, dry = tmp_path / "n.tif", tmp_path / "d.tif"
    bounds = (390000, 6416000, 404000, 6434000)
    _write_tif(nod, "EPSG:32721", bounds, None, nodata_all=True)
    _write_tif(dry, "EPSG:32721", bounds, (0, 0, 0, 0))
    items = [_item_for(nod, "2026-11-02T09:06:36Z"), {**_item_for(dry, "2026-11-01T09:14:31Z"), "id": "DRY"}]
    res = latest_flood(FRAME, NOW, search=lambda *a, **k: items)
    assert res.status == "ok" and res.item_id == "DRY" and res.polys == []


def test_window_outside_raster_counts_as_no_data(tmp_path):
    pytest.importorskip("rasterio")
    pytest.importorskip("shapely")
    pytest.importorskip("pyproj")
    tif = tmp_path / "far.tif"
    _write_tif(tif, "EPSG:32721", (100000, 6000000, 110000, 6010000), (0, 8, 0, 8))
    res = latest_flood(FRAME, NOW, search=lambda *a, **k: [_item_for(tif)])
    assert res.status == "none"


# ---- last-good cache ----

def _ok_search(*a, **k):
    return _items()


def _ok_read(href, frame, timeout):
    return gfm.Window(*_blank_window(frame))


def _fail(*a, **k):
    raise OSError("down")


def test_defaults_are_50s_search_20s_read_90s_budget():
    clock = Clock()
    seen = []

    def search(bbox, start, end, timeout):
        seen.append(timeout)
        return _items()

    def read(href, frame, timeout):
        seen.append(timeout)
        return None

    latest_flood(FRAME, NOW, search=search, read_window=read, clock=clock)
    assert seen[:2] == [50, 20]


def test_ok_writes_cache_and_returns_fresh(tmp_path):
    pytest.importorskip("rasterio")
    cache = tmp_path / "sub" / "gfm.json"
    res = gfm.latest_flood_cached(FRAME, NOW, cache, search=_ok_search, read_window=_ok_read)
    assert res.status == "ok" and res.stale is False
    data = json.loads(cache.read_text())
    assert data["status"] == "ok" and data["item_id"] == res.item_id and data["polys"] == []
    assert data["when_utc"].startswith("2026-11-02T09:06:36")


def test_error_with_fresh_cache_returns_stale_cached(tmp_path):
    pytest.importorskip("rasterio")
    cache = tmp_path / "gfm.json"
    first = gfm.latest_flood_cached(FRAME, NOW, cache, search=_ok_search, read_window=_ok_read)
    res = gfm.latest_flood_cached(FRAME, NOW + timedelta(hours=3), cache, search=_fail)
    assert res.stale is True and res.status == "ok"
    assert (res.item_id, res.when_utc, res.polys) == (first.item_id, first.when_utc, first.polys)


def test_error_with_cache_older_than_ten_days_is_error(tmp_path):
    pytest.importorskip("rasterio")
    cache = tmp_path / "gfm.json"
    gfm.latest_flood_cached(FRAME, NOW, cache, search=_ok_search, read_window=_ok_read)
    res = gfm.latest_flood_cached(FRAME, NOW + timedelta(days=13), cache, search=_fail)
    assert res == GfmResult("error", None, None, []) and res.stale is False


def test_cached_none_ages_by_write_time(tmp_path):
    cache = tmp_path / "gfm.json"
    gfm._write_cache(cache, GfmResult("none", None, None, []), NOW)
    fresh = gfm._read_cache(cache, NOW + timedelta(days=9), 10)
    assert fresh == GfmResult("none", None, None, [], stale=True)
    assert gfm._read_cache(cache, NOW + timedelta(days=11), 10) is None


def test_corrupt_cache_is_ignored(tmp_path):
    pytest.importorskip("rasterio")
    cache = tmp_path / "gfm.json"
    for junk in ("{not json", "[]", '{"status": "ok", "when_utc": "nope"}'):
        cache.write_text(junk)
        res = gfm.latest_flood_cached(FRAME, NOW, cache, search=_fail)
        assert res.status == "error" and res.stale is False
