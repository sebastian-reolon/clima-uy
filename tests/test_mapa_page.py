"""The city flood map page (/rio/mapa/<slug>/), built from a tiny synthetic city."""
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from climauy.catalog import load_catalog
from climauy.mapa.config import load_city, today_level
from climauy.mapa.gfm import GfmResult
from climauy.mapa.pack import load_pack, write_pack
from climauy.publish import scan_tree
from climauy.site import build as B
from climauy.site.build import build_site
from climauy.site.mapa_svg import level_path
from climauy.store import Obs, Store

NOW = datetime(2026, 11, 1, 21, 5, tzinfo=timezone.utc)
X0, Y0 = 400000, 6420000
SRC = {"url": "https://example.org/informe.pdf", "date": "2026-10-22", "name": "Informe de prueba"}
GRID = [3.0 + 0.25 * i for i in range(9)]  # 3 to 5 m
MARK_LEVELS = [4.39, 4.8]
PAYSANDU_NOTES = {
    "slope": "con el río alto, la pendiente puede mover el borde unos 40 cm hacia el norte o el sur.",
    "zero": ("El cero de la escala de Paysandú se tomó del [relevamiento INA-CARU de 2019], ajustado con "
             "cuatro puntos medidos en el puerto. Puede estar corrido unos 35 cm."),
}


def _config(today: bool = True) -> dict:
    return {
        "slug": "paysandu", "name": "Paysandú",
        "frame_utm": [X0, Y0, X0 + 2000, Y0 + 3000],
        "phone_frame_utm": [X0 + 200, Y0, X0 + 1400, Y0 + 3000],
        "dtm_urls": ["https://example.org/CU_Remesa_09/tile.tif"],
        "zero_m": 0.43, "zero_source": SRC, "range_m": [3, 5],
        "river": {"flat_tol_m": 0.01, "note": "test"},
        "marks": [
            {"label": "Alerta (CARU)", "short": "alerta", "m": 4.39, "kind": "oficial", "source": SRC},
            {"label": "Evacuación (CARU)", "short": "evacuación", "m": 4.8, "kind": "oficial", "source": SRC},
        ],
        "today": ({"station_id": "ina_80", "offset_m": 1.18, "offset_range": [1.14, 1.2], "source": SRC}
                  if today else None),
        "sinae_check": {"m": 4.8, "people": 2105, "source": SRC},
        "place_labels": [{"name": "Centro", "x": X0 + 1500, "y": Y0 + 1500, "kind": "lugar"}],
        "notes": PAYSANDU_NOTES,
    }


def _square(m: float) -> list[int]:
    s = int(100 + (m - 3) * 200)  # grows with the level
    return [100, 100, 100 + s, 100, 100 + s, 100 + s, 100, 100 + s]


