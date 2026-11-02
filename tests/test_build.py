import html as htmllib
import math
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from climauy.catalog import load_catalog
from climauy.site.build import FOOTER_TEXT, build_site
from climauy.store import Forecast, IndexValue, Obs, Store

NOW = datetime(2026, 11, 1, 21, 5, tzinfo=timezone.utc)  # 18:05 in Montevideo
PAGES = ["index.html", "rio/index.html", "rio/metodologia/index.html", "datos/index.html"]
ALLOWED_ACRONYMS = {
    "INA", "ANA", "CTM", "SINAE", "CECOED", "INUMET", "NOAA", "RONI", "ONI", "CPC", "CARU", "DINAGUA",
    "UTC", "CSV", "JSON",
}


def _seed(store: Store) -> None:
    # Monte Caseros (represents Bella Unión): 12 years of one daily reading, high today.
    start = date(2014, 11, 1)
    days = (NOW.date() - start).days + 1
    rows = []
    for i in range(days):
        d = start + timedelta(days=i)
        v = 4.0 + 2.0 * math.sin(i / 58.0) + (2.5 if d >= date(2026, 10, 25) else 0.0)
        rows.append(Obs("ina_74", "level", datetime(d.year, d.month, d.day, 15, tzinfo=timezone.utc), v, "ina"))
    store.upsert_obs(rows)
    # Colón (represents Paysandú) stopped reporting on 25/10.
    store.upsert_obs([
        Obs("ina_80", "level", datetime(2026, 10, d, 15, tzinfo=timezone.utc), 5.0 + d / 100, "ina")
        for d in range(1, 26)
    ])
    # One archived INA forecast run for Paso de los Libres.
    issued = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    store.upsert_forecasts([
        Forecast("ina", "ina_72", "level", issued, issued + timedelta(days=k), q, 7.0 + k * 0.05 + off)
        for k in range(1, 14) for q, off in (("main", 0.0), ("inferior", -0.3), ("superior", 0.3))
    ])
    store.upsert_indices([
        IndexValue("RONI", "2026-JJA", 1.36), IndexValue("RONI", "2026-JAS", 1.69),
        IndexValue("ONI", "2026-JJA", 1.80), IndexValue("ONI", "2026-JAS", 2.16),
    ])


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("build")
    with Store(root / "t.sqlite") as store:
        _seed(store)
        written = build_site(store, load_catalog(), root / "out", NOW, base_url="/clima-uy")
    return root / "out", written


def _read(out, page):
    return (out / page).read_text(encoding="utf-8")


def _text(html: str) -> str:
    """Visible text: no tags, scripts or styles, entities decoded, spaces collapsed."""
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def _flat(html: str) -> str:
    """Text with tags removed but no space inserted, so 'a <b>b</b>' reads 'a b'."""
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", html)))


def test_build_outputs_exist(site):
    out, written = site
    for page in PAGES:
        assert (out / page).is_file(), page
        assert (out / page) in written
    assert (out / "static/css/site.css").is_file()
    assert (out / "static/js/morph.js").is_file()
    assert list((out / "static/fonts").glob("*.woff2"))


def test_footer_on_every_page(site):
    out, _ = site
    assert FOOTER_TEXT == (
        "Esto no es una alerta oficial. Para alertas y evacuaciones: SINAE y el CECOED de tu departamento."
    )
    for page in PAGES:
        html = _read(out, page)
        assert FOOTER_TEXT in _flat(html), page
        assert 'href="https://www.gub.uy/sistema-nacional-emergencias/"' in html, page


def test_no_banned_typography(site):
    out, _ = site
    for page in PAGES:
        html = _read(out, page)
        for bad in ("—", " · ", "→", "&mdash;", "&rarr;", " -- "):
            assert bad not in html, (page, bad)
        caps = set(re.findall(r"\b[A-ZÁÉÍÓÚÑÜ]{4,}\b", _text(html)))
        assert caps <= ALLOWED_ACRONYMS, (page, caps - ALLOWED_ACRONYMS)


def test_works_without_js(site):
    out, _ = site
    html = _read(out, "rio/index.html")
    # The sentence is in the static HTML, not injected.
    assert "El río está más alto en Bella Unión (medido en Monte Caseros)" in _flat(html)
    table = re.search(r'<table class="stations-table".*?</table>', html, flags=re.S)
    assert table, "station table missing"
    rows = re.findall(r"<tr\b", table.group(0))
    stations = [s for s in load_catalog()]
    assert len(rows) == len(stations) + 1  # header + one row per station
    home = _read(out, "index.html")
    assert "El Niño sigue activo" in _flat(home)


