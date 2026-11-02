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
PAGES = ["index.html", "rio/index.html", "rio/crecida/index.html", "nino/index.html", "rio/metodologia/index.html",
         "datos/index.html"]
SHEETS = {"index.html": 1, "rio/index.html": 2, "rio/crecida/index.html": 3, "nino/index.html": 4,
          "datos/index.html": 5, "rio/metodologia/index.html": 6}
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
        # «—» is allowed only as the whole content of a table cell (the mockup's empty-cell mark).
        html = re.sub(r"(<td\b[^>]*>)—(</td>)", r"\1\2", _read(out, page))
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
    assert "El Niño sigue activo" in _flat(_read(out, "nino/index.html"))


def test_uy_city_shows_measuring_station(site):
    out, _ = site
    flat = _flat(_read(out, "rio/index.html"))
    for city, station in [("Bella Unión", "Monte Caseros"), ("Salto", "Concordia"), ("Paysandú", "Colón"),
                          ("Artigas", "Quaraí")]:
        assert f"{city}, medido en {station}, enfrente" in flat, city
    # No public gauge: said as on the home, with the nearest main-river gauge that has a level today.
    assert "Fray Bentos: sin regla pública" in flat
    block = _flat(_city_block(_read(out, "rio/index.html"), "fray_bentos"))
    assert "más cercana: Monte Caseros (AR)" in block


def test_nearest_gauge_for_fray_bentos_is_on_the_main_river():
    import dataclasses

    from climauy.analysis.status import StationStatus
    from climauy.site.build import _nearest_text

    cat = load_catalog()
    blank = StationStatus(
        station_id="", variable_used="level", latest_value=None, latest_time_utc=None, latest_source=None,
        stale=True, stale_since=None, level_change_3d_m=None, discharge_change_3d_pct=None, trend=None,
        trend_basis=None, percentile_today=None, percentile_3d_ago=None, alert_percentile=None, level_m=None,
        above_alert=None, above_evac=None, history_years=0.0, history_start_year=None,
    )
    by = {st.id: dataclasses.replace(blank, station_id=st.id) for st in cat}
    # Gualeguaychú (on its own river) is the closest gauge to Fray Bentos; Concepción del Uruguay is further.
    by["ina_99"] = dataclasses.replace(by["ina_99"], level_m=2.3, trend="baja")
    by["ina_81"] = dataclasses.replace(by["ina_81"], level_m=4.1, trend="sube")
    fb = next(st for st in cat if st.id == "fray_bentos")
    assert _nearest_text(fb, cat, by) == "más cercana: Concepción del Uruguay (AR) 4,10 m, sube"


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
            target = out / rel
            if url.endswith("/") or rel == "":
                target = target / "index.html"
            assert target.is_file(), (page, url)


def test_nino_enso_tables(site):
    out, _ = site
    html = _read(out, "nino/index.html")
    flat = _flat(html)
    # enso_summary, its lead as the h1 and the indices in the line under it.
    head = re.search(r'<section class="resp"><h1>(.*?)</h1>\s*<p>(.*?)</p>', html, flags=re.S)
    assert _flat(head.group(1)) == "El Niño sigue activo."
    assert _flat(head.group(2)) == ("El índice oficial de la NOAA (RONI) está en +1,69 para julio a septiembre "
                                    "de 2026; el índice tradicional (ONI) marca +2,16.")
    rows = _rows(_table(html, "enso"))
    assert rows[0][1:] == ("julio a septiembre de 2026", ["+1,69", "+2,16", "+0,47"])
    assert rows[1][1:] == ("junio a agosto de 2026", ["+1,36", "+1,80", "+0,44"])
    assert "es el índice que la NOAA usa ahora como oficial" in flat
    titles = [_text(t) for t in re.findall(r"<h2[^>]*>(.*?)</h2>", html, flags=re.S)]
    assert titles == ["Los dos índices", "Qué espera INUMET", "Notas"]
    notes = _flat(re.search(r'<ol class="notas">.*?</ol>', html, flags=re.S).group(0))
    assert "segunda ventana" not in _flat(html).lower() and "Pico esperado" not in html
    assert ("Según la NOAA CPC, citada por INUMET, El Niño seguiría al menos hasta marzo a mayo de 2027, "
            "con más de 80% de probabilidad.") in notes
    assert "Perspectiva de lluvias: INUMET, boletín de octubre de 2026, vía El Observador (15/10/2026)." in notes
    assert "El índice oficial de la NOAA es el RONI" in notes
    assert 'href="/clima-uy/"' in html[html.index("<h2>Qué espera INUMET</h2>"):]  # back to the river summary
    caj = re.search(r'<dl class="cajetin">(.*?)</dl>', html, flags=re.S).group(1)
    pairs = dict(zip([_flat(x) for x in re.findall(r"<dt>(.*?)</dt>", caj)],
                     [_flat(x) for x in re.findall(r"<dd>(.*?)</dd>", caj)]))
    assert pairs == {"Estado al": "julio a septiembre de 2026", "Índice oficial": "RONI",
                     "Perspectiva": "boletín de octubre de 2026", "Fuentes": "NOAA CPC · INUMET",
                     "Carácter": "informativo, no oficial"}


