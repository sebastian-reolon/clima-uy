import csv
import json
import os
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from climauy.catalog import load_catalog
from climauy.publish import (
    commit_and_push, export_data, full_export_due, mark_full_export, write_snapshot,
)
from climauy.site.build import build_site
from climauy.store import Forecast, IndexValue, Obs, Store
from climauy.timeutil import local_day

UTC = timezone.utc
NOW = datetime(2026, 12, 2, 12, tzinfo=UTC)  # 09:00 on 2/12 in Montevideo
REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = "/clima-uy"


def _seed(store: Store) -> None:
    # Monte Caseros: 12 years of daily INA level, plus two readings on one day.
    start = date(2014, 11, 1)
    rows = []
    for i in range((NOW.date() - start).days + 1):
        d = start + timedelta(days=i)
        rows.append(Obs("ina_74", "level", datetime(d.year, d.month, d.day, 15, tzinfo=UTC), 4.0 + (i % 7) / 10, "ina"))
    rows.append(Obs("ina_74", "level", datetime(2026, 11, 20, 18, tzinfo=UTC), 9.0, "ina"))
    # 03:00Z on 1/12 is midnight in Montevideo: belongs to December locally.
    rows.append(Obs("ina_74", "level", datetime(2026, 12, 1, 3, tzinfo=UTC), 5.5, "ina"))
    # 02:59Z on 1/11 is still 31/10 locally: before the archive, not in any raw month.
    rows.append(Obs("ina_74", "level", datetime(2026, 11, 1, 2, 59, tzinfo=UTC), 5.1, "ina"))
    rows.append(Obs("ina_74", "level", datetime(2026, 11, 21, 15, 30, tzinfo=UTC), 30.0, "ina", "suspect"))
    store.upsert_obs(rows)
    # Uruguaiana: ANA telemetry only.
    store.upsert_obs([Obs("ana_77150000", "discharge", datetime(2026, 11, 30, h, tzinfo=UTC), 9000.0 + h, "ana")
                      for h in range(0, 24, 6)])
    issued = datetime(2026, 11, 30, 12, tzinfo=UTC)
    store.upsert_forecasts([
        Forecast("ina", "ina_72", "level", issued, issued + timedelta(days=k), "main", 7.0 + k / 10) for k in (1, 2)
    ])
    store.upsert_indices([IndexValue("RONI", "2026-ASO", 1.7), IndexValue("ONI", "2026-ASO", 2.1),
                          IndexValue("NINO34_WEEKLY", "2026-11-25", 2.4)])