def _level(m: float, grid: bool) -> dict:
    pop = int((m - 3) * 400)
    zones = [["Norte", pop // 2], ["Sur", pop - pop // 2]] if pop else []
    return {"m": m, "area_ha": 10.0 + (m - 3) * 20, "pop": pop, "zones": zones, "poly": [_square(m)], "grid": grid}


def _pack() -> dict:
    levels = [_level(m, True) for m in GRID] + [_level(m, False) for m in MARK_LEVELS]
    return {
        "frame": [X0, Y0, X0 + 2000, Y0 + 3000],
        "layers": {
            "river": [[0, 0, 80, 0, 80, 3000, 0, 3000]],
            "blocks": [[1200, 1200, 1300, 1200, 1300, 1300, 1200, 1300]],
            "streets_main": [[100, 1500, 1900, 1500]], "streets_minor": [[1000, 100, 1000, 2900]],
            "contours": [{"m": 4.0, "lines": [[300, 100, 300, 2900]]}],
            "labels": [{"name": "Norte", "x": 1000, "y": 2500}],
        },
        "levels": sorted(levels, key=lambda lv: lv["m"]),
        "meta": {"sources": [{"what": "IDE Uruguay, MDT 1 m", "url": "https://example.org/CU_Remesa_09/"}]},
    }


def _root(tmp_path: Path, today: bool = True, with_salto: bool = True) -> Path:
    root = tmp_path / "mapa"
    (root / "paysandu").mkdir(parents=True)
    (root / "paysandu" / "config.json").write_text(json.dumps(_config(today)), encoding="utf-8")
    write_pack(root / "paysandu" / "pack.json.gz", _pack())
    if with_salto:  # a config without a pack: no page, no link
        salto = dict(_config(), slug="salto", name="Salto")
        (root / "salto").mkdir()
        (root / "salto" / "config.json").write_text(json.dumps(salto), encoding="utf-8")
    return root


def _seed_colon(store: Store, fresh: bool, value: float = 5.56) -> None:
    last = NOW.date() if fresh else NOW.date() - timedelta(days=7)
    store.upsert_obs([
        Obs("ina_80", "level", datetime(d.year, d.month, d.day, 15, tzinfo=timezone.utc), value, "ina")
        for d in (last - timedelta(days=k) for k in range(30))
    ])


GFM_OK = GfmResult("ok", "GFM_ITEM", datetime(2026, 11, 1, 9, 5, tzinfo=timezone.utc),
                   [[0, 0, 1000, 0, 1000, 1000, 0, 1000], [200, 200, 300, 200, 300, 300, 200, 300]])


def _build(tmp_path: Path, fresh: bool, today: bool, gfm: GfmResult) -> Path:
    calls = []

    def fetch(frame, now, cache_path):
        calls.append((tuple(frame), now, Path(cache_path)))
        return gfm

    with Store(tmp_path / "t.sqlite") as store:
        _seed_colon(store, fresh)
        build_site(store, load_catalog(), tmp_path / "out", NOW, base_url="/clima-uy",
                   mapa_root=_root(tmp_path, today=today), gfm_fetch=fetch)
    assert calls and calls[0][2] == tmp_path / "out" / "rio/mapa/paysandu/satelite.json"
    return tmp_path / "out"


@pytest.fixture(scope="module")
def fresh(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("fresh"), fresh=True, today=True, gfm=GFM_OK)


@pytest.fixture(scope="module")
def stale(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("stale"), fresh=False, today=True, gfm=GfmResult("none", None, None, []))


@pytest.fixture(scope="module")
def no_today(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("null"), fresh=True, today=False, gfm=GfmResult("error", None, None, []))


def _page(out: Path) -> str:
    return (out / "rio/mapa/paysandu/index.html").read_text(encoding="utf-8")


def _flat(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def _h1(html: str) -> str:
    return _flat(re.search(r"<h1>(.*?)</h1>", html, flags=re.S).group(1))


# ---------------------------------------------------------------- today_level

def test_today_level_fresh_stale_and_null(tmp_path):
    root = _root(tmp_path, with_salto=False)
    cfg = load_city("paysandu", root)
    with Store(tmp_path / "a.sqlite") as store:
        _seed_colon(store, fresh=True)
        t = today_level(store, cfg, NOW)
    assert t is not None and abs(t.m - 4.38) < 1e-9
    assert t.station_name == "Colón" and t.when_utc == datetime(2026, 11, 1, 15, tzinfo=timezone.utc)
    with Store(tmp_path / "b.sqlite") as store:
        _seed_colon(store, fresh=False)
        assert today_level(store, cfg, NOW) is None
        assert today_level(store, load_city("paysandu", _root(tmp_path / "n", today=False)), NOW) is None
    with Store(tmp_path / "c.sqlite") as store:
        assert today_level(store, cfg, NOW) is None  # no readings at all


# ---------------------------------------------------------------- page and data

def test_people_wording():
    assert [B.people(n) for n in (0, 14, 15, 21, 296, 998, 1049, 2105, 12345)] == [
        "menos de 15", "menos de 15", "unas 20", "unas 20", "unas 300", "unas 1.000", "unas 1.000",
        "unas 2.100", "unas 12.300"]


def test_page_and_sheet(fresh):
    html = _page(fresh)
    footer = re.search(r'<footer class="pie">.*?</footer>', html, flags=re.S).group(0)
    assert "Hoja 4 de 7" in _flat(footer)
    for name in ("IDE Uruguay", "Copernicus GFM", "WorldPop", "OpenStreetMap", "INE", "INA", "CARU"):
        assert name in _flat(footer), name
    assert "© colaboradores de OpenStreetMap" in _flat(html)
    assert 'href="/clima-uy/rio/"' in html  # crumb back to /rio/
    assert "<title>" in html and "Paysandú" in html


def test_h1_with_today(fresh):
    # Today 5.56 - 1.18 = 4.38 m, counted at the nearest grid level (4.5 m: 600 people).
    # Evacuation 4.8 m is a mark level: 720 people.
    assert _h1(_page(fresh)) == ("Con el río como hoy, unas 600 personas de Paysandú viven en terreno más bajo "
                                 "que el agua. A 4,80 m, la cota de evacuación, serían unas 720.")
    caj = re.search(r'<dl class="cajetin">.*?</dl>', _page(fresh), flags=re.S).group(0)
    flat = _flat(caj)
    assert "4,38 m, estimado desde Colón, 1 nov" in flat
    assert "Sentinel-1, 1 nov 09:05 UTC" in flat
    for v in ("IDE, grilla de 1 m", "WorldPop 2026, grilla de 100 m", "estimación, no observación"):
        assert v in flat
    assert "Los cálculos van cada 25 cm; la altura de hoy se toma en el más cercano." in _flat(_page(fresh))


@pytest.mark.parametrize("fixture", ["stale", "no_today"])
def test_fallback_h1_without_today(fixture, request):
    html = _page(request.getfixturevalue(fixture))
    assert _h1(html) == ("A 4,80 m, la cota de evacuación, unas 720 personas de Paysandú vivirían en terreno "
                         "más bajo que el agua.")
    assert "sin estimación al día" in _flat(html)
    staff = re.search(r'<svg class="staff".*?</svg>', html, flags=re.S).group(0)
    assert ">hoy" not in staff and 'class="mkt hoy"' not in staff
    assert ">hoy " not in re.search(r'<ul class="marcas".*?</ul>', html, flags=re.S).group(0)


def test_niveles_json(fresh):
    data = json.loads((fresh / "rio/mapa/paysandu/niveles.json").read_text(encoding="utf-8"))
    grid = [lv for lv in data["levels"] if lv["grid"]]
    assert [lv["m"] for lv in grid] == GRID
    assert sorted(lv["m"] for lv in data["levels"] if not lv["grid"]) == MARK_LEVELS
    for lv in data["levels"]:
        assert {"m", "d", "pop", "ha", "z", "grid"} <= set(lv)
    assert data["lo"] == 3 and data["hi"] == 5
    # Only what the pack and config fix: the file stays byte-stable between runs.
    assert "start" not in data
    assert {mk["label"]: mk["m"] for mk in data["marks"]} == {"Alerta (CARU)": 4.39, "Evacuación (CARU)": 4.8}
    # Today's height and mark ride on the page.
    ul = re.search(r'<ul class="marcas"[^>]*>', _page(fresh)).group(0)
    assert 'data-start="4.38"' in ul and 'data-hoy-m="4.38"' in ul and 'data-hoy-label="Hoy, estimado desde Colón"' in ul


def test_niveles_json_is_the_same_with_or_without_today(fresh, stale):
    a = (fresh / "rio/mapa/paysandu/niveles.json").read_bytes()
    assert a == (stale / "rio/mapa/paysandu/niveles.json").read_bytes()
    ul = re.search(r'<ul class="marcas"[^>]*>', _page(stale)).group(0)
    assert 'data-start="4.8"' in ul and "data-hoy" not in ul


def test_no_js_draws_the_start_level(fresh, stale, tmp_path):
    cfg = load_city("paysandu", _root(tmp_path, with_salto=False))
    pack = load_pack("paysandu", tmp_path / "mapa")
    by_m = {lv["m"]: lv for lv in pack.levels}
    for out, m in ((fresh, 4.5), (stale, 4.8)):  # today's nearest grid level; else the evacuation mark
        html = _page(out)
        wide = re.search(r'<svg class="mapa" id="mapa-wide".*?</svg>', html, flags=re.S).group(0)
        d = re.search(r'<path class="est" d="([^"]*)"', wide).group(1)
        assert d and d == level_path(pack, cfg, by_m[m], "wide")
        rows = re.search(r'<tbody data-out="zones">(.*?)</tbody>', html, flags=re.S).group(1)
        assert rows.count("<tr>") == 2 and "Norte" in rows
        assert "<noscript>" in html and "hace falta JavaScript" in _flat(re.search(
            r"<noscript>(.*?)</noscript>", html, flags=re.S).group(1))
    movil = re.search(r'<svg class="mapa movil" id="mapa-movil".*?</svg>', _page(fresh), flags=re.S)
    assert movil and "mapa-movil-hatch" in movil.group(0)


def test_satellite_lines(fresh, stale, no_today):
    sat = lambda out: _flat(re.search(r"<h2>Hoy en el satélite</h2>(.*?)</section>", _page(out), flags=re.S).group(1))
    ok = sat(fresh)
    assert "Última pasada de Sentinel-1: 1 de noviembre, 09:05 UTC." in ok
    assert "Fuera del cauce el radar vio unas 99 ha de agua." in ok  # 100 ha minus a 1 ha hole
    assert "No hay pasadas de Sentinel-1 sobre Paysandú en los últimos 10 días." in sat(stale)
    assert "No se pudo consultar el satélite en esta actualización." in sat(no_today)
    assert '<path class="obs" d=""' in _page(no_today)


def test_satellite_view_stale_and_empty():
    when = datetime(2026, 10, 30, 9, 5, tzinfo=timezone.utc)
    v = B._satellite_view(GfmResult("ok", "x", when, [], stale=True), "Paysandú")
    text = " ".join(v["lines"])
    assert "Última pasada disponible de Sentinel-1: 30 de octubre, 09:05 UTC." in text
    assert "Fuera del cauce el radar no vio agua." in text
    assert "No se pudo consultar el satélite en esta actualización; se muestra la última pasada guardada." in text
    assert v["cajetin"] == "Sentinel-1, 30 oct 09:05 UTC"


def test_links_only_for_cities_with_a_pack(fresh):
    home = (fresh / "index.html").read_text(encoding="utf-8")
    rio = (fresh / "rio/index.html").read_text(encoding="utf-8")
    for html in (home, rio):
        assert html.count(">ver el mapa</a>") == 1
        assert 'href="/clima-uy/rio/mapa/paysandu/">ver el mapa</a>' in html
    assert not (fresh / "rio/mapa/salto/index.html").exists()


def test_home_names_each_city_map_under_the_table(fresh):
    home = (fresh / "index.html").read_text(encoding="utf-8")
    line = ('<p class="mas"><a href="/clima-uy/rio/mapa/paysandu/">Mapa de crecida de Paysandú: '
            'qué terreno y cuánta gente queda bajo el agua a cada altura del río</a></p>')
    assert home.count(line) == 1
    assert home.index("</tbody></table></div>\n" + line) < home.index("<h2>Perfil del río hoy</h2>")
    assert home.count("Mapa de crecida de") == 1


def test_other_sheets_renumbered(fresh):
    for page, n in {"index.html": 1, "rio/index.html": 2, "rio/crecida/index.html": 3, "nino/index.html": 5,
                    "datos/index.html": 6, "rio/metodologia/index.html": 7}.items():
        footer = re.search(r'<footer class="pie">.*?</footer>', (fresh / page).read_text(encoding="utf-8"),
                           flags=re.S).group(0)
        assert f"Hoja {n} de 7" in _flat(footer), page


def test_output_has_no_infra(fresh):
    assert scan_tree(fresh) == []


def test_css_has_the_plancheta():
    css = (Path(B.SITE_DIR) / "static/v2/css/site.css").read_text(encoding="utf-8")
    assert ".plancheta{display:grid;grid-template-columns:minmax(0,1fr) 13rem" in css
    assert "@media (max-width:48em){" in css
    assert ".mapa.movil{display:none}" in css
    for f_only in (".cotas", ".corte", ".fb{", ".compacto", ".secl"):
        assert f_only not in css, f_only


def test_build_survives_a_raising_gfm(tmp_path):
    def boom(*a, **kw):
        raise RuntimeError("network down")

    with Store(tmp_path / "t.sqlite") as store:
        build_site(store, load_catalog(), tmp_path / "out", NOW, mapa_root=_root(tmp_path), gfm_fetch=boom)
    assert "No se pudo consultar el satélite" in _page(tmp_path / "out")


def test_snapshot_leaves_maps_out_and_links_them_live(fresh, tmp_path):
    import shutil

    from climauy.publish import SNAPSHOT_DATA, write_snapshot

    out = tmp_path / "out"
    shutil.copytree(fresh, out)
    for rel in SNAPSHOT_DATA:  # the data export is not part of this test
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).touch(exist_ok=True)
    snap = write_snapshot(out, NOW.date(), base_url="/clima-uy")
    assert not (snap / "rio/mapa").exists()  # Ruling 6
    home = (snap / "index.html").read_text(encoding="utf-8")
    assert 'href="/clima-uy/rio/mapa/paysandu/">ver el mapa</a>' in home


def test_notes_come_from_each_city_config(fresh, tmp_path):
    flat = _flat(_page(fresh))
    assert ("Supone el agua plana frente a toda la ciudad; con el río alto, la pendiente puede mover el borde "
            "unos 40 cm hacia el norte o el sur.") in flat
    assert ("El cero de la escala de Paysandú se tomó del relevamiento INA-CARU de 2019, ajustado con cuatro "
            "puntos medidos en el puerto. Puede estar corrido unos 35 cm.") in flat
    assert '<a href="https://example.org/informe.pdf">relevamiento INA-CARU de 2019</a>' in _page(fresh)

    root = _root(tmp_path, with_salto=False)
    other_src = {"url": "https://example.org/otro.pdf", "date": "2020-02", "name": "Otro"}
    other = dict(_config(today=False), slug="villa", name="Villa", zero_source=other_src, notes={
        "slope": "acá el río casi no tiene pendiente frente a la ciudad.",
        "zero": "El cero de Villa sale de [otro relevamiento], con dos puntos. Puede estar corrido unos 20 cm.",
    })
    (root / "villa").mkdir()
    (root / "villa" / "config.json").write_text(json.dumps(other), encoding="utf-8")
    write_pack(root / "villa" / "pack.json.gz", _pack())
    with Store(tmp_path / "t.sqlite") as store:
        build_site(store, load_catalog(), tmp_path / "out", NOW, mapa_root=root,
                   gfm_fetch=lambda *a, **kw: GfmResult("none", None, None, []))
    html = (tmp_path / "out/rio/mapa/villa/index.html").read_text(encoding="utf-8")
    flat = _flat(html)
    assert "acá el río casi no tiene pendiente frente a la ciudad." in flat
    assert '<a href="https://example.org/otro.pdf">otro relevamiento</a>' in html
    assert '<a href="/clima-uy/rio/mapa/paysandu/">Paysandú</a>' in html  # the other written map
    for paysandu_only in ("40 cm", "35 cm", "INA-CARU", "en el puerto"):
        assert paysandu_only not in flat, paysandu_only


# ---------------------------------------------------------------- a city whose page fails

def _villa(root: Path, pack: dict) -> None:
    villa = dict(_config(today=False), slug="villa", name="Villa")
    (root / "villa").mkdir()
    (root / "villa" / "config.json").write_text(json.dumps(villa), encoding="utf-8")
    write_pack(root / "villa" / "pack.json.gz", pack)


def test_a_city_whose_page_fails_is_left_out(tmp_path, caplog):
    # pop is only checked for presence: this pack loads and then breaks people() in the view.
    bad = _pack()
    for lv in bad["levels"]:
        lv["pop"] = None
    root = _root(tmp_path, with_salto=False)
    _villa(root, bad)
    with Store(tmp_path / "t.sqlite") as store:
        _seed_colon(store, fresh=True)
        written = build_site(store, load_catalog(), tmp_path / "out", NOW, mapa_root=root,
                             gfm_fetch=lambda *a, **kw: GfmResult("none", None, None, []))
    out = tmp_path / "out"
    assert not (out / "rio/mapa/villa/index.html").exists()
    assert not (out / "rio/mapa/villa/niveles.json").exists()
    assert out / "rio/mapa/paysandu/index.html" in written
    assert "Villa" not in _flat(re.search(r'<p class="crumb">.*?</p>', _page(out), flags=re.S).group(0))
    for page in ("index.html", "rio/index.html", "rio/crecida/index.html", "nino/index.html", "datos/index.html"):
        assert (out / page).exists(), page
        assert "/rio/mapa/villa/" not in (out / page).read_text(encoding="utf-8"), page
    assert any("villa" in r.getMessage() and r.exc_info for r in caplog.records)


def test_any_error_in_a_city_view_leaves_no_link(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("drawing failed")

    monkeypatch.setattr(B.mapa_svg, "plan_svg", boom)
    with Store(tmp_path / "t.sqlite") as store:
        _seed_colon(store, fresh=True)
        build_site(store, load_catalog(), tmp_path / "out", NOW, mapa_root=_root(tmp_path),
                   gfm_fetch=lambda *a, **kw: GfmResult("none", None, None, []))
    out = tmp_path / "out"
    assert not (out / "rio/mapa/paysandu/index.html").exists()
    for page in ("index.html", "rio/index.html"):
        html = (out / page).read_text(encoding="utf-8")
        assert "ver el mapa" not in html and "/rio/mapa/" not in html, page


# ---------------------------------------------------------------- today outside the pack's range

def _build_at(tmp_path: Path, colon: float) -> Path:
    with Store(tmp_path / "t.sqlite") as store:
        _seed_colon(store, fresh=True, value=colon)
        build_site(store, load_catalog(), tmp_path / "out", NOW, base_url="/clima-uy",
                   mapa_root=_root(tmp_path, with_salto=False),
                   gfm_fetch=lambda *a, **kw: GfmResult("none", None, None, []))
    return tmp_path / "out"


def _readout(html: str) -> dict:
    return {k: _flat(re.search(rf'<dd[^>]*data-out="{k}">(.*?)</dd>', html, flags=re.S).group(1))
            for k in ("m", "pop", "ha")}


@pytest.fixture(scope="module")
def below(tmp_path_factory):
    return _build_at(tmp_path_factory.mktemp("below"), 3.68)  # 3.68 - 1.18 = 2.50 m, under lo = 3


@pytest.fixture(scope="module")
def above(tmp_path_factory):
    return _build_at(tmp_path_factory.mktemp("above"), 7.18)  # 6.00 m, over hi = 5


def test_today_below_the_range_draws_no_water(below):
    html = _page(below)
    assert _h1(html) == ("Con el río como hoy, menos de 15 personas de Paysandú viven en terreno más bajo "
                         "que el agua. A 4,80 m, la cota de evacuación, serían unas 720.")
    assert _readout(html) == {"m": "2,50 m", "pop": "menos de 15", "ha": "menos de 10 ha"}
    for ident in ("mapa-wide", "mapa-movil"):
        svg = re.search(rf'<svg class="mapa[^"]*" id="{ident}".*?</svg>', html, flags=re.S).group(0)
        assert '<path class="est" d=""' in svg and '<path class="estl" d=""' in svg
    flat = _flat(html)
    assert "Con el río por debajo de 3 m el plano no dibuja el agua: los cálculos empiezan en 3 m." in flat
    assert "la altura de hoy se toma en el más cercano" not in flat
    assert 'data-start="2.5"' in html and "2,50 m, estimado desde Colón" in flat


def test_below_the_range_people_round_up():
    assert [B._people_bound(n, "below") for n in (0, 14, 15, 21, 296, 998, 1001)] == [
        "menos de 15", "menos de 15", "menos de 20", "menos de 30", "menos de 300", "menos de 1.000",
        "menos de 1.100"]
    assert [B._people_bound(n, "above") for n in (21, 296, 1049)] == ["más de 20", "más de 290", "más de 1.000"]


def test_today_above_the_range_draws_the_top_level(above, tmp_path):
    html = _page(above)
    assert _h1(html) == ("Con el río como hoy, más de 800 personas de Paysandú viven en terreno más bajo "
                         "que el agua. A 4,80 m, la cota de evacuación, serían unas 720.")
    assert _readout(html) == {"m": "6,00 m", "pop": "más de 800", "ha": "más de 50 ha"}
    cfg = load_city("paysandu", _root(tmp_path, with_salto=False))
    pack = load_pack("paysandu", tmp_path / "mapa")
    top = next(lv for lv in pack.levels if lv["m"] == 5.0)
    wide = re.search(r'<svg class="mapa" id="mapa-wide".*?</svg>', html, flags=re.S).group(0)
    assert re.search(r'<path class="est" d="([^"]*)"', wide).group(1) == level_path(pack, cfg, top, "wide")
    rows = re.search(r'<tbody data-out="zones">(.*?)</tbody>', html, flags=re.S).group(1)
    assert re.findall(r'<th>(.*?)</th><td class="n">(.*?)</td>', rows) == [("Norte", "más de 400"), ("Sur", "más de 400")]
    flat = _flat(html)
    assert "Con el río por encima de 5 m el plano dibuja el agua a 5 m. Los cálculos llegan hasta 5 m." in flat
    assert "la altura de hoy se toma en el más cercano" not in flat


def _run_script(out: Path, hash_: str = "") -> dict:
    """Run the page script under node with a minimal DOM and the real niveles.json."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    html = _page(out)
    script = re.search(r"<script>\s*(\(function \(\) \{.*?)</script>", html, flags=re.S).group(1)
    ul = re.search(r'<ul class="marcas"([^>]*)>', html).group(1)
    attrs = dict(re.findall(r'data-([a-z-]+)="([^"]*)"', ul))
    data = (out / "rio/mapa/paysandu/niveles.json").read_text(encoding="utf-8")
    stub = """
function el(sub) {
  return {attrs: {}, dataset: {}, children: [], textContent: "", value: "", sub: sub || {},
    classList: {contains: function () { return false; }},
    setAttribute: function (k, v) { this.attrs[k] = v; }, addEventListener: function () {},
    querySelector: function (s) { return this.sub[s]; }, querySelectorAll: function () { return []; },
    appendChild: function (c) { this.children.push(c); }, removeChild: function (c) { this.children.splice(this.children.indexOf(c), 1); },
    get firstChild() { return this.children[0] || null; }};
}
var est = el(), estl = el(), cur = el();
var ul = el(); ul.dataset = {start: ATTRS.start, hoyM: ATTRS["hoy-m"], hoyLabel: ATTRS["hoy-label"]};
var staff = el({".cur": cur, ".hit": el()}); staff.dataset = {top: "20", bot: "620"};
var outs = {m: el(), pop: el(), ha: el(), mark: el(), zones: el()};
var nodes = {"svg.mapa": [el({".est": est, ".estl": estl})], "svg.staff": [staff], "input[data-in=m]": [el()],
  ".marcas button": [], "[data-out=m]": [outs.m], "[data-out=pop]": [outs.pop], "[data-out=ha]": [outs.ha],
  "[data-out=mark]": [outs.mark], "[data-out=zones]": [outs.zones]};
global.window = global;
global.location = {hash: HASH};
global.document = {querySelector: function (s) { return s === ".marcas" ? ul : null; },
  querySelectorAll: function (s) { return nodes[s] || []; }, createElement: function () { return el(); }};
global.fetch = function () { return Promise.resolve({ok: true, json: function () { return Promise.resolve(DATA); }}); };
setTimeout(function () {
  console.log(JSON.stringify({d: est.attrs.d, m: outs.m.textContent, pop: outs.pop.textContent,
    ha: outs.ha.textContent, zones: outs.zones.children.map(function (r) {
      return r.children.map(function (c) { return c.textContent; }); })}));
}, 0);
""".replace("ATTRS", json.dumps(attrs)).replace("HASH", json.dumps(hash_)).replace("DATA", data)
    res = subprocess.run([node], input=stub + script, capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_script_keeps_today_below_the_range_dry(below):
    got = _run_script(below)
    assert got["d"] == "" and got["m"] == "2,50 m"
    assert got["pop"] == "menos de 15" and got["ha"] == "menos de 10 ha"


def test_script_draws_the_top_level_above_the_range(above):
    data = json.loads((above / "rio/mapa/paysandu/niveles.json").read_text(encoding="utf-8"))
    top = next(lv for lv in data["levels"] if lv["m"] == 5)
    got = _run_script(above)
    assert got["d"] == top["d"] and got["m"] == "6,00 m"
    assert got["pop"] == "más de 800" and got["ha"] == "más de 50 ha"
    assert got["zones"] == [["Norte", "más de 400"], ["Sur", "más de 400"]]
    # a shared link past the range reads the same way
    assert _run_script(above, "#m=2")["pop"] == "menos de 15"


def test_script_in_range_matches_the_server(fresh):
    got = _run_script(fresh)
    assert got["m"] == "4,38 m" and got["pop"] == "unas 600"


# ---------------------------------------------------------------- legend, escaping, merged marks

def test_satellite_legend_only_with_water_to_draw(fresh, stale, no_today):
    item = "agua fuera del cauce vista por el satélite"
    assert item in _flat(_page(fresh))
    assert item not in _flat(_page(stale)) and item not in _flat(_page(no_today))


def test_mark_labels_are_escaped_and_merged_marks_keep_their_heights(tmp_path):
    root = _root(tmp_path, with_salto=False)
    cfg = _config()
    cfg["marks"] += [
        {"label": "Pico de mayo & <b>junio</b>", "short": "pico A", "m": 4.5, "kind": "pico", "source": SRC},
        {"label": "Pico de 2016", "short": "pico B", "m": 4.55, "kind": "pico", "source": SRC},
    ]
    cfg["sinae_check"] = dict(cfg["sinae_check"], m=4.5)
    (root / "paysandu" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    with Store(tmp_path / "t.sqlite") as store:
        _seed_colon(store, fresh=True)
        build_site(store, load_catalog(), tmp_path / "out", NOW, mapa_root=root,
                   gfm_fetch=lambda *a, **kw: GfmResult("none", None, None, []))
    html = _page(tmp_path / "out")
    assert "<b>junio</b>" not in html
    assert "En mayo &amp; &lt;b&gt;junio&lt;/b&gt;, con el río a 4,50 m" in html
    buttons = re.search(r'<ul class="marcas".*?</ul>', html, flags=re.S).group(0)
    assert '<button type="button" data-m="4.5" aria-pressed="false">pico A 4,50 y pico B 4,55</button>' in buttons
    staff = re.search(r'<svg class="staff".*?</svg>', html, flags=re.S).group(0)
    assert '>pico A 4,50 y <tspan x="96" y="' in staff and ">pico B 4,55</tspan></text>" in staff