def test_build_with_empty_store(tmp_path):
    with Store(tmp_path / "e.sqlite") as store:
        written = build_site(store, load_catalog(), tmp_path / "out", NOW, base_url="")
    assert (tmp_path / "out" / "rio" / "index.html") in written
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert 'href="/rio/"' in html
    nino = (tmp_path / "out" / "nino" / "index.html").read_text(encoding="utf-8")
    assert "<h1>Todavía no hay índices de la NOAA cargados.</h1>" in nino
    assert "Sin datos de índices todavía." in nino and "<dd>sin índices cargados</dd>" in nino
    rio = (tmp_path / "out" / "rio" / "index.html").read_text(encoding="utf-8")
    assert '<p class="lede">No hay estaciones con datos al día.</p>' in rio
    assert "<dd>sin datos al día</dd>" in rio


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


def test_outlook_missing_cells(site):
    out, _ = site
    html = _read(out, "nino/index.html")
    table = _table(html, "outlook")
    rows = _rows(table)
    assert [r[1] for r in rows] == ["Norte", "Noreste", "Parte del centro", "Parte del este", "Resto del país"]
    assert rows[0][2] == ["60% normal 30, menos 10", "60%", "50%"]
    assert sum(c == "—" for r in rows for c in r[2]) == 8  # NDJ and DJF for the four regions besides Norte
    # Labelled cells, so the table stacks into one block per region on phones.
    for label in ("octubre a diciembre", "noviembre a enero", "diciembre a febrero"):
        assert table.count(f'data-l="{label}"') == 5
    flat = _flat(html)
    assert ("Para noviembre a enero y diciembre a febrero la nota agrupa las regiones de otra forma (60% y 50% para el norte y parte del centro, 40% para el resto), así que en la tabla se muestra solo el norte.") in flat
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
    assert "Hoja 2 de 6" in _flat(_read(out, "rio/index.html"))
    for page, n in SHEETS.items():
        footer = re.search(r'<footer class="pie">.*?</footer>', _read(out, page), flags=re.S).group(0)
        assert f"Hoja {n} de 6" in _flat(footer), page
    index = render_snapshot_index(tmp_path, [date(2026, 11, 1)], base_url="/clima-uy")
    assert "Hoja" not in _flat(index.read_text(encoding="utf-8"))


def _nav(html: str) -> dict[str, bool]:
    nav = re.search(r"<nav\b.*?</nav>", html, flags=re.S).group(0)
    return {htmllib.unescape(label): bool(cur)
            for href, cur, label in re.findall(r'<a href="([^"]+)"( aria-current="page")?>([^<]+)</a>', nav)}


def test_nino_headline_split():
    from climauy.site.build import _nino_headline

    assert _nino_headline("") == ("Todavía no hay índices de la NOAA cargados.", "")
    assert _nino_headline("Hay condiciones de La Niña: el índice oficial está en -0,5.") == (
        "Hay condiciones de La Niña.", "El índice oficial está en -0,5.")
    assert _nino_headline("Sin dos puntos.") == ("Sin dos puntos.", "")