@pytest.fixture
def seeded(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    _seed(store)
    yield store, tmp_path / "out"
    store.close()


def _rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def test_export_csv_headers(seeded):
    store, out = seeded
    written = export_data(store, load_catalog(), out, NOW, full=True)
    data = out / "data"
    assert data / "rio/stations.csv" in written
    assert _rows(data / "rio/stations.csv")[0][:4] == ["id", "name", "country", "river"]
    assert _rows(data / "rio/daily/ina_74_level.csv")[0] == ["date_local", "value", "n_readings"]
    assert _rows(data / "rio/raw/2026-11.csv")[0] == ["station_id", "variable", "time_utc", "value", "source", "quality"]
    assert _rows(data / "rio/forecasts/2026-11.csv")[0] == [
        "issuer", "station_id", "variable", "issued_utc", "valid_utc", "qualifier", "value"]
    assert _rows(data / "enso/indices.csv")[0] == ["name", "period", "value"]

    latest = json.loads((data / "rio/latest.json").read_text(encoding="utf-8"))
    assert latest["generated_utc"] == "2026-12-02T12:00:00Z"
    assert isinstance(latest["summary"], str)
    by_id = {s["station_id"]: s for s in latest["stations"]}
    assert by_id["ina_74"]["name"] == "Monte Caseros"
    assert by_id["ina_74"]["latest_time_utc"].endswith("Z")

    daily = {r[0]: r for r in _rows(data / "rio/daily/ina_74_level.csv")[1:]}
    assert daily["2026-11-20"][2] == "2"  # two readings that day
    assert float(daily["2026-11-21"][1]) < 10  # the suspect 30 m is not in the mean
    assert daily["2026-11-21"][2] == "1"

    nov = _rows(data / "rio/raw/2026-11.csv")[1:]
    dec = _rows(data / "rio/raw/2026-12.csv")[1:]
    assert ["ina_74", "level", "2026-11-21T15:30:00Z", "30.0", "ina", "suspect"] in nov
    assert any(r[2] == "2026-12-01T03:00:00Z" for r in dec)
    assert not any(r[2] == "2026-12-01T03:00:00Z" for r in nov)
    assert not any(r[2] == "2026-11-01T02:59:00Z" for r in nov + dec)
    assert all(r[2] >= "2026-11-01T03:00:00Z" for r in nov)
    assert len(_rows(data / "rio/forecasts/2026-11.csv")) == 3
    assert _rows(data / "rio/forecasts/2026-12.csv") == [
        ["issuer", "station_id", "variable", "issued_utc", "valid_utc", "qualifier", "value"]]


def test_light_export_skips_full_files(seeded):
    store, out = seeded
    written = export_data(store, load_catalog(), out, NOW, full=False)
    assert {p.relative_to(out).as_posix() for p in written} == {"data/rio/stations.csv", "data/rio/latest.json"}
    assert not (out / "data/rio/raw").exists()


def test_raw_monthly_append_only(seeded):
    store, out = seeded
    export_data(store, load_catalog(), out, NOW, full=True)
    nov, dec = out / "data/rio/raw/2026-11.csv", out / "data/rio/raw/2026-12.csv"
    fc_nov = out / "data/rio/forecasts/2026-11.csv"
    before = {p: p.read_bytes() for p in (nov, fc_nov)}
    # A late November reading and a new December one arrive.
    store.upsert_obs([Obs("ina_74", "level", datetime(2026, 11, 30, 20, tzinfo=UTC), 6.0, "ina"),
                      Obs("ina_74", "level", datetime(2026, 12, 2, 11, tzinfo=UTC), 6.1, "ina")])
    written = export_data(store, load_catalog(), out, NOW + timedelta(hours=1), full=True)
    assert nov not in written and fc_nov not in written
    assert {p: p.read_bytes() for p in (nov, fc_nov)} == before
    assert dec in written
    assert any(r[2] == "2026-12-02T11:00:00Z" for r in _rows(dec))


def test_full_export_due_once_per_day_after_0030(tmp_path):
    with Store(tmp_path / "t.sqlite") as store:
        # 00:20 in Montevideo on 2/12 with nothing done yet: first ever run is full.
        assert full_export_due(store, datetime(2026, 12, 2, 3, 20, tzinfo=UTC))
        mark_full_export(store, datetime(2026, 12, 1, 3, 40, tzinfo=UTC))  # done on 1/12
        assert not full_export_due(store, datetime(2026, 12, 2, 3, 20, tzinfo=UTC))  # 00:20, too early
        assert full_export_due(store, datetime(2026, 12, 2, 3, 30, tzinfo=UTC))  # 00:30 sharp
        mark_full_export(store, datetime(2026, 12, 2, 3, 30, tzinfo=UTC))
        assert not full_export_due(store, datetime(2026, 12, 2, 4, 30, tzinfo=UTC))
        assert not full_export_due(store, datetime(2026, 12, 3, 2, 0, tzinfo=UTC))  # 23:00 on 2/12
        assert full_export_due(store, datetime(2026, 12, 3, 4, 0, tzinfo=UTC))


@pytest.fixture
def built(seeded):
    store, out = seeded
    build_site(store, load_catalog(), out, NOW, base_url=BASE)
    export_data(store, load_catalog(), out, NOW, full=True)
    return out


def test_snapshot_dir_by_local_day(built):
    out = built
    now = datetime(2026, 11, 15, 2, tzinfo=UTC)  # 23:00 on 14/11 in Montevideo
    snap = write_snapshot(out, local_day(now), base_url=BASE)
    assert snap == out / "snapshots" / "2026-11-14"
    for rel in ("index.html", "rio/index.html", "rio/metodologia/index.html", "data/rio/latest.json"):
        assert (snap / rel).is_file(), rel
    # During the day it is overwritten; another day leaves it fixed.
    (out / "data/rio/latest.json").write_text('{"v": 2}', encoding="utf-8")
    write_snapshot(out, date(2026, 11, 14), base_url=BASE)
    assert json.loads((snap / "data/rio/latest.json").read_text(encoding="utf-8")) == {"v": 2}
    (out / "data/rio/latest.json").write_text('{"v": 3}', encoding="utf-8")
    write_snapshot(out, date(2026, 11, 15), base_url=BASE)
    assert json.loads((snap / "data/rio/latest.json").read_text(encoding="utf-8")) == {"v": 2}
    index = (out / "snapshots/index.html").read_text(encoding="utf-8")
    assert index.index("2026-11-15/") < index.index("2026-11-14/")  # newest first


_LINK = re.compile(r'\b(?:href|src)="([^"]*)"')


def _targets(page: Path, out: Path) -> list[str]:
    """Internal link paths of a page, relative to out (no base, no fragment)."""
    found = []
    for url in _LINK.findall(page.read_text(encoding="utf-8")):
        parts = urlsplit(url)
        if parts.scheme in ("http", "https", "mailto") or url.startswith("#"):
            continue
        assert parts.path.startswith(BASE + "/"), f"{page}: {url}"
        found.append(parts.path[len(BASE) + 1:])
    return found


def _resolves(out: Path, rel: str) -> bool:
    target = out / rel
    return (target / "index.html").is_file() if rel == "" or rel.endswith("/") else target.is_file()


def test_snapshot_links_stay_inside_snapshot(built):
    out = built
    snap = write_snapshot(out, date(2026, 12, 2), base_url=BASE)
    day = "snapshots/2026-12-02/"
    pages = list(snap.rglob("*.html"))
    assert len(pages) == 3
    for page in pages:
        for rel in _targets(page, out):
            # The Datos menu entry goes to the list of snapshots: the Datos page itself is live.
            assert rel.startswith(day) or rel.startswith("static/") or rel == "snapshots/", f"{page}: {rel}"
            assert _resolves(out, rel), f"{page}: {rel}"
    for rel in _targets(out / "snapshots/index.html", out):
        assert _resolves(out, rel), rel


def test_live_pages_links_resolve(built):
    out = built
    write_snapshot(out, local_day(NOW), base_url=BASE)
    for page in ("index.html", "rio/index.html", "rio/metodologia/index.html", "datos/index.html"):
        for rel in _targets(out / page, out):
            assert _resolves(out, rel), f"{page}: {rel}"


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


def test_commit_noop_when_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-q", str(origin))
    pages = tmp_path / "pages"
    pages.mkdir()
    _git(pages, "init", "-q", "-b", "gh-pages")
    for k, v in (("user.name", "test"), ("user.email", "test@example.invalid"), ("commit.gpgsign", "false")):
        _git(pages, "config", k, v)
    _git(pages, "remote", "add", "origin", str(origin))
    (pages / "index.html").write_text("one", encoding="utf-8")

    assert commit_and_push(pages, "Build 1") is True
    first = _git(origin, "rev-parse", "gh-pages").strip()
    assert first == _git(pages, "rev-parse", "HEAD").strip()

    assert commit_and_push(pages, "Build 2") is False
    assert _git(pages, "rev-list", "--count", "HEAD").strip() == "1"

    (pages / "index.html").write_text("two", encoding="utf-8")
    assert commit_and_push(pages, "Build 3") is True
    assert _git(origin, "rev-parse", "gh-pages").strip() != first
    assert _git(origin, "log", "-1", "--format=%s", "gh-pages").strip() == "Build 3"


# Built from pieces so this file does not match itself.
_FORBIDDEN = [
    re.compile(re.escape("/ro" + "ot/")),
    re.compile("het" + "zner", re.I),
    re.compile(re.escape("@gm" + "ail"), re.I),
    re.compile("tele" + "gram", re.I),
    re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\d.])"),
]


