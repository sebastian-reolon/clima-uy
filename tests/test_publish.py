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
    PublishError, commit_and_push, export_data, find_infra, full_export_due, mark_full_export, scan_tree,
    write_snapshot,
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
def all_sources(monkeypatch):
    """Republish every source in full (the default is the conservative licence setting)."""
    from climauy import publish

    monkeypatch.setattr(publish, "FULL_SERIES_SOURCES", frozenset({"ana", "ana_hist", "ina", "ctm"}))
    monkeypatch.setattr(publish, "FULL_FORECAST_ISSUERS", frozenset({"ina", "glofas"}))


def test_default_licence_setting_publishes_every_source():
    from climauy import publish

    assert publish.FULL_SERIES_SOURCES == {"ana", "ana_hist", "ina", "ctm"}
    assert publish.FULL_FORECAST_ISSUERS == {"ina", "glofas"}


@pytest.fixture
def seeded(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    _seed(store)
    yield store, tmp_path / "out"
    store.close()


def _rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def test_export_csv_headers(seeded, all_sources):
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
    assert by_id["ina_74"]["latest_time_utc"] == "2026-12-02T15:00:00Z"
    assert by_id["ina_74"]["latest_source"] == "ina"
    # Newest reading among the stations that are up to date.
    assert latest["last_data_utc"] == "2026-12-02T15:00:00Z"  # Uruguaiana (30/11) is stale
    assert not (data / "rio/latest.json.tmp").exists()

    daily = {r[0]: r for r in _rows(data / "rio/daily/ina_74_level.csv")[1:]}
    # The local day in progress (2/12) is not exported until it ends.
    assert "2026-12-01" in daily and "2026-12-02" not in daily
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


def test_raw_monthly_append_only(seeded, all_sources):
    store, out = seeded
    nov, fc_nov = out / "data/rio/raw/2026-11.csv", out / "data/rio/forecasts/2026-11.csv"
    late = lambda day, hour, v: Obs("ina_74", "level", datetime(2026, 11, day, hour, tzinfo=UTC), v, "ina")
    # 29/11 at 09:00 in Montevideo: November is the current month, rewritten on every run.
    nov_29 = datetime(2026, 11, 29, 12, tzinfo=UTC)
    export_data(store, load_catalog(), out, nov_29, full=True)
    store.upsert_obs([late(30, 20, 6.0)])
    # 1/12 at 00:40: first run after November closed, one final rewrite with the late reading.
    dec_1 = datetime(2026, 12, 1, 3, 40, tzinfo=UTC)
    written = export_data(store, load_catalog(), out, dec_1, full=True)
    assert nov in written and fc_nov in written
    assert any(r[2] == "2026-11-30T20:00:00Z" for r in _rows(nov))
    frozen = {p: p.read_bytes() for p in (nov, fc_nov)}
    # Anything after that never touches November again.
    store.upsert_obs([late(30, 22, 6.2), Obs("ina_74", "level", datetime(2026, 12, 2, 11, tzinfo=UTC), 6.1, "ina")])
    written = export_data(store, load_catalog(), out, NOW, full=True)
    assert nov not in written and fc_nov not in written
    assert {p: p.read_bytes() for p in (nov, fc_nov)} == frozen
    dec = out / "data/rio/raw/2026-12.csv"
    assert dec in written and any(r[2] == "2026-12-02T11:00:00Z" for r in _rows(dec))


def test_closed_month_written_once_when_first_seen(seeded, all_sources):
    store, out = seeded
    nov = out / "data/rio/raw/2026-11.csv"
    assert nov in export_data(store, load_catalog(), out, NOW, full=True)
    first = nov.read_bytes()
    store.upsert_obs([Obs("ina_74", "level", datetime(2026, 11, 30, 22, tzinfo=UTC), 6.2, "ina")])
    assert nov not in export_data(store, load_catalog(), out, NOW + timedelta(days=1), full=True)
    assert nov.read_bytes() == first


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
    for rel in ("index.html", "rio/index.html", "rio/crecida/index.html", "nino/index.html", "rio/metodologia/index.html",
                "data/rio/latest.json", "data/rio/crecida-60d.csv"):
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
    # The home, the river, its methodology and El Niño.
    assert sorted(str(p.relative_to(snap)) for p in pages) == [
        "index.html", "nino/index.html", "rio/crecida/index.html", "rio/index.html", "rio/metodologia/index.html"]
    assert (snap / "data/rio/crecida-60d.csv").is_file()
    for page in pages:
        assert 'Hecho por <a href="https://github.com/sebastian-reolon">Sebastián Reolon</a>' in page.read_text(
            encoding="utf-8"), page
        for rel in _targets(page, out):
            # The Datos menu entry goes to the list of snapshots: the Datos page itself is live,
            # and so are the city maps, which snapshots do not copy.
            assert (rel.startswith(day) or rel.startswith("static/") or rel == "snapshots/"
                    or rel.startswith("rio/mapa/")), f"{page}: {rel}"
            assert _resolves(out, rel), f"{page}: {rel}"
    index = (out / "snapshots/index.html").read_text(encoding="utf-8")
    assert 'Hecho por <a href="https://github.com/sebastian-reolon">Sebastián Reolon</a>' in index
    for rel in _targets(out / "snapshots/index.html", out):
        assert _resolves(out, rel), rel


def test_live_pages_links_resolve(built):
    out = built
    write_snapshot(out, local_day(NOW), base_url=BASE)
    for page in ("index.html", "rio/index.html", "rio/crecida/index.html", "nino/index.html",
                 "rio/metodologia/index.html", "datos/index.html"):
        for rel in _targets(out / page, out):
            assert _resolves(out, rel), f"{page}: {rel}"


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def git_env(monkeypatch):
    """No global or system git config; commit identity only from the CLIMAUY_GIT_* variables."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("CLIMAUY_GIT_NAME", "clima-uy bot")
    monkeypatch.setenv("CLIMAUY_GIT_EMAIL", "bot@example.invalid")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
              "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(k, raising=False)


def _pages_repo(tmp_path: Path, branch: str = "gh-pages") -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-q", str(origin))
    pages = tmp_path / "pages"
    pages.mkdir()
    _git(pages, "init", "-q", "-b", branch)
    _git(pages, "config", "commit.gpgsign", "false")
    _git(pages, "remote", "add", "origin", str(origin))
    return pages, origin


def test_commit_noop_when_unchanged(tmp_path, git_env):
    pages, origin = _pages_repo(tmp_path)
    (pages / "index.html").write_text("one", encoding="utf-8")

    assert commit_and_push(pages, "Build 1") is True
    first = _git(origin, "rev-parse", "gh-pages").strip()
    assert first == _git(pages, "rev-parse", "HEAD").strip()
    assert _git(pages, "log", "-1", "--format=%an <%ae>").strip() == "clima-uy bot <bot@example.invalid>"

    assert commit_and_push(pages, "Build 2") is False
    assert _git(pages, "rev-list", "--count", "HEAD").strip() == "1"

    (pages / "index.html").write_text("two", encoding="utf-8")
    assert commit_and_push(pages, "Build 3") is True
    assert _git(origin, "rev-parse", "gh-pages").strip() != first
    assert _git(origin, "log", "-1", "--format=%s", "gh-pages").strip() == "Build 3"


def test_commit_refuses_dir_nested_in_another_repo(tmp_path, git_env):
    parent = tmp_path / "parent"
    parent.mkdir()
    _git(parent, "init", "-q", "-b", "gh-pages")
    pages = parent / "out"
    pages.mkdir()
    (pages / "index.html").write_text("x", encoding="utf-8")
    with pytest.raises(PublishError, match="not the top"):
        commit_and_push(pages, "Build")
    assert _git(parent, "status", "--porcelain").strip() == "?? out/"  # nothing staged
    assert subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=parent).returncode != 0  # no commit


def test_commit_refuses_other_branch(tmp_path, git_env):
    pages, _ = _pages_repo(tmp_path, branch="main")
    (pages / "index.html").write_text("x", encoding="utf-8")
    with pytest.raises(PublishError, match="gh-pages"):
        commit_and_push(pages, "Build")
    assert _git(pages, "status", "--porcelain").strip() == "?? index.html"


def test_commit_ignores_inherited_git_dir(tmp_path, git_env, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q", "-b", "gh-pages")
    pages, origin = _pages_repo(tmp_path)
    (pages / "index.html").write_text("x", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert commit_and_push(pages, "Build") is True
    assert _git(origin, "log", "-1", "--format=%s", "gh-pages").strip() == "Build"
    assert subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=other,
                          env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")}).returncode != 0


def test_commit_identity_ignores_inherited_author_and_committer(tmp_path, git_env, monkeypatch):
    pages, origin = _pages_repo(tmp_path)
    (pages / "index.html").write_text("x", encoding="utf-8")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "intruder")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "intruder@example.invalid")
    assert commit_and_push(pages, "Build") is True
    who = _git(origin, "log", "-1", "--format=%an <%ae>|%cn <%ce>", "gh-pages").strip()
    assert who == "clima-uy bot <bot@example.invalid>|clima-uy bot <bot@example.invalid>"


def test_cli_all_refuses_when_scan_fails(tmp_path, monkeypatch):
    from climauy import cli, publish

    (tmp_path / "archive").mkdir()
    monkeypatch.setenv("CLIMAUY_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("CLIMAUY_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("CLIMAUY_OUT", str(tmp_path / "pages"))
    monkeypatch.setenv("CLIMAUY_PAGES_PUSH", "1")
    monkeypatch.setattr(cli, "_now", lambda: NOW)

    def unreadable(root):
        raise PermissionError("unreadable file")

    pushed = []
    monkeypatch.setattr(publish, "scan_tree", unreadable)
    monkeypatch.setattr(publish, "commit_and_push", lambda *a: pushed.append(a) or True)
    assert cli.main(["all"]) == 1
    assert pushed == []


@pytest.mark.parametrize("missing", ["CLIMAUY_GIT_NAME", "CLIMAUY_GIT_EMAIL"])
def test_commit_requires_identity_from_env(tmp_path, git_env, monkeypatch, missing):
    pages, _ = _pages_repo(tmp_path)
    _git(pages, "config", "user.name", "someone")  # a configured identity is not enough
    _git(pages, "config", "user.email", "someone@example.invalid")
    (pages / "index.html").write_text("x", encoding="utf-8")
    monkeypatch.delenv(missing)
    with pytest.raises(PublishError, match="CLIMAUY_GIT_NAME and CLIMAUY_GIT_EMAIL"):
        commit_and_push(pages, "Build")
    assert subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=pages).returncode != 0


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


def test_public_tree_has_no_infra(built):
    out = built
    write_snapshot(out, local_day(NOW), base_url=BASE)
    assert scan_tree(out) == []
    assert find_infra(_repo_files()) == []


def _ip(*octets: int) -> str:
    """Addresses are assembled at run time so this file never contains one."""
    return ".".join(str(o) for o in octets)


@pytest.mark.parametrize("text,hit", [
    ("server at " + _ip(10, 0, 0, 1) + ".", True),       # an address ending a sentence
    ("see " + _ip(192, 168, 1, 20) + ", then", True),
    ("x " + _ip(10, 0, 0, 1) + " y", True),
    ("version 1.2.3", False),
    ("lat -27.5206", False),
    ("1.2.3.4.5", False),
    ("10.0.0.256", False),
    ("path /ro" + "ot/clima", True),
    ("HET" + "ZNER box", True),
    ("someone@GM" + "AIL.com", True),
    ("TELE" + "GRAM_TOKEN", True),
])
def test_infra_patterns(tmp_path, text, hit):
    f = tmp_path / "f.txt"
    f.write_text("first line\n" + text + "\n", encoding="utf-8")
    hits = find_infra([f])
    assert bool(hits) == hit
    if hit:
        assert hits[0].startswith(f"{f}:2: ")


def test_scan_tree_skips_git_dir(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("url = " + _ip(10, 0, 0, 1), encoding="utf-8")
    (tmp_path / "ok.html").write_text("nothing", encoding="utf-8")
    assert scan_tree(tmp_path) == []
    (tmp_path / "leak.html").write_text(_ip(10, 0, 0, 1), encoding="utf-8")
    assert len(scan_tree(tmp_path)) == 1


def test_stale_daily_files_are_removed(seeded, all_sources, monkeypatch):
    from climauy import publish

    store, out = seeded
    export_data(store, load_catalog(), out, NOW, full=True)
    ina = out / "data/rio/daily/ina_74_level.csv"
    assert ina.is_file()
    (out / "data/rio/daily/gone_station_level.csv").write_text("x", encoding="utf-8")
    monkeypatch.setattr(publish, "FULL_SERIES_SOURCES", frozenset({"ana", "ana_hist"}))
    export_data(store, load_catalog(), out, NOW, full=True)
    left = sorted(p.name for p in (out / "data/rio/daily").glob("*.csv"))
    assert left == ["ana_77150000_discharge.csv"]


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


DERIVED_SENTENCE = "no se ofrecen para descargar, porque"


@pytest.mark.parametrize("restricted", [False, True])
def test_licence_switch_drops_series_files_and_says_so(seeded, monkeypatch, restricted):
    from climauy import publish

    if restricted:  # what withdrawing INA and CTM on request looks like
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
        assert "pronósticos del INA se ven en la página del río pero no se ofrecen para descargar" in datos
        assert not (out / "data/rio/daily/ina_74_level.csv").exists()
        assert "ina_74_level.csv" not in datos
        assert nov_raw and {r[4] for r in nov_raw} == {"ana"}
        assert nov_fc == []
    else:
        assert DERIVED_SENTENCE not in datos
        assert (out / "data/rio/daily/ina_74_level.csv").is_file()
        assert "ina" in {r[4] for r in nov_raw} and nov_fc
    # The crecida matrix keeps its percentiles; the day means of a withdrawn source go blank.
    crecida = [r for r in _rows(out / "data/rio/crecida-60d.csv")[1:] if r[1] == "ina_74"]
    assert crecida and all(r[6] for r in crecida)
    assert all(r[5] == "" for r in crecida) if restricted else all(float(r[5]) > 0 for r in crecida)
    for page in ("index.html", "rio/index.html", "datos/index.html"):
        for rel in _targets(out / page, out):
            assert _resolves(out, rel), f"{page}: {rel}"
    # Derived states of every station stay in latest.json.
    latest = json.loads((out / "data/rio/latest.json").read_text(encoding="utf-8"))
    assert "ina_74" in {s["station_id"] for s in latest["stations"]}


def test_cli_all_refuses_to_publish_on_infra_hit(tmp_path, monkeypatch):
    from climauy import cli, publish

    (tmp_path / "archive").mkdir()
    monkeypatch.setenv("CLIMAUY_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("CLIMAUY_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("CLIMAUY_OUT", str(tmp_path / "pages"))
    monkeypatch.setenv("CLIMAUY_PAGES_PUSH", "1")
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    real_export = publish.export_data

    def leaky_export(store, catalog, out_dir, now_utc, full):
        written = real_export(store, catalog, out_dir, now_utc, full)
        (out_dir / "data/leak.txt").write_text("ok\nhost " + _ip(10, 20, 30, 40) + ".\n", encoding="utf-8")
        return written

    pushed = []
    monkeypatch.setattr(publish, "export_data", leaky_export)
    monkeypatch.setattr(publish, "commit_and_push", lambda *a: pushed.append(a) or True)
    assert cli.main(["all"]) == 1
    assert pushed == []


def test_last_data_utc_null_when_every_station_is_stale(tmp_path):
    with Store(tmp_path / "t.sqlite") as store:
        store.upsert_obs([Obs("ina_74", "level", datetime(2026, 11, 1, 15, tzinfo=UTC), 4.0, "ina")])
        export_data(store, load_catalog(), tmp_path / "out", NOW, full=False)
    latest = json.loads((tmp_path / "out/data/rio/latest.json").read_text(encoding="utf-8"))
    assert latest["last_data_utc"] is None


def _cli_env(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir(exist_ok=True)
    monkeypatch.setenv("CLIMAUY_ARCHIVE_DIR", str(archive))
    monkeypatch.setenv("CLIMAUY_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("CLIMAUY_OUT", str(tmp_path / "pages"))
    monkeypatch.setenv("CLIMAUY_PAGES_PUSH", "1")
    return archive


def test_failed_push_on_month_close_rewrites_the_month_next_run(tmp_path, monkeypatch):
    from climauy import cli, publish

    _cli_env(tmp_path, monkeypatch)
    with Store(tmp_path / "t.sqlite") as store:
        _seed(store)
    out = tmp_path / "pages"
    nov = out / "data/rio/raw/2026-11.csv"
    late = Obs("ina_74", "level", datetime(2026, 11, 30, 23, tzinfo=UTC), 6.4, "ina")

    def fail(*a):
        raise subprocess.CalledProcessError(1, ["git", "push"])

    # 1/12 00:40 in Montevideo: November closes and gets its final write, but the push fails.
    dec_1 = datetime(2026, 12, 1, 3, 40, tzinfo=UTC)
    monkeypatch.setattr(cli, "_now", lambda: dec_1)
    monkeypatch.setattr(publish, "commit_and_push", fail)
    assert cli.main(["all"]) == 1
    with Store(tmp_path / "t.sqlite") as store:
        assert store.meta_get("month_final:raw:2026-11") is None
        assert full_export_due(store, dec_1 + timedelta(hours=3))  # the full export is not marked either
        store.upsert_obs([late])  # arrives before the next run

    # Next run pushes: November is written again (with the late reading) and only now frozen.
    pushed = []
    monkeypatch.setattr(cli, "_now", lambda: dec_1 + timedelta(hours=3))
    monkeypatch.setattr(publish, "commit_and_push", lambda *a: pushed.append(a) or True)
    assert cli.main(["all"]) == 0 and pushed
    assert any(r[2] == "2026-11-30T23:00:00Z" for r in _rows(nov))
    with Store(tmp_path / "t.sqlite") as store:
        assert store.meta_get("month_final:raw:2026-11") == "1"
        assert store.meta_get("month_final:forecasts:2026-11") == "1"
        assert not full_export_due(store, dec_1 + timedelta(hours=4))


def test_marks_are_kept_when_push_is_disabled(tmp_path, monkeypatch):
    from climauy import cli

    _cli_env(tmp_path, monkeypatch)
    monkeypatch.delenv("CLIMAUY_PAGES_PUSH")
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    assert cli.main(["all"]) == 0
    with Store(tmp_path / "t.sqlite") as store:
        assert store.meta_get("month_final:raw:2026-11") == "1"
        assert not full_export_due(store, NOW + timedelta(hours=1))


def test_cli_all_exits_0_when_only_an_ingest_file_failed(tmp_path, monkeypatch, caplog):
    from climauy import cli, publish

    archive = _cli_env(tmp_path, monkeypatch)
    bad = archive / "ctm" / "2026-12-02"
    bad.mkdir(parents=True)
    import gzip as _gz
    (bad / "090000Z_caudales_niveles.pdf.gz").write_bytes(_gz.compress(b"junk"))
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    monkeypatch.setattr(publish, "commit_and_push", lambda *a: True)
    with caplog.at_level("WARNING"):
        assert cli.main(["all"]) == 0
    assert "ingest" in caplog.text and "caudales_niveles" in caplog.text