def test_rio_cajetin(site):
    out, _ = site
    caj = re.search(r'<dl class="cajetin">(.*?)</dl>', _read(out, "rio/index.html"), flags=re.S).group(1)
    assert [_flat(x) for x in re.findall(r"<dt>(.*?)</dt>", caj)] == [
        "Estado al", "Actualización", "Estaciones", "Fuentes", "Carácter"]
    assert "<dd>2026-11-01 12:00 (UY)</dd>" in caj


def test_methodology_is_a_sheet_of_sections(site):
    out, _ = site
    html = _read(out, "rio/metodologia/index.html")
    md = (REPO / "docs" / "metodologia.md").read_text(encoding="utf-8")
    title = re.search(r"^# (.+)$", md, flags=re.M).group(1)
    assert re.search(r'<section class="resp"><h1>(.*?)</h1>', html).group(1) == title
    h2s = [_text(t) for t in re.findall(r"<h2[^>]*>(.*?)</h2>", html, flags=re.S)]
    assert h2s == re.findall(r"^## (.+)$", md, flags=re.M) + ["Notas"]
    assert html.count('<section class="sec doc">') == len(h2s) - 1
    assert 'href="/clima-uy/rio/"' in html


def test_nav_marks_the_current_sheet(site):
    out, _ = site
    nav = re.search(r"<nav\b.*?</nav>", _read(out, "rio/index.html"), flags=re.S).group(0)
    assert re.findall(r'href="([^"]+)"', nav) == ["/clima-uy/", "/clima-uy/nino/", "/clima-uy/datos/"]
    assert _nav(_read(out, "index.html")) == {"Río": True, "El Niño": False, "Datos": False}
    assert _nav(_read(out, "rio/index.html")) == {"Río": True, "El Niño": False, "Datos": False}
    assert _nav(_read(out, "rio/metodologia/index.html")) == {"Río": True, "El Niño": False, "Datos": False}
    assert _nav(_read(out, "rio/crecida/index.html")) == {"Río": True, "El Niño": False, "Datos": False}
    assert _nav(_read(out, "nino/index.html")) == {"Río": False, "El Niño": True, "Datos": False}
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
    # Paso de los Libres also has a decade of INA discharge, so its status ranks the discharge.
    store.upsert_obs(_daily("ina_72", "discharge", "ina", {
        start + timedelta(days=i): 8000.0 + 3000.0 * math.sin(i / 37.0) for i in range(n)}))
    # Uruguaiana: one fresh ANA level, no 3-day change, so it is not an upstream row.
    store.upsert_obs(_daily("ana_77150000", "level", "ana", _last_days([7.55])))
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
        # Fresh: Monte Caseros, Quaraí, Concordia, Alvear, La Cruz, Yapeyú, Paso de los Libres, Uruguaiana,
        # Boca Gualeguaychú. Colón is stale; Fray Bentos has no gauge and is not counted.
        "Estaciones": "9 con dato, 28 sin",
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
    assert artigas[2][2] == "—"  # its 3-day change is in %, not metres
    assert artigas[2][3].startswith(pct(q.percentile_today))
    assert artigas[2][4:] == ["—", "—"]
    assert "up" not in artigas[0]
    # Salto: rising, marked as such.
    assert salto[2] == ["Concordia (AR)", "9,30 m", "+0,30", salto[2][3], "11,00", "1,70"]
    assert 'class="up"' in salto[0]
    # Paysandú: Colón stopped on 25/10; the reading carries its date and nothing is said as current.
    assert paysandu[2][:3] == ["Colón (AR)", "5,25 m (al 25/10)", "—"]
    assert paysandu[2][3:] == ["—", "7,10", "—"]
    assert "up" not in paysandu[0]
    # Fray Bentos: no public gauge, the nearest station with data instead.
    assert 'class="sin"' in fray[0]
    assert fray[2] == ["sin regla pública", "más cercana: Boca Gualeguaychú (AR) 2,10 m, baja"]