def test_uy_city_shows_measuring_station(site):
    out, _ = site
    flat = _flat(_read(out, "rio/index.html"))
    for city, station in [("Bella Unión", "Monte Caseros"), ("Salto", "Concordia"), ("Paysandú", "Colón"),
                          ("Artigas", "Quaraí")]:
        assert f"{city}, medido en {station}, enfrente" in flat, city
    assert "Fray Bentos: sin estación con datos al día" in flat


def test_no_gauge_without_any_data(site):
    out, _ = site
    html = _read(out, "rio/index.html")
    block = re.search(r'<li[^>]*id="estacion-fray_bentos".*?</li>', html, flags=re.S).group(0)
    assert "chart-gauge" not in block
    block = re.search(r'<li[^>]*id="estacion-ina_74".*?</li>', html, flags=re.S).group(0)
    assert "chart-gauge" in block


def test_unvalidated_lag_title(site):
    out, _ = site
    flat = _flat(_read(out, "rio/index.html"))
    assert "Lo que tardó el agua en crecidas pasadas (no validado como pronóstico)" in flat
    assert "No validado: no hay crecidas para estimar el rezago." in flat


def test_forecast_status_and_glofas_label(site):
    out, _ = site
    flat = _flat(_read(out, "rio/index.html"))
    assert "acumulando datos desde el 1 de noviembre de 2026: 1 pronóstico guardado" in flat
    assert "acumulando datos desde el 1 de noviembre de 2026: 0 pronósticos guardados" in flat
    listing = re.search(r'<ul class="verification">.*?</ul>', _read(out, "rio/index.html"), flags=re.S).group(0)
    assert "corrida" not in listing
    assert flat.count("Modelo global (GloFAS), sin corregir para este río") >= 2


def test_rio_sections_in_order(site):
    out, _ = site
    html = _read(out, "rio/index.html")
    titles = re.findall(r"<h2[^>]*>(.*?)</h2>", html, flags=re.S)
    assert [_text(t) for t in titles] == [
        "El río hoy", "Estación por estación", "Lo que viene", "Esta crecida contra las anteriores",
        "Lo que surge de unir los datos", "Datos",
    ]
    assert ("Página generada el 1 de noviembre de 2026 a las 18:05, hora de Montevideo. "
            "Último dato del río: 1 de noviembre de 2026 a las 12:00.") in _flat(html)
    assert 'href="/clima-uy/snapshots/2026-11-01/"' in html


def test_relative_links_under_base_url(site):
    out, _ = site
    for page in PAGES:
        html = _read(out, page)
        for attr, url in re.findall(r'\b(href|src)="([^"]*)"', html):
            if url.startswith(("https://", "http://", "mailto:", "#")):
                continue
            assert url.startswith("/clima-uy/"), (page, url)
            rel = url[len("/clima-uy/"):].split("#")[0]
            if rel.startswith(("snapshots/", "data/")):
                continue  # written by the publish step
            target = out / rel
            if url.endswith("/") or rel == "":
                target = target / "index.html"
            assert target.is_file(), (page, url)


def test_home_enso_tables(site):
    out, _ = site
    flat = _flat(_read(out, "index.html"))
    assert "julio-septiembre de 2026" in flat
    assert "+1,69" in flat and "+2,16" in flat
    assert "Ojo: se espera una segunda ventana de lluvias en marzo a mayo de 2027" in flat
    assert "INUMET" in flat
    assert 'href="/clima-uy/rio/"' in _read(out, "index.html")


def test_build_with_empty_store(tmp_path):
    with Store(tmp_path / "e.sqlite") as store:
        written = build_site(store, load_catalog(), tmp_path / "out", NOW, base_url="")
    assert (tmp_path / "out" / "rio" / "index.html") in written
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert 'href="/rio/"' in html


