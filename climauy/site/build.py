"""Static site build: the El Niño home page, the Uruguay River module, methodology and data pages.

Every page works without JavaScript: sentences, tables and charts are in the
HTML. Links are absolute under `base_url`, so a copied page (the daily
snapshot) still finds the stylesheet and the rest of the site.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Sequence

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from ..analysis.daily import family_daily, family_for, station_daily, station_readings
from ..analysis.events import SEASONS, compare_floods, season_series
from ..analysis.lag import LagResult, run_production_lags
from ..analysis.status import MIN_HISTORY_YEARS, StationStatus, compute_status
from ..analysis.verify import VERIFICATION_TARGETS, verify_production
from ..catalog import Station
from ..fmt import (
    MONTHS, RECORD_TEXT, day_long, day_numeric, day_short, days, hour_local, is_record, is_season, meters, num, pct,
    season_key, season_label, signed,
)
from ..narrative import enso_summary, river_summary
from .. import publish
from ..publish import series_exported
from ..store import Store
from ..timeutil import local_day
from . import charts
from .markdown import render as render_md

SITE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SITE_DIR.parents[1]
OUTLOOK_PATH = REPO_ROOT / "content" / "enso_outlook.json"
METHOD_PATH = REPO_ROOT / "docs" / "metodologia.md"
REFS_PATH = REPO_ROOT / "content" / "referencias_uy.json"
OPEN_METEO_URL = "https://open-meteo.com/"
OPEN_METEO_TEXT = "Weather data by Open-Meteo.com"  # wording asked by the Open-Meteo licence
LICENCES_DOC = "docs/licencias.md"


def copernicus_notice(year: int) -> str:
    """Notice required by the CEMS-FLOODS licence for modified (aggregated) GloFAS data."""
    return f"Contains modified Copernicus Emergency Management Service information {year}"

FOOTER_TEXT = "Esto no es una alerta oficial. Para alertas y evacuaciones: SINAE y el CECOED de tu departamento."
SINAE_URL = "https://www.gub.uy/sistema-nacional-emergencias/"
REPO_URL = "https://github.com/sebastian-reolon/clima-uy"
AUTHOR_URL = "https://github.com/sebastian-reolon"
SITE_ORIGIN = "https://sebastian-reolon.github.io"
COTA_SOURCE_LINE = "Cotas de alerta y evacuación: INA y Prefectura Naval Argentina."
DISCLAIMER_SHORT = "Esto no es una alerta oficial. Alertas y evacuaciones:"
SOURCE_NAMES = {"ina": "del INA", "ctm": "de CTM Salto Grande", "ana": "de ANA (Brasil)", "ana_hist": "de ANA (Brasil)"}
SOURCES = [
    ("ANA (Brasil)", "https://telemetriaws1.ana.gov.br/ServiceANA.asmx"),
    ("INA y Prefectura Naval Argentina", "https://alerta.ina.gob.ar/a5"),
    ("CTM Salto Grande", "https://www.saltogrande.org/docs/hidrologia/CaudalesNiveles.pdf"),
    ("GloFAS vía Open-Meteo", "https://open-meteo.com/en/docs/flood-api"),
    ("NOAA CPC", "https://www.cpc.ncep.noaa.gov/data/indices/"),
    ("INUMET", "https://www.inumet.gub.uy/"),
]

VERIFY_SINCE = date(2026, 11, 1)
# Stations whose seasons are compared with past floods (Uruguayan cities across the river).
SEASON_STATIONS = ["ina_74", "ina_79", "ina_80", "ana_77500000"]
ENSO_ROWS = 6
DAM_STALE_DAYS = 2
SPARK_DAYS = 30
SPARK_MIN_DAYS = 7  # fewer days in the window make a dot, not a line
RIVER_NAMES = {"Quaraí": "Quaraí o Cuareim"}
VARIABLE_NAMES = {"level": "nivel", "discharge": "caudal"}
UNITS = {"level": "m", "discharge": "m³/s"}
COUNTRY = {"AR": "Argentina", "BR": "Brasil", "UY": "Uruguay"}


# ---------------------------------------------------------------- view models

@dataclass
class StationView:
    station: Station
    status: StationStatus
    title: str
    how: str  # ", medido en X, enfrente" for a Uruguayan city, else ""
    sub: str  # country and river, shown next to the name
    gauge: Markup | None
    spark: Markup | None
    reading: str
    change: str
    change_short: str  # without "en 3 días", for the table column
    percentile: str
    when: str
    meta: str  # percentile and reading time, joined
    branches: list["Branch"] = field(default_factory=list)
    ref: dict | None = None  # official level of a Uruguayan city, on its own gauge


@dataclass
class Branch:
    label: str
    items: list[StationView]


def _years(y: float) -> str:
    n = int(y)
    return "menos de 1 año" if n < 1 else ("1 año" if n == 1 else f"{n} años")


def _when(ss: StationStatus, source: str | None, today: date) -> str:
    """'dato del INA de las 12:00' or 'dato de ANA (Brasil) del 31/10 a las 21:00'."""
    t = ss.latest_time_utc
    if t is None:
        return ""
    who = f"dato {SOURCE_NAMES.get(source, 'de ' + source)}" if source else "dato"
    d = local_day(t)
    if d == today:
        return f"{who} de las {hour_local(t)}"
    return f"{who} del {day_short(d)} a las {hour_local(t)}"


def _change(ss: StationStatus) -> str:
    if ss.trend is None:
        return ""
    if ss.trend == "estable":
        return "estable"
    verb = "subió" if ss.trend == "sube" else "bajó"
    if ss.trend_basis == "level" and ss.level_change_3d_m is not None:
        return f"{verb} {meters(abs(ss.level_change_3d_m))}"
    if ss.discharge_change_3d_pct is not None:
        return f"el caudal {verb} {int(abs(ss.discharge_change_3d_pct))}%"
    return ""


def _station_view(st: Station, ss: StationStatus, store: Store, today: date) -> StationView:
    city = st.represents
    if city and city != st.name:
        title, how = city, f", medido en {st.name}, enfrente"
    else:
        title, how = (city or st.name), ""
    no_data = ss.latest_time_utc is None
    river = RIVER_NAMES.get(st.river, st.river)
    river_part = "" if st.river == "Uruguay" else f"río {river}"
    # A city already says where it is measured; the country of that station would only confuse.
    sub = river_part if how else ", ".join(p for p in (COUNTRY.get(st.country, st.country), river_part) if p)

    reading = change = percentile = ""
    if no_data:
        if not st.sources:
            # A city with no gauge of its own nor one across the river (Fray Bentos).
            how = ": sin estación con datos al día"
            sub = ""
        else:
            reading = "sin datos"
    elif ss.stale:
        reading = f"desactualizada desde el {day_numeric(ss.stale_since)}" if ss.stale_since else "desactualizada"
    else:
        # The level is written on the gauge; the reading line adds what the gauge does not show.
        parts = []
        if ss.above_evac:
            parts.append("sobre la cota de evacuación")
        elif ss.above_alert:
            parts.append("sobre la cota de alerta")
        if ss.variable_used == "discharge" and ss.latest_value is not None:
            parts.append(f"caudal {num(ss.latest_value)} m³/s")
        reading = ", ".join(parts)
        change = _change(ss)
        if ss.percentile_today is not None:
            percentile = (RECORD_TEXT if is_record(ss.percentile_today)
                          else f"más alto que el {pct(ss.percentile_today)}% de los días desde 1990")
        elif ss.history_years < MIN_HISTORY_YEARS:
            percentile = f"sin percentil (tiene {_years(ss.history_years)} de historia, hacen falta 10)"
    gauge = None if no_data else Markup(charts.gauge_svg(ss, st))
    readings = [] if no_data else station_readings(store, st, ss.variable_used)
    daily = family_daily(readings, family_for(st, ss.variable_used)) if readings else {}
    source = readings[-1].source if readings else None
    start = max(daily) - timedelta(days=SPARK_DAYS) if daily else None
    recent = sum(1 for d in daily if d > start) if daily else 0
    spark = Markup(charts.sparkline_svg(daily, days=SPARK_DAYS)) if recent >= SPARK_MIN_DAYS else None
    when = _when(ss, source, today)
    meta = ", ".join(p for p in (percentile, ("último " + when) if ss.stale and when else when) if p)
    change_long = f"{change} en 3 días" if change else ""
    return StationView(st, ss, title, how, sub, gauge, spark, reading, change_long, change, percentile, when, meta)


def _cota_m(v: float) -> str:
    """Official levels are written with two decimals, as their sources publish them (11,40 m)."""
    return f"{v:.2f}".replace(".", ",") + " m"


def _city_refs(path: Path, catalog: Sequence[Station]) -> dict[str, dict]:
    """Per station id, the official level of the city it represents, ready for the template."""
    cities = json.loads(path.read_text(encoding="utf-8"))["cities"]
    out = {}
    for st in catalog:
        r = cities.get(st.represents or "")
        if r is None:
            continue
        other = r.get("other")
        out[st.id] = {
            "city": st.represents, "kind": r["kind"], "value": _cota_m(r["value_m"]),
            "source": r["source"], "url": r["url"], "date": day_long(date.fromisoformat(r["date"])),
            # Only a station across the river or upstream needs the warning.
            "station": st.name if st.sources and st.name != st.represents else None,
            "other": None if not other else {
                "source": other["source"], "url": other["url"],
                "date": day_long(date.fromisoformat(other["date"])),
            },
        }
    return out


def _column(views: dict[str, StationView], catalog: Sequence[Station]) -> list[StationView]:
    """Main-stem stations downstream, each carrying the tributaries that join just above it."""
    children: dict[str, list[Station]] = {}
    for st in sorted(catalog, key=lambda s: s.order):
        if st.tributary_of:
            children.setdefault(st.tributary_of, []).append(st)

    def branches(parent: Station, into: str) -> list[Branch]:
        out = []
        for trib in children.get(parent.id, []):
            v = views[trib.id]
            river = RIVER_NAMES.get(trib.river, trib.river)
            v.branches = branches(trib, f"al {river}")
            where = f" antes de {parent.name}" if parent.tributary_of is None else ""
            out.append(Branch(f"Río {river}: llega {into}{where}", [v]))
        return out

    main = [st for st in sorted(catalog, key=lambda s: s.order) if st.tributary_of is None]
    column = []
    for st in main:
        v = views[st.id]
        v.branches = branches(st, "al Uruguay")
        column.append(v)
    return column


def _dam_view(store: Store, today: date) -> dict | None:
    rows = store.dam_days()
    if not rows:
        return None
    d = rows[-1]
    return {
        "date": day_long(d.date_local),
        "date_numeric": day_numeric(d.date_local),
        "stale": (today - d.date_local).days > DAM_STALE_DAYS,
        "machadinho": None if d.vol_machadinho_pct is None else f"{num(d.vol_machadinho_pct, 0)}%",
        "ita": None if d.vol_ita_pct is None else f"{num(d.vol_ita_pct, 0)}%",
        "turb": num(d.q_turb, 0),
        "vert": num(d.q_vert, 0),
        "total": num(d.q_total, 0),
    }


def _runs_archived(store: Store, issuer: str, station_id: str, variable: str) -> int:
    return len({f.issued_utc for f in store.forecasts(issuer, station_id) if f.variable == variable})


def _saved(n: int) -> str:
    return "1 pronóstico guardado" if n == 1 else f"{n} pronósticos guardados"


def _verified(n: int) -> str:
    return "1 corrida verificada" if n == 1 else f"{n} corridas verificadas"


def _forecast_views(store: Store, by_id: dict[str, Station], verify: dict) -> tuple[list[dict], list[dict]]:
    """(charts for section 3, verification rows for section 5), in VERIFICATION_TARGETS order."""
    fc_views, ver_views = [], []
    for (issuer, sid, variable), obs_id in VERIFICATION_TARGETS.items():
        st, obs_st = by_id[sid], by_id[obs_id]
        fc = [f for f in store.forecasts(issuer, sid) if f.variable == variable]
        place = st.represents or st.name
        if issuer == "glofas" and st.represents:
            place = f"{st.represents} y {st.name}"
        name = f"{place}, {VARIABLE_NAMES[variable]}"
        if issuer == "ina":
            caption = ("Pronóstico oficial del INA a 13 días. "
                       "La banda va del valor inferior al superior que publica el INA.")
        else:
            caption = (
                "Modelo global (GloFAS), sin corregir para este río. Pronóstico a 30 días en la celda del cauce; "
                "la banda es la mitad central del conjunto de corridas del modelo."
            )
            if obs_id != sid:
                caption += (f" Lo observado es el caudal de {obs_st.name}, "
                            f"porque la serie de {st.name} dejó de publicarse.")
        run = ""
        if fc:
            issued = max(f.issued_utc for f in fc)
            run = f"Corrida del {day_long(local_day(issued))} a las {hour_local(issued)}, hora de Montevideo."
        fc_views.append({
            "issuer": issuer, "name": name, "caption": caption, "run": run, "glofas": issuer == "glofas",
            "svg": Markup(charts.forecast_band_svg(station_daily(store, obs_st, variable), fc)) if fc else None,
        })
        res = verify[(issuer, sid, variable)]
        archived = _runs_archived(store, issuer, sid, variable)
        rows = []
        if res.enough:
            unit = UNITS[variable]
            for lead, s in res.by_lead.items():
                rows.append({
                    "lead": "1 día" if lead == 1 else f"{lead} días", "n": s.n,
                    "mae": f"{num(s.mae)} {unit}",
                    "bias": f"{signed(s.bias) if unit == 'm' else num(s.bias, 0)} {unit}",
                    "coverage": "" if s.coverage is None else f"{int(s.coverage * 100)}%",
                })
        ver_views.append({
            "label": ("INA" if issuer == "ina" else "GloFAS") + f", {name}",
            # Until verification is possible the count is of archived runs, said as such;
            # afterwards it is VerifyResult.n_runs (runs with at least one observed day).
            "status": _verified(res.n_runs) if res.enough else
            f"acumulando datos desde el {day_long(VERIFY_SINCE)}: {_saved(archived)}",
            "rows": rows,
        })
    return fc_views, ver_views


def _season_views(store: Store, catalog: Sequence[Station], by_id: dict[str, Station]) -> list[dict]:
    floods = compare_floods(store, SEASON_STATIONS, catalog)
    out = []
    for sid in SEASON_STATIONS:
        st = by_id[sid]
        summaries = floods.get(sid, {})
        variable = next(iter(summaries.values())).variable if summaries else "level"
        unit = UNITS[variable]
        series = {name: season_series(store, sid, variable, date(year, 10, 1), catalog)
                  for name, year in SEASONS.items()}
        has_cota = variable == "level" and "ina_level" in st.sources and st.alert_m is not None
        alert = st.alert_m if has_cota else None
        if has_cota:
            cota = f"Cota de alerta: {st.cota_source or 'INA'}."
        else:
            cota = "Esta estación no tiene cota oficial de alerta, así que no se dibuja ninguna."
        rows = []
        for name in SEASONS:
            s = summaries.get(name)
            if s is None:
                rows.append({"season": name, "peak": "sin datos", "date": "", "above": ""})
                continue
            peak = meters(s.peak_value) if unit == "m" else f"{num(s.peak_value)} {unit}"
            above = "" if s.days_above_alert is None else (
                "1 día" if s.days_above_alert == 1 else f"{s.days_above_alert} días")
            rows.append({"season": name, "peak": peak, "date": day_numeric(s.peak_date), "above": above})
        title = st.represents or st.name
        river = RIVER_NAMES.get(st.river, st.river)
        how = f"medido en {st.name}" + (f", sobre el río {river}" if st.river != "Uruguay" else "")
        out.append({
            "id": sid, "title": title, "how": how, "cota": cota, "has_cota": has_cota, "rows": rows,
            "svg": Markup(charts.season_compare_svg(series, alert, unit=unit)),
        })
    return out


def _lag_views(results: Sequence[LagResult], by_id: dict[str, Station]) -> list[dict]:
    out = []
    for r in results:
        up_id, _, variable = r.upstream.partition(":")
        down_id = r.downstream.partition(":")[0]
        up, down = by_id[up_id].name, by_id[down_id].name
        unit = UNITS.get(variable, "")
        n = len(r.per_event)
        if r.lag_days_range:
            lo, hi = (int(v) for v in r.lag_days_range)
            crecidas = "1 crecida pasada" if n == 1 else f"{n} crecidas pasadas"
            summary = (f"En {crecidas}, el pico tardó entre {lo} y {hi} días en llegar de {up} a {down}; "
                       f"la mediana es {days(r.lag_days_median)}.")
        else:
            summary = f"No hay crecidas pasadas para medir cuánto tarda el pico de {up} a {down}."
        skipped = ""
        if r.events_skipped:
            k = r.events_skipped
            skipped = (f"{'Otra crecida no se pudo medir' if k == 1 else f'Otras {k} crecidas no se pudieron medir'}: "
                       f"el pico de {up} no quedó claro dentro de los 15 días anteriores.")

        def val(v: float) -> str:
            return f"{num(v)} {unit}" if unit == "m" else f"{num(v, 0)} {unit}"

        rows = [{
            "year": name,
            "predicted": day_numeric(h.predicted_peak), "observed": day_numeric(h.observed_peak),
            "error": days(h.peak_error_days),
            "mae_model": val(h.mae_model), "mae_persistence": val(h.mae_persistence),
        } for name, h in r.holdout.items()]
        out.append({
            "title": f"De {up} a {down}, {VARIABLE_NAMES.get(variable, variable)}",
            "svg": Markup(charts.lag_svg(r, up, down)), "summary": summary, "skipped": skipped,
            "reason": r.reason, "validated": r.validated, "rows": rows, "unit": unit,
        })
    return out


def _enso_rows(store: Store) -> list[dict]:
    roni = {i.period: i.value for i in store.indices("RONI") if is_season(i.period)}
    oni = {i.period: i.value for i in store.indices("ONI") if is_season(i.period)}
    periods = sorted(roni, key=season_key)[-ENSO_ROWS:]
    rows = []
    for p in reversed(periods):
        o = oni.get(p)
        rows.append({
            "period": season_label(p), "roni": signed(roni[p]),
            "oni": signed(o) if o is not None else "sin dato",
            "diff": signed(o - roni[p]) if o is not None else "",
        })
    return rows


def _outlook(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for region in data["regions"]:
        cells = []
        for season in data["seasons"]:
            v = region.get(season["code"])
            if not v or v[0] is None:
                cells.append({"main": "", "rest": ""})
                continue
            rest = "" if v[1] is None or v[2] is None else f"normal {v[1]}, menos {v[2]}"
            cells.append({"main": f"{v[0]}%", "rest": rest})
        rows.append({"region": region["name"], "cells": cells})
    return {"source": data["source"], "source_url": data["source_url"],
            "seasons": data["seasons"], "rows": rows}


def _downloads(store: Store, catalog: Sequence[Station], now_utc: datetime) -> dict:
    series = []
    for st in catalog:
        for variable in ("level", "discharge"):
            if series_exported(store, st, variable):
                series.append({"name": f"{st.name}, {VARIABLE_NAMES[variable]}",
                               "path": f"data/rio/daily/{st.id}_{variable}.csv"})
    months = [{"key": f"{m.year}-{m.month:02d}", "label": f"{MONTHS[m.month - 1]} de {m.year}"}
              for m in publish.month_starts(local_day(now_utc))]
    return {"series": series, "months": months}


_NO_DOWNLOAD = "Sus series completas no se ofrecen para descargar"
_NO_TERMS = ("{who} condiciones de reutilización: republicar sus datos públicos citando la fuente es un "
             "criterio de este proyecto, no un permiso. Si piden retirarlos, se retiran.")
_SHOWN = ("Las páginas muestran sus datos con la fuente y el link, también en gráficos hechos con sus series "
          "diarias (temporadas de crecida, últimos 30 días, lo observado en los pronósticos).")


def _source_terms() -> list[dict]:
    """What the Datos page says about each source, following the licence switch in publish."""
    full, issuers = publish.FULL_SERIES_SOURCES, publish.FULL_FORECAST_ISSUERS
    ana = ("Se pueden descargar sus series completas: la ANA permite reproducir sus datos citando la fuente."
           if "ana" in full else _NO_DOWNLOAD + ". " + _SHOWN)
    if "ina" in full:
        ina = ("Se pueden descargar sus series completas. " + _NO_TERMS.format(who="El INA y Prefectura no publican"))
    else:
        ina = (_NO_DOWNLOAD + ", porque el sistema de datos del INA no dice si se pueden republicar. " + _SHOWN)
    ina += (" Los pronósticos del INA también se pueden descargar." if "ina" in issuers else
            " Los pronósticos del INA se ven en la página del río pero no se ofrecen para descargar.")
    ctm = ("Se pueden descargar sus lecturas. " + _NO_TERMS.format(who="La CTM no publica")
           if "ctm" in full else
           "Sus lecturas no se ofrecen para descargar, porque su sitio no publica condiciones de reutilización. "
           "Los datos del día de la represa se ven en la página del río, con el link al parte.")
    glofas = ("Se pueden descargar los pronósticos completos. Open-Meteo los publica con licencia CC BY 4.0 y la "
              "licencia de Copernicus pide este aviso:" if "glofas" in issuers else
              _NO_DOWNLOAD + ". Aviso de Copernicus:")
    rows = [
        ("ANA (Brasil)", SOURCES[0][1], ana),
        ("INA y Prefectura Naval Argentina", SOURCES[1][1], ina),
        ("CTM Salto Grande", SOURCES[2][1], ctm),
        ("GloFAS vía Open-Meteo", SOURCES[3][1], glofas),
        ("NOAA CPC", SOURCES[4][1], "Se pueden descargar los índices completos: son de dominio público en Estados Unidos."),
        ("INUMET", SOURCES[5][1],
         "Se citan en la portada sus probabilidades para los próximos meses, con su link; no hay descarga."),
    ]
    withdraw = {"INA y Prefectura Naval Argentina": "ina" in full, "CTM Salto Grande": "ctm" in full}
    return [{"name": n, "url": u, "text": t, "glofas": n.startswith("GloFAS"), "withdraw": withdraw.get(n, False)}
            for n, u, t in rows]


def _catalog_rows(catalog: Sequence[Station]) -> list[dict]:
    rows = []
    for st in sorted(catalog, key=lambda s: s.order):
        src = []
        if "ana" in st.sources:
            src.append(f"ANA {st.sources['ana']}")
        if "ina_level" in st.sources:
            src.append(f"INA {st.sources['ina_level']}")
        if "ina_q" in st.sources:
            src.append(f"INA {st.sources['ina_q']} (caudal)")
        if "glofas" in st.sources:
            src.append("GloFAS")
        cotas = ""
        if "ina_level" in st.sources and st.alert_m is not None:
            cotas = f"{meters(st.alert_m)}" + (f" y {meters(st.evac_m)}" if st.evac_m is not None else "")
        river = RIVER_NAMES.get(st.river, st.river)
        rows.append({
            "id": st.id, "name": st.name, "country": COUNTRY.get(st.country, st.country), "river": river,
            "sources": ", ".join(src) or "sin fuente con datos", "cotas": cotas,
            "cota_source": st.cota_source if cotas else "", "represents": st.represents or "",
        })
    return rows


# ---------------------------------------------------------------- build

def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(SITE_DIR / "templates"),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(meters=meters, num=num, pct=pct)
    return env


def _common(base: str) -> dict:
    return {
        "base": base,
        "footer_text": FOOTER_TEXT,
        "sinae_url": SINAE_URL,
        "sources": SOURCES,
        "repo_url": REPO_URL,
        "author_url": AUTHOR_URL,
    }


def render_snapshot_index(out_dir: Path, days: Sequence[date], base_url: str = "/clima-uy") -> Path:
    """Write snapshots/index.html listing the given days (in the order given)."""
    base = base_url.rstrip("/")
    path = out_dir / "snapshots" / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    items = [{"key": d.isoformat(), "label": day_long(d)} for d in days]
    path.write_text(
        _env().get_template("snapshots.html.j2").render(**_common(base), nav="", days=items), encoding="utf-8"
    )
    return path


def build_site(
    store: Store,
    catalog: Sequence[Station],
    out_dir: Path,
    now_utc: datetime,
    base_url: str = "/clima-uy",
    outlook_path: Path = OUTLOOK_PATH,
    method_path: Path = METHOD_PATH,
    refs_path: Path = REFS_PATH,
) -> list[Path]:
    """Write the four pages and the static assets into out_dir; returns the pages written."""
    base = base_url.rstrip("/")
    out_dir.mkdir(parents=True, exist_ok=True)
    static_out = out_dir / "static"
    if static_out.exists():
        shutil.rmtree(static_out)
    shutil.copytree(SITE_DIR / "static", static_out)

    by_id = {s.id: s for s in catalog}
    statuses = compute_status(store, catalog, now_utc)
    by_status = {s.station_id: s for s in statuses}
    today = local_day(now_utc)
    views = {st.id: _station_view(st, by_status[st.id], store, today) for st in catalog}
    for sid, ref in _city_refs(refs_path, catalog).items():
        views[sid].ref = ref
    river_text = river_summary(statuses, catalog)
    fresh = [s.latest_time_utc for s in statuses if not s.stale and s.latest_time_utc is not None]
    last_data = max(fresh) if fresh else None
    indices = store.indices("RONI") + store.indices("ONI")
    fc_views, ver_views = _forecast_views(store, by_id, verify_production(store, catalog))
    lag_results = run_production_lags(store, catalog)

    common = {
        **_common(base),
        "generated": f"Página generada el {day_long(today)} a las {hour_local(now_utc)}, hora de Montevideo.",
        "disclaimer": DISCLAIMER_SHORT,
        "open_meteo_url": OPEN_METEO_URL,
        "open_meteo_text": OPEN_METEO_TEXT,
        "copernicus": copernicus_notice(today.year),
    }
    pages = {
        "index.html": ("index.html.j2", {
            "nav": "home",
            "enso": enso_summary(indices),
            "enso_rows": _enso_rows(store),
            "outlook": _outlook(outlook_path),
            "river_text": river_text,
        }),
        "rio/index.html": ("rio.html.j2", {
            "nav": "rio",
            "river_text": river_text,
            "profile": Markup(charts.profile_svg(statuses, catalog)),
            "snapshot_url": f"{base}/snapshots/{today.isoformat()}/",
            "last_data": (f"Último dato del río: {day_long(local_day(last_data))} a las {hour_local(last_data)}."
                          if last_data else "No hay datos del río al día."),
            "cities": [views[st.id] for st in sorted(catalog, key=lambda s: s.order) if st.represents],
            "cota_line": COTA_SOURCE_LINE,
            "column": _column(views, catalog),
            "table": [views[st.id] for st in sorted(catalog, key=lambda s: s.order)],
            "dam": _dam_view(store, today),
            "forecasts": fc_views,
            "verification": ver_views,
            "seasons": _season_views(store, catalog, by_id),
            "lags": _lag_views(lag_results, by_id),
            "lag_validated": all(r.validated for r in lag_results) and bool(lag_results),
        }),
        "rio/metodologia/index.html": ("metodologia.html.j2", {
            "nav": "rio",
            "body": Markup(render_md(method_path.read_text(encoding="utf-8"))),
        }),
        "datos/index.html": ("datos.html.j2", {
            "nav": "datos",
            "downloads": _downloads(store, catalog, now_utc),
            "source_terms": _source_terms(),
            "licences_url": f"{REPO_URL}/blob/main/{LICENCES_DOC}",
            "catalog_rows": _catalog_rows(catalog),
            "cota_sources": ", ".join(sorted({s.cota_source for s in catalog if s.cota_source})) or "ninguna",
            "cite_url": f"{SITE_ORIGIN}{base}/snapshots/{today.isoformat()}/",
            "today_long": day_long(today),
        }),
    }
    env = _env()
    written = []
    for rel, (template, ctx) in pages.items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(env.get_template(template).render(**common, **ctx), encoding="utf-8")
        written.append(path)
    return written