def test_home_meta_description_puts_artigas_on_the_cuareim(home):
    html, _ = home
    desc = re.search(r'<meta name="description" content="([^"]*)">', html).group(1)
    assert "frente a Bella Unión, Salto, Paysandú y Fray Bentos, y del Cuareim en Artigas" in htmllib.unescape(desc)


def test_sheet_h1_uses_a_shipped_weight():
    css = (SITE_STATIC / "v2/css/site.css").read_text(encoding="utf-8")
    h1 = re.search(r"\.resp h1\{([^}]*)\}", css).group(1)
    assert "font-weight:700" in h1  # Barlow Semi Condensed ships 600 and 700 (design.md)


def test_home_percentile_baseline_allows_later_starts(home):
    # Several stations start after 1990 (Alvear, La Cruz, Yapeyú in 2006): the definition says so.
    html, _ = home
    que = _flat(re.search(r'<h2>Ciudades uruguayas</h2>\s*<p class="que">(.*?)</p>', html, flags=re.S).group(1))
    assert ("Percentil: qué parte de los días desde 1990, o desde que la estación tiene datos, estuvo más bajo."
            in que)


def test_home_city_table_labels_cells_for_phones(home):
    html, _ = home
    table = _table(html, "ciu")
    for label in ("Regla", "Nivel", "Δ 3 días", "Percentil", "Alerta", "Falta"):
        assert f'data-l="{label}"' in table
    assert '<span class="bar"><span style="width:' in table


def test_home_upstream_rows(home):
    from climauy.fmt import pct

    html, _ = home
    rows = _rows(_table(html, after="<h2>Aguas arriba</h2>"))
    # Ruling 9 (amended): the four main-river stations right above Bella Unión's with fresh data and a
    # 3-day level change, downstream order. Uruguaiana (no change) and the tributaries are skipped.
    assert [r[1] for r in rows] == ["Alvear", "La Cruz", "Yapeyú", "Paso de los Libres"]
    # An INA gauge with a ranked discharge: the level in metres, the percentile of the discharge, said so.
    libres = {s.station_id: s for s in home[1]}["ina_72"]
    assert libres.variable_used == "discharge"
    assert rows[3][2] == ["7,66 m", "-0,44", pct(libres.percentile_today)]
    assert "Donde la estación mide caudal, el percentil es del caudal." in _flat(html[html.index("<h2>Aguas arriba</h2>"):])
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
        ("Lluvia norte, noviembre a enero", "60% de probabilidad de lluvia sobre lo normal"),
        ("Sigue al menos hasta", "marzo a mayo de 2027, más de 80% de probabilidad (NOAA CPC)"),
        ("Fuente", "INUMET, boletín de octubre de 2026, vía El Observador"),
    ]
    assert 'href="/clima-uy/nino/"' in block
    assert '<a href="https://www.elobservador.com.uy/' in kv


def test_outlook_source_names_the_outlet():
    from climauy.site.build import _outlook_source

    data = {"issued": "2026-10-15"}
    assert _outlook_source(data) == "INUMET, boletín de octubre de 2026"
    assert _outlook_source({**data, "source_outlet": "El Observador"}) == (
        "INUMET, boletín de octubre de 2026, vía El Observador")


def test_home_stale_level_next_to_fresh_discharge(tmp_path):
    """Paso de los Libres: its INA level stopped on 25/10 while its discharge is current. The level is shown
    with its date, so the 3-day change, the percentile and the alert are left empty in every home table."""
    with Store(tmp_path / "s.sqlite") as store:
        _seed(store)
        start = date(2014, 11, 1)
        store.upsert_obs(_daily("ina_72", "discharge", "ina", {
            start + timedelta(days=i): 8000.0 + 3000.0 * math.sin(i / 37.0) for i in range((NOW.date() - start).days + 1)}))
        store.upsert_obs(_daily("ina_72", "level", "ina", {date(2026, 10, d): 7.0 + d / 100 for d in range(1, 26)}))
        build_site(store, load_catalog(), tmp_path / "out", NOW, base_url="/clima-uy")
    html = _read(tmp_path / "out", "index.html")
    rows = {r[1]: r for r in _rows(_table(html[html.index("<h2>Todas las estaciones</h2>"):]))}
    libres = rows["Paso de los Libres AR"]
    assert libres[2] == ["7,25 m (al 25/10)", "—", "—", "—"]
    assert 'class="al"' not in libres[0]


