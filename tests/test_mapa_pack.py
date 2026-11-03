import copy
import gzip
import json
from pathlib import Path

import pytest

from climauy.mapa.config import CONTENT_DIR, list_cities, load_city
from climauy.mapa.pack import load_pack, write_pack
from climauy.publish import find_infra

SRC = {"url": "https://example.org/a.pdf", "date": "2026-10-22", "name": "Fuente"}

BASE = {
    "slug": "villa",
    "name": "Villa",
    "frame_utm": [1000, 2000, 1400, 2600],
    "phone_frame_utm": [1000, 2000, 1200, 2600],
    "dtm_urls": ["https://example.org/t1.tif"],
    "zero_m": 0.43,
    "zero_source": SRC,
    "range_m": [3, 12],
    "marks": [
        {"label": "Alerta (CARU)", "short": "alerta", "m": 4.39, "kind": "oficial", "source": SRC},
    ],
    "today": {"station_id": "ina_80", "offset_m": 1.18, "offset_range": [1.14, 1.20], "source": SRC},
    "sinae_check": {"m": 7.84, "people": 2105, "source": SRC},
}


def _write_cfg(root: Path, cfg: dict) -> None:
    d = root / cfg["slug"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


def test_load_city_ok(tmp_path):
    _write_cfg(tmp_path, BASE)
    c = load_city("villa", tmp_path)
    assert c.slug == "villa"
    assert c.frame_utm == (1000.0, 2000.0, 1400.0, 2600.0)
    assert c.range_m == (3.0, 12.0)
    assert c.marks[0].kind == "oficial" and c.marks[0].source.url == SRC["url"]
    assert c.today.offset_range == (1.14, 1.20)
    assert c.sinae_check.people == 2105


def test_mark_without_source_url_rejected(tmp_path):
    cfg = copy.deepcopy(BASE)
    del cfg["marks"][0]["source"]["url"]
    _write_cfg(tmp_path, cfg)
    with pytest.raises(ValueError, match=r"marks\[0\]\.source"):
        load_city("villa", tmp_path)


def test_range_lo_not_below_hi_rejected(tmp_path):
    cfg = copy.deepcopy(BASE)
    cfg["range_m"] = [12, 12]
    _write_cfg(tmp_path, cfg)
    with pytest.raises(ValueError, match="range_m"):
        load_city("villa", tmp_path)


def test_bad_mark_kind_rejected(tmp_path):
    cfg = copy.deepcopy(BASE)
    cfg["marks"][0]["kind"] = "rumor"
    _write_cfg(tmp_path, cfg)
    with pytest.raises(ValueError, match=r"marks\[0\]\.kind"):
        load_city("villa", tmp_path)


def test_today_and_sinae_optional(tmp_path):
    cfg = copy.deepcopy(BASE)
    cfg["today"] = None
    del cfg["sinae_check"]
    _write_cfg(tmp_path, cfg)
    c = load_city("villa", tmp_path)
    assert c.today is None and c.sinae_check is None


def _tiny_pack() -> dict:
    return {
        "frame": [1000, 2000, 1400, 2600],
        "layers": {
            "river": [[0, 0, 10, 0, 10, 10]],
            "blocks": [[20, 20, 40, 20, 40, 40, 20, 40]],
            "streets_main": [[0, 5, 100, 5]],
            "streets_minor": [],
            "contours": [{"m": 4.0, "lines": [[0, 1, 2, 3]]}],
            "labels": [{"name": "Centro", "x": 200, "y": 300}],
        },
        "levels": [
            {"m": 3.0, "area_ha": 1.5, "pop": 0, "zones": [], "poly": [[0, 0, 4, 0, 4, 4]], "grid": True},
            {"m": 4.39, "area_ha": 2.25, "pop": 21, "zones": [["Centro", 21]],
             "poly": [[0, 0, 6, 0, 6, 6, 0, 6], [1, 1, 2, 1, 2, 2]], "grid": False},
        ],
        "meta": {"generated": "2026-11-03", "sources": []},
    }


def test_pack_round_trip(tmp_path):
    d = tmp_path / "villa"
    d.mkdir()
    write_pack(d / "pack.json.gz", _tiny_pack())
    with gzip.open(d / "pack.json.gz", "rt", encoding="utf-8") as f:
        raw = f.read()
    assert ", " not in raw and ": " not in raw  # compact separators
    p = load_pack("villa", tmp_path)
    assert p.levels == _tiny_pack()["levels"]
    assert p.frame == (1000, 2000, 1400, 2600)
    for lv in p.levels:
        for ring in lv["poly"]:
            assert all(type(v) is int for v in ring)
            assert len(ring) % 2 == 0 and len(ring) >= 6
    assert p.layers["labels"][0]["name"] == "Centro"


def test_pack_rejects_float_coordinates(tmp_path):
    bad = _tiny_pack()
    bad["levels"][0]["poly"] = [[0.5, 0, 4, 0, 4, 4]]
    with pytest.raises(ValueError, match="int"):
        write_pack(tmp_path / "p.json.gz", bad)


def test_list_cities_needs_config_and_pack(tmp_path):
    _write_cfg(tmp_path, BASE)
    assert list_cities(tmp_path) == []
    write_pack(tmp_path / "villa" / "pack.json.gz", _tiny_pack())
    assert list_cities(tmp_path) == ["villa"]


def test_committed_city_files_have_no_infra_strings(tmp_path):
    files = sorted((CONTENT_DIR / "mapa").glob("*/*"))
    if not any(f.name == "pack.json.gz" for f in files):
        pytest.skip("no city pack built yet")
    plain = []
    for f in files:
        if f.suffix == ".gz":  # scan the decompressed text, not the gzip bytes
            out = tmp_path / f"{f.parent.name}-{f.stem}"
            out.write_bytes(gzip.decompress(f.read_bytes()))
            plain.append(out)
        else:
            plain.append(f)
    assert find_infra(plain) == []
    for f in plain:
        assert "/Users/" not in f.read_text(encoding="utf-8")


def test_committed_paysandu_config_loads():
    if not (CONTENT_DIR / "mapa" / "paysandu" / "config.json").exists():
        pytest.skip("no Paysandú config yet")
    c = load_city("paysandu")
    assert c.zero_m == 0.43 and c.range_m == (3.0, 12.0)
    assert [m.m for m in c.marks if m.short == "evacuación"] == [6.89]


def test_mapa_pack_without_extra_prints_install_hint(monkeypatch, capsys):
    import sys

    from climauy import cli

    monkeypatch.setitem(sys.modules, "climauy.mapa.packbuild", None)  # import fails like a missing extra
    assert cli.main(["mapa-pack", "paysandu"]) == 2
    err = capsys.readouterr().err
    assert "pip install -e '.[mapa-pack]'" in err and len(err.strip().splitlines()) == 1


def test_level_list_is_grid_plus_off_grid_marks(tmp_path):
    pytest.importorskip("osmium")
    pytest.importorskip("rasterio")
    pytest.importorskip("shapefile")
    from climauy.mapa.packbuild import level_list

    cfg = copy.deepcopy(BASE)
    cfg["marks"].append({"label": "Seguridad", "short": "seguridad", "m": 6.0, "kind": "oficial", "source": SRC})
    _write_cfg(tmp_path, cfg)
    levels = level_list(load_city("villa", tmp_path))
    assert len(levels) == 37 + 1  # 3..12 every 0.25 m, plus 4.39; 6.00 already on the grid
    assert (4.39, False) in levels and (6.0, True) in levels
    assert [m for m, _ in levels] == sorted(m for m, _ in levels)