def _repo_files() -> list[Path]:
    """What would be pushed: tracked plus untracked-not-ignored files."""
    try:
        listing = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=REPO_ROOT, check=True, capture_output=True, text=True,
        ).stdout
        return [REPO_ROOT / p for p in listing.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        skip = {".git", ".venv"}
        return [p for p in REPO_ROOT.rglob("*") if p.is_file() and not skip & set(p.relative_to(REPO_ROOT).parts)]


def _scan(files) -> list[str]:
    hits = []
    for path in files:
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        for pat in _FORBIDDEN:
            m = pat.search(text)
            if m:
                hits.append(f"{path}: {m.group(0)!r}")
    return hits


def test_public_tree_has_no_infra(built):
    out = built
    write_snapshot(out, local_day(NOW), base_url=BASE)
    files = [p for p in out.rglob("*") if p.is_file()] + _repo_files()
    assert _scan(files) == []


def test_cli_all_builds_exports_and_snapshots_without_pushing(tmp_path, monkeypatch):
    from climauy import cli, publish

    archive, out = tmp_path / "archive", tmp_path / "pages"
    archive.mkdir()
    with Store(tmp_path / "t.sqlite") as store:
        _seed(store)
    monkeypatch.setenv("CLIMAUY_ARCHIVE_DIR", str(archive))
    monkeypatch.setenv("CLIMAUY_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("CLIMAUY_OUT", str(out))
    monkeypatch.delenv("CLIMAUY_PAGES_PUSH", raising=False)
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    pushed = []
    monkeypatch.setattr(publish, "commit_and_push", lambda *a: pushed.append(a) or True)

    assert cli.main(["all"]) == 0
    assert (out / "rio/index.html").is_file()
    assert (out / "data/rio/raw/2026-12.csv").is_file()
    assert (out / "snapshots/2026-12-02/rio/index.html").is_file()
    assert pushed == []
    with Store(tmp_path / "t.sqlite") as store:
        assert not full_export_due(store, NOW + timedelta(hours=1))

    monkeypatch.setenv("CLIMAUY_PAGES_PUSH", "1")
    assert cli.main(["all"]) == 0
    assert len(pushed) == 1 and pushed[0][0] == out


def test_cli_all_fails_when_push_fails(tmp_path, monkeypatch):
    from climauy import cli, publish

    (tmp_path / "archive").mkdir()
    monkeypatch.setenv("CLIMAUY_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("CLIMAUY_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("CLIMAUY_OUT", str(tmp_path / "pages"))
    monkeypatch.setenv("CLIMAUY_PAGES_PUSH", "1")
    monkeypatch.setattr(cli, "_now", lambda: NOW)

    def boom(*a):
        raise subprocess.CalledProcessError(1, ["git", "push"])

    monkeypatch.setattr(publish, "commit_and_push", boom)
    assert cli.main(["all"]) != 0


DERIVED_SENTENCE = "se publica solo lo derivado"


@pytest.mark.parametrize("restricted", [False, True])
def test_licence_switch_drops_series_files_and_says_so(seeded, monkeypatch, restricted):
    from climauy import publish

    if restricted:
        monkeypatch.setattr(publish, "FULL_SERIES_SOURCES", frozenset({"ana", "ana_hist"}))
        monkeypatch.setattr(publish, "FULL_FORECAST_ISSUERS", frozenset({"glofas"}))
    store, out = seeded
    build_site(store, load_catalog(), out, NOW, base_url=BASE)
    export_data(store, load_catalog(), out, NOW, full=True)
    write_snapshot(out, local_day(NOW), base_url=BASE)

    datos = (out / "datos/index.html").read_text(encoding="utf-8")
    nov_raw = _rows(out / "data/rio/raw/2026-11.csv")[1:]
    nov_fc = _rows(out / "data/rio/forecasts/2026-11.csv")[1:]
    assert (out / "data/rio/daily/ana_77150000_discharge.csv").is_file()
    if restricted:
        assert DERIVED_SENTENCE in datos and "INA y Prefectura Naval Argentina" in datos
        assert "los pronósticos del INA" in datos
        assert not (out / "data/rio/daily/ina_74_level.csv").exists()
        assert "ina_74_level.csv" not in datos
        assert nov_raw and {r[4] for r in nov_raw} == {"ana"}
        assert nov_fc == []
    else:
        assert DERIVED_SENTENCE not in datos
        assert (out / "data/rio/daily/ina_74_level.csv").is_file()
        assert "ina" in {r[4] for r in nov_raw} and nov_fc
    for page in ("index.html", "rio/index.html", "datos/index.html"):
        for rel in _targets(out / page, out):
            assert _resolves(out, rel), f"{page}: {rel}"
    # Derived states of every station stay in latest.json.
    latest = json.loads((out / "data/rio/latest.json").read_text(encoding="utf-8"))
    assert "ina_74" in {s["station_id"] for s in latest["stations"]}