def test_home_all_stations(home):
    from climauy.fmt import pct

    html, statuses = home
    block = html[html.index("<h2>Todas las estaciones</h2>"):]
    assert "<summary>Ver las 9 estaciones en orden aguas abajo</summary>" in block
    assert "Donde la estación mide caudal, el percentil es del caudal." in _flat(block)
    rows = _rows(_table(block))
    assert [r[1] for r in rows] == [
        "Alvear AR", "La Cruz AR", "Yapeyú AR", "Paso de los Libres AR", "Uruguaiana BR", "Quaraí BR · frente a Artigas",
        "Monte Caseros AR · frente a Bella Unión", "Concordia AR · frente a Salto", "Boca Gualeguaychú AR",
    ]
    libres = rows[3]
    assert 'class="al"' in libres[0]
    # Hoy in metres next to the alert in metres, even though the status ranks the discharge.
    by = {s.station_id: s for s in statuses}
    assert libres[2] == ["7,66 m", "-0,44", pct(by["ina_72"].percentile_today), "7,50"]
    quarai = rows[5]
    assert quarai[2][0].endswith("m³/s") and quarai[2][1] == "—" and quarai[2][3] == "—"
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


def test_dash_only_as_an_empty_table_cell(home):
    html, _ = home
    assert re.search(r"<td\b[^>]*>—</td>", html)
    assert "—" not in re.sub(r"<td\b[^>]*>—</td>", "", html)


def test_home_tables_scope_their_column_headers(home):
    html, _ = home
    for thead in re.findall(r"<thead>.*?</thead>", html, flags=re.S):
        ths = re.findall(r"<th\b[^>]*>", thead)
        assert ths and all('scope="col"' in th for th in ths)


def test_home_city_at_alert_and_nearest_skips_stale(tmp_path):
    with Store(tmp_path / "a.sqlite") as store:
        # Concordia (Salto) at 11,20 m, over its 11,00 alert.
        store.upsert_obs(_daily("ina_79", "level", "ina", _last_days([10.6, 10.8, 11.0, 11.2])))
        # Boca Gualeguaychú, the closest gauge to Fray Bentos, stopped on 20/10; Gualeguaychú (a tributary,
        # never offered as the river's level) reads today. The nearest main-river gauge left is Concordia.
        store.upsert_obs(_daily("ina_84", "level", "ina", {date(2026, 10, 20): 2.5}))
        store.upsert_obs(_daily("ina_99", "level", "ina", {date(2026, 11, 1): 2.2}))
        build_site(store, load_catalog(), tmp_path / "out", NOW, base_url="")
    html = _read(tmp_path / "out", "index.html")
    rows = {r[1]: r for r in _rows(_table(html, "ciu"))}
    salto = rows["Salto"]
    assert "al" in salto[0].split('"')[1].split() and "up" in salto[0].split('"')[1].split()
    assert salto[2][1] == "11,20 m" and salto[2][4:] == ["11,00", "llegó"]
    assert "El río pasó la cota de alerta frente a Salto." in _flat(html)
    assert rows["Fray Bentos"][2][1] == "más cercana: Concordia (AR) 11,20 m, sube"


def test_home_notes_group_cotas_by_source_and_date(tmp_path):
    import json

    refs = json.loads((REPO / "content/referencias_uy.json").read_text(encoding="utf-8"))
    refs["cities"]["Fray Bentos"]["date"] = "2026-09-01"
    refs["cities"]["Fray Bentos"]["url"] = "https://example.org/otro.pdf"
    path = tmp_path / "refs.json"
    path.write_text(json.dumps(refs), encoding="utf-8")
    with Store(tmp_path / "n.sqlite") as store:
        build_site(store, load_catalog(), tmp_path / "out", NOW, base_url="", refs_path=path)
    notes = re.search(r'<ol class="notas">(.*?)</ol>', _read(tmp_path / "out", "index.html"), flags=re.S).group(1)
    assert ("Las cotas oficiales uruguayas se miden en la regla de cada ciudad: Bella Unión 6,30 m, Salto 11,40 m, "
            "Paysandú 6,00 m (DINAGUA, 3/8/2026); Fray Bentos 3,90 m (DINAGUA, 1/9/2026). "
            "No se comparan con las reglas de enfrente.") in _flat(notes)
    assert 'href="https://example.org/otro.pdf"' in notes and f'href="{DINAGUA_URL}"' in notes


