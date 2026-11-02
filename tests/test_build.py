import html as htmllib
import math
import re
from datetime import date, datetime, timedelta, timezone

from pathlib import Path

import pytest

from climauy.catalog import load_catalog
from climauy.site.build import FOOTER_TEXT, build_site
from climauy.store import Forecast, IndexValue, Obs, Store

NOW = datetime(2026, 11, 1, 21, 5, tzinfo=timezone.utc)  # 18:05 in Montevideo
PAGES = ["index.html", "rio/index.html", "rio/metodologia/index.html", "datos/index.html"]
# Linked from the nav before the page exists (the El Niño page comes in a later
# step of the redesign). Remove the entry once the page is built.
PENDING_PAGES = {"nino/"}
SHEETS = {"index.html": 1, "rio/index.html": 2, "datos/index.html": 4, "rio/metodologia/index.html": 5}
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
    # One GloFAS fetch for Uruguaiana (fetch time 18:26 in Montevideo), valid at local midnight.
    fetched = datetime(2026, 11, 1, 21, 26, 40, tzinfo=timezone.utc)
    store.upsert_forecasts([
        Forecast("glofas", "ana_77150000", "discharge", fetched, datetime(2026, 11, 1 + k, 3, tzinfo=timezone.utc),
                 q, 9000.0 + 100 * k + off)
        for k in range(1, 20) for q, off in (("median", 0), ("p25", -300), ("p75", 300), ("min", -600), ("max", 600))
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
        # The middle dot is allowed: Ruling 12 («INA · ANA · CTM») and the profile callouts use it.
        for bad in ("—", "–", "→", "&mdash;", "&ndash;", "&rarr;", " -- "):
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
    # Task 5: the El Niño sentence ("El Niño sigue activo") moves from the old home to /nino/.


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


def test_forecast_run_wording(site):
    out, _ = site
    flat = _flat(_read(out, "rio/index.html"))
    # INA publishes a run time; GloFAS does not, so the page says when it was downloaded.
    assert "Corrida del 1 de noviembre de 2026 a las 09:00, hora de Montevideo." in flat
    assert "Pronóstico bajado el 1 de noviembre de 2026 a las 18:26, hora de Montevideo." in flat
    assert flat.count("Corrida del") == 1


def test_alert_reading_says_reached_or_exceeded(tmp_path):
    with Store(tmp_path / "t.sqlite") as store:
        # Paso de los Libres exactly at its alert level (7,50 m per the catalog).
        alert = next(s for s in load_catalog() if s.id == "ina_72").alert_m
        store.upsert_obs([Obs("ina_72", "level", datetime(2026, 11, 1, 15, tzinfo=timezone.utc), alert, "ina")])
        build_site(store, load_catalog(), tmp_path / "out", NOW, base_url="/clima-uy")
    html = _read(tmp_path / "out", "rio/index.html")
    block = _flat(re.search(r'<li[^>]*id="estacion-ina_72".*?</li>', html, flags=re.S).group(0))
    assert "alcanzó o superó la cota de alerta" in block
    assert "1 estación alcanzó o superó su cota de alerta" in _flat(html)


def test_forecast_status_and_glofas_label(site):
    out, _ = site
    flat = _flat(_read(out, "rio/index.html"))
    assert "acumulando datos desde el 1 de noviembre de 2026: 1 pronóstico guardado" in flat
    assert "acumulando datos desde el 1 de noviembre de 2026: 0 pronósticos guardados" in flat
    # GloFAS is counted by local day of download (one seeded download -> 1 day).
    assert "acumulando datos desde el 1 de noviembre de 2026: 1 día de pronóstico guardado" in flat
    listing = re.search(r'<ul class="verification">.*?</ul>', _read(out, "rio/index.html"), flags=re.S).group(0)
    assert "corrida" not in listing
    assert flat.count("Modelo global (GloFAS), sin corregir para este río") >= 2


def test_rio_sections_in_order(site):
    out, _ = site
    html = _read(out, "rio/index.html")
    titles = re.findall(r"<h2[^>]*>(.*?)</h2>", html, flags=re.S)
    assert [_text(t) for t in titles] == [
        "El río hoy", "Estación por estación", "Lo que viene", "Esta crecida contra las anteriores",
        "Lo que surge de unir los datos", "Datos", "Notas",  # Notas comes from the sheet base
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
            if rel in PENDING_PAGES:
                continue
            target = out / rel
            if url.endswith("/") or rel == "":
                target = target / "index.html"
            assert target.is_file(), (page, url)


@pytest.mark.skip(reason="Task 5: the old El Niño home (index.html.j2) becomes /nino/; retarget there")
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
    assert 'href="/clima-uy/static/v2/css/site.css"' in html
    assert (tmp_path / "site" / "static" / "css" / "site.css").is_file()
    assert main(["build", "--base-url", ""]) == 0
    assert 'href="/static/v2/css/site.css"' in (tmp_path / "site" / "index.html").read_text(encoding="utf-8")


DISCLAIMER = "Esto no es una alerta oficial. Alertas y evacuaciones: SINAE y el CECOED de tu departamento."


def test_disclaimer_near_the_top(site):
    out, _ = site
    # Task 5: the old home's entry ("index.html", 'class="go"') returns when /nino/ is built.
    for page, before in [("rio/index.html", 'class="chart chart-wide chart-profile')]:
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


@pytest.mark.skip(reason="Task 5: the INUMET table moves from the old home to /nino/; retarget there")
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
    # Its seeded history starts in 2014: under 30 years, so the text says since when.
    assert "el valor más alto desde 2014" in block
    assert "desde 1990" not in block
    assert "100%" not in _text(html.split("<svg")[0]) and "más que el 100%" not in _flat(html)


COPERNICUS_NOTICE = "Contains modified Copernicus Emergency Management Service information 2026"
DINAGUA_URL = ("https://www.gub.uy/ministerio-ambiente/sites/ministerio-ambiente/files/documentos/publicaciones/"
               "Informe_situacion_pronostico_9.pdf")
SINAE_2024_URL = ("https://www.gub.uy/sistema-nacional-emergencias/comunicacion/noticias/"
                  "informe-situacion-inundaciones-fin-emergencia-080124")


def test_glofas_figcaptions_carry_attribution(site):
    out, _ = site
    html = _read(out, "rio/index.html")
    figures = re.findall(r'<figure class="forecast">.*?</figure>', html, flags=re.S)
    glofas = [f for f in figures if "<h3>GloFAS" in f]
    assert glofas, "no GloFAS figure"
    for fig in glofas:
        caption = re.search(r"<figcaption>.*?</figcaption>", fig, flags=re.S).group(0)
        assert 'href="https://open-meteo.com/"' in caption
        assert COPERNICUS_NOTICE in _flat(caption)
    for fig in figures:
        if "<h3>INA" in fig:
            assert COPERNICUS_NOTICE not in fig
    datos = _read(out, "datos/index.html")
    assert 'href="https://open-meteo.com/"' in datos
    assert COPERNICUS_NOTICE in _flat(datos)


def test_datos_explains_each_source(site):
    out, _ = site
    flat = _flat(_read(out, "datos/index.html"))
    section = flat[flat.index("Licencias y atribución"):flat.index("Cómo citar")]
    for name in ("ANA (Brasil)", "INA y Prefectura Naval Argentina", "CTM Salto Grande", "GloFAS", "NOAA CPC",
                 "INUMET"):
        assert name in section, name
    assert "se están verificando" not in section
    assert section.count("criterio de este proyecto, no un permiso") == 2  # INA y Prefectura, CTM
    assert section.count("Si piden retirarlos, se retiran") == 2
    assert section.count("los issues del repositorio") == 2
    assert 'href="https://github.com/sebastian-reolon/clima-uy/issues"' in _read(out, "datos/index.html")
    assert "no se ofrecen para descargar" not in section
    assert "dominio público" in section


def _city_block(html: str, station_id: str) -> str:
    start = html.index(f'id="estacion-{station_id}"')
    end = html.find('<li class="station', start)
    return html[start:end if end != -1 else len(html)]


@pytest.mark.parametrize("station_id,expected,other_source", [
    ("ina_79", "Cota de seguridad oficial en Salto: 11,40 m", True),
    ("ina_74", "Cota de seguridad oficial en Bella Unión: 6,30 m", True),
    ("ina_80", "Cota de seguridad oficial en Paysandú: 6,00 m", True),
    ("ana_77500000", "Cota de seguridad oficial en Artigas: 10,20 m", False),
    ("fray_bentos", "Cota de seguridad oficial en Fray Bentos: 3,90 m", False),
])
def test_uy_city_cites_official_cota(site, station_id, expected, other_source):
    out, _ = site
    block = _city_block(_read(out, "rio/index.html"), station_id)
    flat = _flat(block)
    assert expected in flat
    assert ("SINAE publicó otro valor" in flat) == other_source
    if other_source:
        assert f'href="{SINAE_2024_URL}"' in block
    if station_id == "ana_77500000":
        assert "CECOED de Artigas" in flat and "15 de noviembre de 2023" in flat
    else:
        assert f'href="{DINAGUA_URL}"' in block and "3 de agosto de 2026" in flat
    if station_id != "fray_bentos":
        assert "no se comparan" in flat
    # Never drawn on the opposite-bank gauge.
    value = expected.rsplit(": ", 1)[1].replace(" m", "")
    for svg in re.findall(r"<svg.*?</svg>", block, flags=re.S):
        assert value not in svg and value.rstrip("0").rstrip(",") + " m" not in _flat(svg)


def test_city_cotas_never_change_any_chart(site, tmp_path):
    """The official Uruguayan cotas are text only: every SVG on the river page is identical without them."""
    out, _ = site
    empty = tmp_path / "refs.json"
    empty.write_text('{"cities": {}}', encoding="utf-8")
    with Store(tmp_path / "t.sqlite") as store:
        _seed(store)
        build_site(store, load_catalog(), tmp_path / "out", NOW, base_url="/clima-uy", refs_path=empty)
    with_refs = _read(out, "rio/index.html")
    without = _read(tmp_path / "out", "rio/index.html")
    svgs = lambda h: re.findall(r"<svg.*?</svg>", h, flags=re.S)
    assert svgs(with_refs) == svgs(without)
    assert "city-ref" in with_refs and "city-ref" not in without


def test_author_credit_on_every_page(site):
    out, _ = site
    for page in PAGES:
        html = _read(out, page)
        footer = re.search(r'<footer class="pie">.*?</footer>', html, flags=re.S).group(0)
        assert 'Hecho por <a href="https://github.com/sebastian-reolon">Sebastián Reolon</a>' in footer, page
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "Hecho por [Sebastián Reolon](https://github.com/sebastian-reolon)." in readme


def test_datos_explains_partial_day_and_latest_fields(site):
    out, _ = site
    flat = _flat(_read(out, "datos/index.html"))
    assert "El día en curso no está: se agrega cuando termina." in flat
    assert "last_data_utc" in flat and "latest_source" in flat


# ---------------------------------------------------------------- drawing-sheet base

REPO = Path(__file__).resolve().parents[1]
SITE_STATIC = REPO / "climauy" / "site" / "static"


def test_pending_pages_are_still_missing(site):
    out, _ = site
    for rel in PENDING_PAGES:
        assert not (out / rel / "index.html").exists(), f"{rel} exists now: remove it from PENDING_PAGES"


def test_sheet_css_and_base_keep_the_global_rules():
    css = (SITE_STATIC / "v2/css/site.css").read_text(encoding="utf-8")
    base = (REPO / "climauy/site/templates/base.html.j2").read_text(encoding="utf-8")
    assert "html,body{overflow-x:clip}" in css
    assert ":focus-visible{outline:2px solid var(--focus)" in css
    for text in (css.split("*/", 1)[1], base):  # the Hallmark header line uses middle dots on purpose
        for bad in ("—", " -- ", " · ", "&mdash;"):
            assert bad not in text, bad
        assert not re.search(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", text)
        assert "@" not in text.replace("@font-face", "").replace("@media", "")


def test_every_page_links_the_sheet_stylesheet(site, tmp_path):
    from climauy.site.build import render_snapshot_index

    out, _ = site
    index = render_snapshot_index(tmp_path, [date(2026, 11, 1)], base_url="/clima-uy")
    for html in [_read(out, p) for p in PAGES] + [index.read_text(encoding="utf-8")]:
        assert '<link rel="stylesheet" href="/clima-uy/static/v2/css/site.css">' in html
        assert 'href="/clima-uy/static/css/site.css"' not in html
        assert 'href="/clima-uy/static/v2/fonts/barlow-400-latin.woff2"' in html  # preload
        assert '<div class="hoja"><div class="marco">' in html


def test_sheet_assets_copied_and_old_ones_untouched(site):
    out, _ = site
    css = (out / "static/v2/css/site.css").read_text(encoding="utf-8")
    assert css.startswith("/* Hallmark · macrostructure: drawing sheet · tone: technical · design-system: design.md */")
    for font in re.findall(r'url\("\.\./fonts/([^"]+)"\)', css):
        assert (out / "static/v2/fonts" / font).is_file(), font
    assert css.count("@font-face") == css.count("font-display:swap") > 0
    assert (out / "static/v2/fonts/OFL-barlow.txt").is_file()
    # A Spanish page only downloads the latin subsets (latin-ext loads on demand).
    latin = list((out / "static/v2/fonts").glob("*-latin.woff2"))
    assert latin and sum(f.stat().st_size for f in latin) < 160_000
    # Frozen snapshots link the old stylesheet and fonts: they are published byte for byte as on main.
    frozen = _main_files("climauy/site/static/css", "climauy/site/static/fonts")
    assert "climauy/site/static/css/site.css" in frozen
    on_disk = {str(p.relative_to(REPO)) for d in ("css", "fonts") for p in (SITE_STATIC / d).iterdir()}
    assert on_disk == set(frozen)
    for path, data in frozen.items():
        rel = path.removeprefix("climauy/site/static/")
        assert (SITE_STATIC / rel).read_bytes() == data, path
        assert (out / "static" / rel).read_bytes() == data, path


def _main_files(*dirs: str) -> dict[str, bytes]:
    """Bytes of every file under the given repo dirs as committed on main; skips without git or main."""
    import shutil
    import subprocess

    if not shutil.which("git"):
        pytest.skip("git is not available to read the frozen assets from main")
    git = lambda *a, **kw: subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, **kw)
    if git("rev-parse", "--verify", "--quiet", "main^{commit}").returncode != 0:
        pytest.skip("no main branch to compare the frozen assets with")
    paths = git("ls-tree", "-r", "--name-only", "main", "--", *dirs, check=True, text=True).stdout.split()
    return {p: git("show", f"main:{p}", check=True).stdout for p in paths}


def test_credit_and_disclaimer_on_every_sheet(site, tmp_path):
    from climauy.site.build import render_snapshot_index

    out, _ = site
    index = render_snapshot_index(tmp_path, [date(2026, 11, 1)], base_url="/clima-uy")
    for html in [_read(out, p) for p in PAGES] + [index.read_text(encoding="utf-8")]:
        notes = re.search(r'<ol class="notas">.*?</ol>', html, flags=re.S).group(0)
        assert FOOTER_TEXT in _flat(notes)
        assert '<a href="https://www.gub.uy/sistema-nacional-emergencias/">SINAE</a>' in notes
        footer = re.search(r'<footer class="pie">.*?</footer>', html, flags=re.S).group(0)
        assert 'Hecho por <a href="https://github.com/sebastian-reolon">Sebastián Reolon</a>' in footer
        assert 'href="https://github.com/sebastian-reolon/clima-uy"' in footer
        assert "Fuentes:" in _flat(footer) and "NOAA CPC" in _flat(footer)


def test_sheet_number_in_footer(site, tmp_path):
    from climauy.site.build import render_snapshot_index

    out, _ = site
    assert "Hoja 2 de 5" in _flat(_read(out, "rio/index.html"))
    for page, n in SHEETS.items():
        footer = re.search(r'<footer class="pie">.*?</footer>', _read(out, page), flags=re.S).group(0)
        assert f"Hoja {n} de 5" in _flat(footer), page
    index = render_snapshot_index(tmp_path, [date(2026, 11, 1)], base_url="/clima-uy")
    assert "Hoja" not in _flat(index.read_text(encoding="utf-8"))


def _nav(html: str) -> dict[str, bool]:
    nav = re.search(r"<nav\b.*?</nav>", html, flags=re.S).group(0)
    return {htmllib.unescape(label): bool(cur)
            for href, cur, label in re.findall(r'<a href="([^"]+)"( aria-current="page")?>([^<]+)</a>', nav)}


def test_nav_marks_the_current_sheet(site):
    out, _ = site
    nav = re.search(r"<nav\b.*?</nav>", _read(out, "rio/index.html"), flags=re.S).group(0)
    assert re.findall(r'href="([^"]+)"', nav) == ["/clima-uy/", "/clima-uy/nino/", "/clima-uy/datos/"]
    assert _nav(_read(out, "index.html")) == {"Río": True, "El Niño": False, "Datos": False}
    assert _nav(_read(out, "rio/index.html")) == {"Río": True, "El Niño": False, "Datos": False}
    assert _nav(_read(out, "rio/metodologia/index.html")) == {"Río": True, "El Niño": False, "Datos": False}
    assert _nav(_read(out, "datos/index.html")) == {"Río": False, "El Niño": False, "Datos": True}


# ---------------------------------------------------------------- home: river summary (sheet 1)

def _daily(sid: str, variable: str, source: str, values: dict[date, float]) -> list[Obs]:
    return [Obs(sid, variable, datetime(d.year, d.month, d.day, 15, tzinfo=timezone.utc), v, source)
            for d, v in values.items()]


def _last_days(values: list[float], end: date = date(2026, 11, 1)) -> dict[date, float]:
    """Daily values ending on `end`, oldest first."""
    return {end - timedelta(days=len(values) - 1 - i): v for i, v in enumerate(values)}


def _seed_home(store: Store) -> None:
    """_seed plus the stations the home tables read: Artigas (discharge), Salto, upstream, near Fray Bentos."""
    _seed(store)
    start = date(2014, 11, 1)
    n = (NOW.date() - start).days + 1
    # Quaraí (Artigas): a decade of daily ANA discharge, so the station is ranked in m³/s.
    store.upsert_obs(_daily("ana_77500000", "discharge", "ana", {
        start + timedelta(days=i): 20.0 + 15.0 * math.sin(i / 23.0) for i in range(n)}))
    # Concordia (Salto): a decade of levels, rising 0,30 m in the last 3 days to 9,30 m.
    hist = {start + timedelta(days=i): 7.0 + 1.5 * math.sin(i / 41.0) for i in range(n - 4)}
    hist.update(_last_days([9.0, 9.1, 9.2, 9.3]))
    store.upsert_obs(_daily("ina_79", "level", "ina", hist))
    # Upstream main-river stations, short series (no percentile). Uruguaiana has none.
    store.upsert_obs(_daily("ina_69", "level", "ina", _last_days([8.4, 8.3, 8.2, 8.1, 8.0])))
    store.upsert_obs(_daily("ina_70", "level", "ina", _last_days([8.5, 8.4, 8.3, 8.2, 8.12])))
    store.upsert_obs(_daily("ina_71", "level", "ina", _last_days([8.3, 8.2, 8.1, 8.05, 7.98])))
    store.upsert_obs(_daily("ina_72", "level", "ina", _last_days([8.2, 8.1, 8.11, 7.9, 7.66])))  # above 7,50
    # Boca Gualeguaychú, the closest gauge to Fray Bentos: falling.
    store.upsert_obs(_daily("ina_84", "level", "ina", _last_days([2.4, 2.35, 2.3, 2.2, 2.1])))


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    root = tmp_path_factory.mktemp("home")
    with Store(root / "h.sqlite") as store:
        _seed_home(store)
        build_site(store, load_catalog(), root / "out", NOW, base_url="/clima-uy")
        from climauy.analysis.status import compute_status
        statuses = compute_status(store, load_catalog(), NOW)
    return _read(root / "out", "index.html"), statuses


def _table(html: str, cls: str | None = None, after: str | None = None) -> str:
    if after:
        html = html[html.index(after):]
    pattern = rf'<table class="{cls}">.*?</table>' if cls else r"<table>.*?</table>"
    return re.search(pattern, html, flags=re.S).group(0)


def _rows(table: str) -> list[tuple[str, str, list[str]]]:
    """(tr attributes, row header text, cell texts) of each body row."""
    body = re.search(r"<tbody>(.*?)</tbody>", table, flags=re.S).group(1)
    out = []
    for attrs, inner in re.findall(r"<tr([^>]*)>(.*?)</tr>", body, flags=re.S):
        th = _text(re.search(r"<th[^>]*>(.*?)</th>", inner, flags=re.S).group(1))
        cells = [_flat(c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", inner, flags=re.S)]
        out.append((attrs, th, cells))
    return out


def test_home_is_the_river_summary(home):
    from climauy.narrative import river_headline

    html, statuses = home
    h1, sub = river_headline(statuses, load_catalog())
    assert re.search(r'<section class="resp"><h1>(.*?)</h1>', html, flags=re.S).group(1) == htmllib.escape(h1, quote=False)
    assert sub and sub in _flat(html)
    titles = [_text(t) for t in re.findall(r"<h2[^>]*>(.*?)</h2>", html, flags=re.S)]
    assert titles == ["Ciudades uruguayas", "Perfil del río hoy", "Aguas arriba", "El Niño",
                      "Todas las estaciones", "Notas"]
    assert "El Niño 2026-27 en Uruguay" not in html  # the old home is not rendered (Task 5 moves it)


def test_home_cajetin(home):
    html, _ = home
    caj = re.search(r'<dl class="cajetin">(.*?)</dl>', html, flags=re.S).group(1)
    pairs = dict(zip([_flat(x) for x in re.findall(r"<dt>(.*?)</dt>", caj)],
                     [_flat(x) for x in re.findall(r"<dd>(.*?)</dd>", caj)]))
    assert pairs == {
        "Estado al": "2026-11-01 12:00 (UY)",
        "Actualización": "cada 3 horas",
        # Fresh: Monte Caseros, Quaraí, Concordia, Alvear, La Cruz, Yapeyú, Paso de los Libres, Boca
        # Gualeguaychú. Colón is stale; Fray Bentos has no gauge and is not counted.
        "Estaciones": "8 con dato, 29 sin",
        "Fuentes": "INA · ANA · CTM",
        "Historia": "desde 2014",
        "Carácter": "informativo, no oficial",
    }


def test_home_city_rows(home):
    from climauy.fmt import num, pct

    html, statuses = home
    by = {s.station_id: s for s in statuses}
    rows = _rows(_table(html, "ciu"))
    assert [r[1] for r in rows] == ["Bella Unión", "Artigas", "Salto", "Paysandú", "Fray Bentos"]
    bu, artigas, salto, paysandu, fray = rows
    # Bella Unión: Monte Caseros level, cota 7,50.
    mc = by["ina_74"]
    assert bu[2][:2] == ["Monte Caseros (AR)", f"{num(mc.level_m)} m"]
    assert bu[2][3].startswith(pct(mc.percentile_today))
    assert bu[2][4:] == ["7,50", num(7.5 - mc.level_m)]
    # Artigas: discharge, no cota, nothing missing to the alert.
    q = by["ana_77500000"]
    assert artigas[2][:2] == ["Quaraí (BR)", f"{num(q.latest_value, 0)} m³/s"]
    assert artigas[2][2] == ""  # its 3-day change is in %, not metres
    assert artigas[2][3].startswith(pct(q.percentile_today))
    assert artigas[2][4:] == ["", ""]
    assert "up" not in artigas[0]
    # Salto: rising, marked as such.
    assert salto[2] == ["Concordia (AR)", "9,30 m", "+0,30", salto[2][3], "11,00", "1,70"]
    assert 'class="up"' in salto[0]
    # Paysandú: Colón stopped on 25/10; the reading carries its date and nothing is said as current.
    assert paysandu[2][:3] == ["Colón (AR)", "5,25 m (al 25/10)", ""]
    assert paysandu[2][3:] == ["", "7,10", ""]
    assert "up" not in paysandu[0]
    # Fray Bentos: no public gauge, the nearest station with data instead.
    assert 'class="sin"' in fray[0]
    assert fray[2] == ["sin regla pública", "más cercana: Boca Gualeguaychú (AR) 2,10 m, baja"]


def test_home_city_table_labels_cells_for_phones(home):
    html, _ = home
    table = _table(html, "ciu")
    for label in ("Regla", "Nivel", "Δ 3 días", "Percentil", "Alerta", "Falta"):
        assert f'data-l="{label}"' in table
    assert '<span class="bar"><span style="width:' in table


def test_home_upstream_rows(home):
    html, _ = home
    rows = _rows(_table(html, after="<h2>Aguas arriba</h2>"))
    # Ruling 9: the four main-river stations with data right above Bella Unión's, downstream order.
    # Uruguaiana (no data) and the tributaries in between are skipped.
    assert [r[1] for r in rows] == ["Alvear", "La Cruz", "Yapeyú", "Paso de los Libres"]
    assert rows[3][2] == ["7,66 m", "-0,44", ""]
    assert 'href="/clima-uy/rio/"' in html[html.index("<h2>Aguas arriba</h2>"):html.index("<h2>El Niño</h2>")]


def test_home_enso_block(home):
    html, _ = home
    block = html[html.index("<h2>El Niño</h2>"):html.index("<h2>Todas las estaciones</h2>")]
    assert "Anomalía del Pacífico central, julio a septiembre de 2026, y perspectiva de INUMET." in _flat(block)
    kv = re.search(r'<dl class="kv">(.*?)</dl>', block, flags=re.S).group(1)
    pairs = list(zip([_flat(x) for x in re.findall(r"<dt>(.*?)</dt>", kv)],
                     [_flat(x) for x in re.findall(r"<dd>(.*?)</dd>", kv)]))
    assert pairs == [
        ("RONI (oficial NOAA)", "+1,69 °C"),
        ("ONI", "+2,16 °C"),
        ("Pico esperado", "noviembre y diciembre de 2026"),
        ("Lluvia norte, noviembre a enero", "60% sobre lo normal"),
        ("Segunda ventana", "marzo a mayo de 2027"),
    ]
    assert 'href="/clima-uy/nino/"' in block


def test_home_all_stations(home):
    html, _ = home
    block = html[html.index("<h2>Todas las estaciones</h2>"):]
    assert "<summary>Ver las 8 reglas en orden aguas abajo</summary>" in block
    rows = _rows(_table(block))
    assert [r[1] for r in rows] == [
        "Alvear AR", "La Cruz AR", "Yapeyú AR", "Paso de los Libres AR", "Quaraí BR · frente a Artigas",
        "Monte Caseros AR · frente a Bella Unión", "Concordia AR · frente a Salto", "Boca Gualeguaychú AR",
    ]
    libres = rows[3]
    assert 'class="al"' in libres[0]
    assert libres[2] == ["7,66 m", "-0,44", "", "7,50"]
    assert sum('class="al"' in r[0] for r in rows) == 1


def test_home_notes(home):
    html, _ = home
    notes = re.search(r'<ol class="notas">(.*?)</ol>', html, flags=re.S).group(1)
    items = [_flat(li).strip() for li in re.findall(r"<li>(.*?)</li>", notes, flags=re.S)]
    assert items == [
        FOOTER_TEXT,
        "Las cotas oficiales uruguayas se miden en la regla de cada ciudad: Bella Unión 6,30 m, Salto 11,40 m, "
        "Paysandú 6,00 m, Fray Bentos 3,90 m (DINAGUA, 3/8/2026). No se comparan con las reglas de enfrente.",
        "Artigas se mide en Quaraí, sobre el río Cuareim, en caudal. Esa estación no tiene cota de alerta.",
        "Los datos crudos de todas las fuentes se descargan en Datos.",
    ]
    assert f'href="{DINAGUA_URL}"' in notes
    assert '<a href="/clima-uy/datos/">Datos</a>' in notes


def test_home_embeds_both_profiles(home):
    html, _ = home
    assert html.count('class="perfil"') == 1
    assert html.count('class="perfil movil"') == 1
    sec = html[html.index("<h2>Perfil del río hoy</h2>"):html.index("<h2>Aguas arriba</h2>")]
    assert sec.index('class="perfil"') < sec.index('class="perfil movil"') < sec.index('class="ley"')


def test_home_with_empty_store(tmp_path):
    with Store(tmp_path / "e.sqlite") as store:
        build_site(store, load_catalog(), tmp_path / "out", NOW, base_url="")
    html = _read(tmp_path / "out", "index.html")
    flat = _flat(html)
    assert "No hay datos al día del río frente a las ciudades uruguayas." in flat
    caj = _flat(re.search(r'<dl class="cajetin">(.*?)</dl>', html, flags=re.S).group(1))
    assert "sin datos al día" in caj and "0 con dato, 37 sin" in caj
    rows = _rows(_table(html, "ciu"))
    assert [r[1] for r in rows] == ["Bella Unión", "Artigas", "Salto", "Paysandú", "Fray Bentos"]
    assert rows[0][2][1] == "sin datos"
    assert rows[4][2] == ["sin regla pública", "sin estación cercana con datos al día"]
    assert "No hay estaciones con datos al día." in flat
    assert "Lluvia norte" in flat  # the outlook is hand-loaded, not from the store
    assert "Todavía no hay índices de la NOAA cargados." in flat
