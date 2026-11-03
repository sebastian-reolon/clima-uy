"""Plan and staff drawings for the flood map page."""
import re
from dataclasses import replace

import pytest

from climauy.mapa.config import load_city
from climauy.mapa.pack import load_pack
from climauy.site.mapa_svg import plan_svg, ring_path, staff_svg


@pytest.fixture(scope="module")
def city():
    return load_city("paysandu"), load_pack("paysandu")


def _level(pack, m):
    return min(pack.levels, key=lambda lv: abs(lv["m"] - m))


def test_ring_path_square():
    # frame 0..100 m, unit 10: y flips (ymax - y) / unit
    d = ring_path([[0, 0, 100, 0, 100, 100, 0, 100]], (0, 0, 100, 100), 10)
    assert d == "M0.0,10.0L10.0,10.0L10.0,0.0L0.0,0.0Z"


def test_ring_path_joins_rings_and_drops_degenerate():
    rings = [[0, 0, 100, 0, 100, 100], [0, 0, 1, 1]]
    d = ring_path(rings, (0, 0, 100, 100), 10)
    assert d.count("M") == 1 and d.endswith("Z")
    assert ring_path([], (0, 0, 100, 100), 10) == ""


def test_ring_path_offset_frame():
    # a phone frame that starts at x=50: x shifts left
    d = ring_path([[50, 0, 150, 0, 150, 100]], (50, 0, 150, 100), 10)
    assert d.startswith("M0.0,10.0")


def test_plan_svg_structure(city):
    cfg, pack = city
    lv = _level(pack, 4.38)
    svg = plan_svg(pack, cfg, lv, None, "mapa-e")
    assert svg.count("mapa-e-hatch") == 2  # pattern id + fill reference
    assert svg.count('id="mapa-e-hatch"') == 1
    assert len(re.findall(r'class="est"', svg)) == 1
    d = ring_path(lv["poly"], (0, 0, 6800, 11400), 10)
    assert f'<path class="est" d="{d}"' in svg
    assert f'<path class="estl" d="{d}"' in svg
    assert "NaN" not in svg and "nan" not in svg
    assert 'viewBox="0 0 680 1140"' in svg
    assert "Paysandú" in svg
    for cls in ("paper", "blk", "streets", "iso", "isol", "rio", "obs", "bl", "pl", "deco", "tk", "frame"):
        assert f'class="{cls}' in svg, cls


def test_plan_svg_gfm_and_empty(city):
    cfg, pack = city
    lv = _level(pack, 4.38)
    ring = [100, 100, 900, 100, 900, 900, 100, 900]
    svg = plan_svg(pack, cfg, lv, [ring], "m")
    assert '<path class="obs" d="M' in svg
    for g in (None, []):
        assert '<path class="obs" d="" ' in plan_svg(pack, cfg, lv, g, "m")


def test_plan_svg_variants_differ(city):
    cfg, pack = city
    lv = _level(pack, 4.38)
    wide = plan_svg(pack, cfg, lv, None, "mapa-wide")
    movil = plan_svg(pack, cfg, lv, None, "mapa-movil", variant="movil")
    assert 'id="mapa-wide"' in wide and 'id="mapa-movil"' in movil
    assert "mapa-movil-hatch" in movil and "mapa-wide-hatch" not in movil
    assert 'viewBox="0 0 480 1140"' in movil  # phone frame is 4800 m wide
    assert wide != movil
    assert "NaN" not in movil


def test_plan_svg_contour_labels_spaced_and_labels_inside(city):
    cfg, pack = city
    svg = plan_svg(pack, cfg, _level(pack, 6), None, "m")
    pts = [(float(x), float(y)) for x, y in re.findall(r'class="isol" x="([\d.]+)" y="([\d.]+)"', svg)]
    assert pts
    for i, a in enumerate(pts):
        for b in pts[i + 1:]:
            assert ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 > 60
    assert re.search(r'class="isol"[^>]*>\d+ m</text>', svg)
    for x, y in re.findall(r'class="bl" x="([\d.]+)" y="([\d.]+)"', svg):
        assert 60 < float(x) < 680 - 60 and 0 < float(y) < 1140


def test_plan_svg_river_label_and_ticks(city):
    cfg, pack = city
    svg = plan_svg(pack, cfg, _level(pack, 6), None, "m")
    assert "rotate(-82" in svg and "Río Uruguay" in svg
    assert svg.count('class="tk"') == 2 * 7 + 2 * 11  # 394..400 km east, 6420..6430 km north
    assert "1 km" in svg and ">N</text>" in svg


def _cfg_with(cfg, marks, rng=None):
    return replace(cfg, marks=marks, range_m=rng or cfg.range_m)


def _baselines(svg):
    return sorted(float(y) for y in re.findall(r'class="mkt[^"]*" x="[\d.]+" y="([\d.]+)"', svg))


def test_staff_merges_today_with_alerta(city):
    cfg, _ = city
    svg = staff_svg(cfg, 4.38)
    assert "hoy, y alerta 4,38" in svg
    assert ">alerta" not in svg
    assert svg.count(">hoy") == 1


def test_staff_merges_near_marks_without_today(city):
    cfg, _ = city
    svg = staff_svg(cfg, None)
    assert "2009 y 2015/16 9,07" in svg
    assert ">2015/16" not in svg
    assert "hoy" not in svg
    assert ">alerta 4,39<" in svg


def test_staff_omits_out_of_range(city):
    cfg, _ = city
    far = replace(cfg.marks[0], short="lejano", m=15.0)
    svg = staff_svg(_cfg_with(cfg, cfg.marks + [far]), 4.38)
    assert "lejano" not in svg
    # today outside the range: no hoy label, no crash
    assert "hoy" not in staff_svg(cfg, 20.0)


def test_staff_labels_never_closer_than_13(city):
    cfg, _ = city
    close = [replace(cfg.marks[0], short=f"c{i}", m=7.0 + i * 0.12) for i in range(5)]
    for today in (None, 4.38, 7.1):
        svg = staff_svg(_cfg_with(cfg, cfg.marks + close), today)
        ys = _baselines(svg)
        assert len(ys) >= 5
        assert all(b - a >= 13 - 1e-6 for a, b in zip(ys, ys[1:]))


def test_staff_ticks_and_labels(city):
    cfg, _ = city
    svg = staff_svg(cfg, 4.38)
    assert svg.count('class="stt"') == 37  # 3 to 12 m every 0.25
    nums = re.findall(r'class="stn"[^>]*>(\d+)</text>', svg)
    assert nums == [str(n) for n in range(3, 13)]
    assert 'data-lo="3' in svg and 'data-hi="12' in svg
    assert 'class="cur"' in svg and 'class="hit"' in svg
    assert "NaN" not in svg