# ---------------------------------------------------------------- /rio/crecida/ (sheet 3)

def test_crecida_title_block(site):
    out, _ = site
    html = _read(out, "rio/crecida/index.html")
    assert re.search(r'<section class="resp"><h1>La crecida, día por día</h1>', html)
    # Only Monte Caseros is measured in the seed: the lead names it, not the whole river.
    assert "Los últimos 60 días del río, en Monte Caseros." in _flat(html)
    assert "Nueva Palmira" not in _flat(re.search(r'<section class="resp">.*?</section>', html, flags=re.S).group(0))
    assert '<a href="/clima-uy/rio/">El río estación por estación</a>' in html
    caj = re.search(r'<dl class="cajetin">(.*?)</dl>', html, flags=re.S).group(1)
    assert [_flat(x) for x in re.findall(r"<dt>(.*?)</dt>", caj)] == ["Ventana", "Estaciones", "Datos", "Carácter"]
    dds = [_flat(x) for x in re.findall(r"<dd>(.*?)</dd>", caj, flags=re.S)]
    # NOW is 18:05 on 1/11 in Montevideo: the window ends that day.
    assert dds == ["03/09/2026 al 01/11/2026", "1 de 33", "matriz en CSV", "informativo, no oficial"]
    assert '<a href="/clima-uy/data/rio/crecida-60d.csv" download>matriz en CSV</a>' in caj


def test_crecida_draws_both_variants_and_the_scale(site):
    out, _ = site
    html = _read(out, "rio/crecida/index.html")
    assert html.count('<svg xmlns="http://www.w3.org/2000/svg" class="hov"') == 1
    assert html.count('<svg xmlns="http://www.w3.org/2000/svg" class="hov movil"') == 1
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    scale = _flat(re.search(r'<p class="escala">.*?</p>', html, flags=re.S).group(0))
    assert scale == "menos de 5050 a 7575 a 9090 a 9595 a 9999 o másblanco: sin dato"


def test_crecida_day_selector_works_only_with_js(site):
    out, _ = site
    html = _read(out, "rio/crecida/index.html")
    assert '<label for="dia">Día</label><input id="dia" type="range" min="0" max="59" value="59"' in html
    assert re.search(r'<div class="dia" id="dia-control" hidden>', html)
    assert '<div id="dia-perfil" hidden>' in html
    assert "Para elegir un día hace falta JavaScript." in re.search(r"<noscript>.*?</noscript>", html, flags=re.S).group(0)


def test_crecida_day_data_is_safe_json(site):
    import json

    out, _ = site
    html = _read(out, "rio/crecida/index.html")
    raw = re.search(r'<script type="application/json" id="crecida-datos">(.*?)</script>', html, flags=re.S).group(1)
    assert "<" not in raw
    data = json.loads(raw)
    assert len(data["days"]) == 60 and data["days"][-1] == "2026-11-01"
    assert data["partial"] is True and data["partialText"] == " (hoy, parcial)"
    for r in data["rows"]:
        for p in r["p"]:
            assert p is None or p == math.floor(p * 10 + 1e-9) / 10
    assert data["months"][8] == "sep"
    assert [r["city"] for r in data["rows"]] == ["Bella Unión"]
    assert data["rows"][0]["p"][-1] is not None


def test_crecida_notes(site):
    out, _ = site
    notes = _flat(re.search(r'<ol class="notas">.*?</ol>', _read(out, "rio/crecida/index.html"), flags=re.S).group(0))
    assert "Entran las estaciones con 10 años o más de historia y dato en al menos 80% de los días de la ventana." in notes
    assert "Con huecos en estos días" not in notes  # the seed has no station with history and too few days
    assert "Sin 10 años de historia: Machadinho jusante, " in notes and "Colón" in notes
    assert "La última fila es hoy y se completa a medida que llegan datos." in notes
    assert ("Un pico es un día más alto que los cinco días anteriores y los cinco siguientes, con percentil 90 o más."
            in notes)