def test_cli_build(tmp_path, monkeypatch):
    from climauy.cli import main

    with Store(tmp_path / "c.sqlite") as store:
        _seed(store)
    monkeypatch.setenv("CLIMAUY_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("CLIMAUY_DB", str(tmp_path / "c.sqlite"))
    monkeypatch.setenv("CLIMAUY_OUT", str(tmp_path / "site"))
    assert main(["build"]) == 0
    html = (tmp_path / "site" / "rio" / "index.html").read_text(encoding="utf-8")
    assert 'href="/clima-uy/static/css/site.css"' in html
    assert (tmp_path / "site" / "static" / "css" / "site.css").is_file()
    assert main(["build", "--base-url", ""]) == 0
    assert 'href="/static/css/site.css"' in (tmp_path / "site" / "index.html").read_text(encoding="utf-8")


DISCLAIMER = "Esto no es una alerta oficial. Alertas y evacuaciones: SINAE y el CECOED de tu departamento."


def test_disclaimer_near_the_top(site):
    out, _ = site
    for page, before in [("rio/index.html", 'class="chart chart-wide chart-profile'), ("index.html", 'class="go"')]:
        html = _read(out, page)
        i = html.index('class="top-warning"')
        assert i < html.index(before), page
        assert DISCLAIMER in _flat(html[i:i + 600]), page
        assert 'href="https://www.gub.uy/sistema-nacional-emergencias/"' in html[i:i + 600]


def test_station_meta_names_the_source_and_cota_source(site):
    out, _ = site
    html = _read(out, "rio/index.html")
    block = re.search(r'<li[^>]*id="estacion-ina_74".*?</li>', html, flags=re.S).group(0)
    assert "dato del INA de las 12:00" in _flat(block)
    flat = _flat(html)
    assert flat.count("Cotas de alerta y evacuación: INA y Prefectura Naval Argentina.") >= 2
    assert "a5" not in flat


def test_stale_station_rendering(site):
    out, _ = site
    html = _read(out, "rio/index.html")
    block = _flat(re.search(r'<li[^>]*id="estacion-ina_80".*?</li>', html, flags=re.S).group(0))
    assert "Paysandú, medido en Colón, enfrente" in block
    assert "desactualizada desde el 25/10/2026" in block
    assert "más alto que el" not in block
    lede = re.search(r'<p class="lede">(.*?)</p>', html, flags=re.S).group(1)
    assert "Paysandú" not in lede and "Colón" not in lede
    table = re.search(r'<table class="stations-table".*?</table>', html, flags=re.S).group(0)
    row = re.search(r"<tr[^>]*>\s*<th scope=\"row\">Paysandú.*?</tr>", table, flags=re.S).group(0)
    assert "desactualizada desde el 25/10/2026" in _flat(row)
    empty = re.search(r"<tr[^>]*>\s*<th scope=\"row\">Nueva Palmira.*?</tr>", table, flags=re.S).group(0)
    assert "sin datos" in _flat(empty)


def test_city_jump_list(site):
    out, _ = site
    html = _read(out, "rio/index.html")
    nav = re.search(r'<nav class="cities".*?</nav>', html, flags=re.S).group(0)
    hrefs = re.findall(r'href="#([^"]+)"', nav)
    assert hrefs == ["estacion-ana_77500000", "estacion-ina_74", "estacion-ina_79", "estacion-ina_80",
                     "estacion-fray_bentos"]
    for h in hrefs:
        assert f'id="{h}"' in html


def test_season_captions(site):
    out, _ = site
    html = _read(out, "rio/index.html")
    def caption(sid):
        fig = re.search(rf'<figure class="season" id="temporadas-{sid}".*?</figure>', html, flags=re.S).group(0)
        return _flat(re.search(r"<figcaption>.*?</figcaption>", fig, flags=re.S).group(0))
    assert caption("ina_74") == "Cota de alerta: INA / Prefectura Naval Argentina."
    assert "no tiene cota oficial de alerta" in caption("ana_77500000")


def test_outlook_missing_cells(site):
    out, _ = site
    html = _read(out, "index.html")
    table = re.search(r'<table class="outlook-table".*?</table>', html, flags=re.S).group(0)
    assert _flat(table).count("sin dato") == 8  # NDJ and DJF for the four regions besides Norte
    flat = _flat(html)
    assert "Para noviembre-enero y diciembre-febrero el boletín publicado solo da el dato del norte." in flat
    assert "elobservador.com.uy" in html


def test_datos_page_months_and_cite(site):
    out, _ = site
    html = _read(out, "datos/index.html")
    flat = _flat(html)
    assert "Lecturas de noviembre de 2026 (CSV)" in flat
    assert "https://sebastian-reolon.github.io/clima-uy/snapshots/2026-11-01/" in flat


def test_record_station_meta(site):
    # Monte Caseros today is above every day of its seeded history.
    out, _ = site
    html = _read(out, "rio/index.html")
    block = _flat(re.search(r'<li[^>]*id="estacion-ina_74".*?</li>', html, flags=re.S).group(0))
    assert "el valor más alto desde 1990" in block
    assert "100%" not in _text(html.split("<svg")[0]) and "más que el 100%" not in _flat(html)