def test_crecida_skipped_note_lists_names(tmp_path):
    with Store(tmp_path / "t.sqlite") as store:
        _seed(store)
        # Uruguaiana: 12 years of discharge that stop a month before the build.
        start = date(2014, 9, 1)
        store.upsert_obs([
            Obs("ana_77150000", "discharge", datetime(d.year, d.month, d.day, 15, tzinfo=timezone.utc), 5000.0, "ana_hist")
            for d in (start + timedelta(days=i) for i in range((date(2026, 10, 1) - start).days))
        ])
        build_site(store, load_catalog(), tmp_path / "out", NOW, base_url="/clima-uy")
    html = _read(tmp_path / "out", "rio/crecida/index.html")
    notes = _flat(re.search(r'<ol class="notas">.*?</ol>', html, flags=re.S).group(0))
    assert "Con huecos en estos días: Uruguaiana." in notes
    no_history = re.search(r"Sin 10 años de historia: ([^.]*)\.", notes).group(1).split(", ")
    assert "Uruguaiana" not in no_history and "Monte Caseros" not in no_history
    caj = re.search(r'<dl class="cajetin">(.*?)</dl>', html, flags=re.S).group(1)
    kept, total = map(int, re.search(r"<dd>(\d+) de (\d+)</dd>", caj).groups())
    assert kept + 1 + len(no_history) == total


def test_crecida_csv(site):
    import csv

    out, _ = site
    with (out / "data/rio/crecida-60d.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["date", "station_id", "station", "km", "variable", "value", "percentile", "partial"]
    body = rows[1:]
    assert len(body) == 60
    assert [r[7] for r in body] == ["0"] * 59 + ["1"]
    assert all(re.fullmatch(r"\d+(\.\d)?", r[6]) for r in body)
    assert {r[1] for r in body} == {"ina_74"} and body[0][2] == "Monte Caseros" and body[0][4] == "level"
    assert body[0][0] == "2026-09-03" and body[-1][0] == "2026-11-01"
    for r in body:
        float(r[3]), float(r[5]), float(r[6])
        assert 0 <= float(r[6]) <= 100


def test_links_to_crecida(site):
    out, _ = site
    link = '<a href="/clima-uy/rio/crecida/">Ver la crecida día por día, últimos 60 días</a>'
    home = _read(out, "index.html")
    assert link in home
    assert home.index(link) > home.index('<p class="ley">')
    rio = _read(out, "rio/index.html")
    assert link in rio and rio.index(link) > rio.index('<figure class="profile">')
    datos = _read(out, "datos/index.html")
    assert 'href="/clima-uy/data/rio/crecida-60d.csv"' in datos
    assert "<code>partial</code>" in datos


def test_crecida_sets_js_class_for_the_selector(site):
    out, _ = site
    html = _read(out, "rio/crecida/index.html")
    assert 'document.documentElement.classList.add("js")' in html


def test_crecida_stretch_names_the_measured_river():
    from climauy.analysis.crecida import MatrixRow, RiverMatrix
    from climauy.site.build import _crecida_stretch

    cat = load_catalog()
    def m(*ids):
        rows = [MatrixRow(i, i.upper(), None, 0.0, "level", [], [], []) for i in ids]
        return RiverMatrix([], rows, [], [], 33, 1152.0, None)
    from climauy.catalog import river_km
    main = [s.id for s in cat if s.id in river_km(cat)]
    assert _crecida_stretch(m(main[0], "x", main[-1]), cat) == "de las represas de Brasil a Nueva Palmira"
    assert _crecida_stretch(m("ina_60", "ina_84"), cat) == "de INA_60 a INA_84"
    assert _crecida_stretch(m("ina_74"), cat) == "en INA_74"
    assert _crecida_stretch(m(), cat) == "sin estaciones con datos"
