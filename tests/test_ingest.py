import gzip
import json
import dataclasses
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from climauy.catalog import load_catalog
from climauy.cli import main
from climauy.ingest import ingest_archive
from climauy.store import Store

FIX = Path(__file__).parent / "fixtures"
DAY = "2026-11-01"


def _put(root: Path, source: str, name: str, data: bytes, day: str = DAY, hms: str = "120000") -> Path:
    d = root / source / day
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{hms}Z_{name}.gz"
    p.write_bytes(gzip.compress(data))
    return p


def _fix(rel: str) -> bytes:
    return (FIX / rel).read_bytes()


def _telemetry(rows) -> bytes:
    body = "".join(
        f"<DadosHidrometereologicos><CodEstacao>1</CodEstacao><DataHora>{t} </DataHora>"
        f"<Vazao>{q}</Vazao><Nivel></Nivel><Chuva></Chuva></DadosHidrometereologicos>"
        for t, q in rows
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?><DataTable xmlns="http://MRCS/">'
        '<diffgr:diffgram xmlns:msdata="urn:schemas-microsoft-com:xml-msdata" '
        'xmlns:diffgr="urn:schemas-microsoft-com:xml-diffgram-v1">'
        f'<DocumentElement xmlns="">{body}</DocumentElement></diffgr:diffgram></DataTable>'
    ).encode()


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "t.sqlite") as s:
        yield s


@pytest.fixture
def catalog():
    return load_catalog()


def _full_archive(root: Path) -> int:
    _put(root, "ana", "77150000_uruguaiana.xml", _fix("ana/telemetry_77150000_uruguaiana.xml"))
    _put(root, "ina", "obs_72_series72.json", _fix("ina/obs_72_series72.json"))
    _put(root, "ina", "obs_72_series921.json", _fix("ina/obs_72_series921.json"))
    _put(root, "ina_prono", "cal704_series9646.json", _fix("ina/forecast_cal704_series9646.json"))
    _put(root, "ctm", "caudales_niveles.pdf", gzip.decompress(_fix("ctm/2026-11-01_caudales_niveles.pdf.gz")))
    _put(root, "glofas", "uruguaiana.json", gzip.decompress(_fix("glofas/uruguaiana.json.gz")))
    _put(root, "glofas", "salto.json", gzip.decompress(_fix("glofas/salto.json.gz")))
    _put(root, "cpc", "oni.txt", _fix("cpc/oni.ascii.txt"))
    _put(root, "cpc", "roni.txt", _fix("cpc/RONI.ascii.txt"))
    _put(root, "cpc", "wksst9120.txt", _fix("cpc/wksst9120.for"))
    return 10


def test_ingest_tmp_archive(tmp_path, store, catalog):
    n = _full_archive(tmp_path / "data")
    # Ignored sources must not count.
    _put(tmp_path / "data", "ina_meta", "estacion_1.json", b"{}")
    _put(tmp_path / "data", "adme", "spot.xlt", b"x")
    _put(tmp_path / "data", "ina", "estaciones_uruguay.json", b"[]")
    rep = ingest_archive(tmp_path / "data", store, catalog)
    assert rep.errors == []
    assert rep.files_new == n
    assert rep.rows > 0
    assert store.obs("ana_77150000", "discharge")
    assert store.obs("ina_72", "level") and store.obs("ina_72", "discharge")
    cells = {"uruguaiana": "-29.675,-57.075", "salto": "-31.375,-58.025"}
    for cell in cells.values():
        st = next(x for x in catalog if x.sources.get("glofas") == cell)
        assert store.forecasts("glofas", st.id), cell
    assert store.forecasts("ina", "ina_72")
    assert store.dam_days()
    assert store.indices("ONI") and store.indices("RONI") and store.indices("NINO34_WEEKLY")


def test_ingest_twice_no_duplicates(tmp_path, store, catalog):
    _full_archive(tmp_path / "data")
    first = ingest_archive(tmp_path / "data", store, catalog)
    count = len(store.obs("ina_72", "level")) + len(store.forecasts("ina")) + len(store.forecasts("glofas"))
    second = ingest_archive(tmp_path / "data", store, catalog)
    assert first.files_new > 0
    assert second.files_new == 0 and second.rows == 0
    assert second.files_seen == first.files_seen
    assert count == len(store.obs("ina_72", "level")) + len(store.forecasts("ina")) + len(store.forecasts("glofas"))


def test_overlapping_ana_windows_dedupe(tmp_path, store, catalog):
    times = [f"2026-10-30 {h:02d}:{m:02d}:00" for h in range(0, 10) for m in (0, 30)]  # 20 stamps
    a = [(t, 100 + i) for i, t in enumerate(times[:15])]
    b = [(t, 100 + i) for i, t in enumerate(times[4:], start=4)]  # 11 shared with a
    root = tmp_path / "data"
    _put(root, "ana", "77150000_uruguaiana.xml", _telemetry(a), hms="100000")
    _put(root, "ana", "77150000_uruguaiana.xml", _telemetry(b), hms="110000")
    rep = ingest_archive(root, store, catalog)
    assert rep.files_new == 2
    assert rep.rows == len(a) + len(b)
    assert len(store.obs("ana_77150000", "discharge")) == len(set(times))


def test_bad_file_reported_not_fatal(tmp_path, store, catalog):
    root = tmp_path / "data"
    bad = _put(root, "ctm", "caudales_niveles.pdf", b"not a pdf", hms="100000")
    _put(root, "ina", "obs_72_series72.json", _fix("ina/obs_72_series72.json"))
    rep = ingest_archive(root, store, catalog)
    assert rep.files_new == 1
    assert len(rep.errors) == 1 and "ctm/2026-11-01/100000Z_caudales_niveles.pdf.gz" in rep.errors[0]
    assert store.obs("ina_72", "level")
    # The failed file is retried, not marked processed.
    rep2 = ingest_archive(root, store, catalog)
    assert len(rep2.errors) == 1 and rep2.files_new == 0
    bad.unlink()
    assert ingest_archive(root, store, catalog).errors == []


def test_ina_forecast_series_mapped_to_station(tmp_path, store, catalog):
    _put(tmp_path / "data", "ina_prono", "cal704_series9646.json", _fix("ina/forecast_cal704_series9646.json"))
    ingest_archive(tmp_path / "data", store, catalog)
    fc = store.forecasts("ina")
    assert fc and {f.station_id for f in fc} == {"ina_72"} and {f.variable for f in fc} == {"level"}


def test_unmapped_series_skipped_silently(tmp_path, store, catalog):
    root = tmp_path / "data"
    _put(root, "ina", "obs_999_series999999.json", _fix("ina/obs_72_series72.json"))
    _put(root, "ina_prono", "cal433_series26203.json", _fix("ina/forecast_cal704_series9646.json"))
    rep = ingest_archive(root, store, catalog)
    assert rep.errors == [] and rep.files_new == 0 and rep.files_skipped == 2


def test_fetched_utc_comes_from_filename(tmp_path, store, catalog):
    root = tmp_path / "data"
    raw = gzip.decompress(_fix("glofas/salto.json.gz"))
    _put(root, "glofas", "salto.json", raw, day="2026-11-01", hms="235900")
    _put(root, "glofas", "salto.json", raw, day="2026-11-02", hms="000100")
    ingest_archive(root, store, catalog)
    issued = {f.issued_utc.date().isoformat() for f in store.forecasts("glofas")}
    assert issued == {"2026-11-01", "2026-11-02"}


def _series(values, start=datetime(2026, 10, 1), step=30, first=0) -> bytes:
    return _telemetry(
        [((start + timedelta(minutes=step * i)).strftime("%Y-%m-%d %H:%M:%S"), f"{v:.1f}") for i, v in enumerate(values, first)]
    )


def test_lone_spike_is_stored_as_suspect(tmp_path, store, catalog):
    vals = [100.0] * 20
    vals[10] = 5000.0
    _put(tmp_path / "data", "ana", "77150000_uruguaiana.xml", _series(vals))
    ingest_archive(tmp_path / "data", store, catalog)
    rows = store.obs("ana_77150000", "discharge", include_suspect=True)
    assert [o.quality for o in rows].count("suspect") == 1
    assert next(o for o in rows if o.value == 5000.0).quality == "suspect"
    assert len(store.obs("ana_77150000", "discharge")) == 19


@pytest.mark.parametrize("a_first", [True, False])
def test_quality_independent_of_ingest_order(tmp_path, catalog, a_first):
    full = [100.0 * 1.6**i for i in range(40)]  # steep rising limb
    full_a = list(full[:25])
    full_a[5] = 1e9  # spike only in window A
    a = _series(full_a)
    # B starts at reading 8 so the steep limb sits on its truncated left edge.
    b = _series(full[8:], first=8)
    with Store(tmp_path / f"{a_first}.sqlite") as st:
        root = tmp_path / f"data{a_first}"
        _put(root, "ana", "77150000_uruguaiana.xml", a, hms="100000" if a_first else "110000")
        _put(root, "ana", "77150000_uruguaiana.xml", b, hms="110000" if a_first else "100000")
        ingest_archive(root, st, catalog)
        rows = st.obs("ana_77150000", "discharge", include_suspect=True)
    q = {o.time_utc: o.quality for o in rows}
    # Reference: the whole merged series flagged once.
    from climauy.parsers import flag_outliers
    from climauy.store import Obs
    merged = [Obs("x", "discharge", t, v, "ana") for t, v in zip(sorted(q), [full_a[i] if i < 25 else full[i] for i in range(40)])]
    expected = {o.time_utc: o.quality for o in flag_outliers(merged)}
    assert q == expected
    assert q[sorted(q)[5]] == "suspect"
    assert q[sorted(q)[8]] == "ok" and q[sorted(q)[24]] == "ok"


def test_second_run_does_not_read_ledgered_files(tmp_path, store, catalog, monkeypatch):
    root = tmp_path / "data"
    _full_archive(root)
    _put(root, "adme", "spot.xlt", b"x")
    _put(root, "ina", "obs_999_series999999.json", _fix("ina/obs_72_series72.json"))
    reads = []
    orig = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: (reads.append(self), orig(self))[1])
    ingest_archive(root, store, catalog)
    assert reads
    reads.clear()
    rep = ingest_archive(root, store, catalog)
    assert reads == [] and rep.files_new == 0 and rep.files_skipped == 1


def test_skipped_file_reevaluated_when_catalog_changes(tmp_path, store, catalog):
    root = tmp_path / "data"
    _put(root, "ina_prono", "cal433_series26203.json", _fix("ina/forecast_cal704_series9646.json"))
    assert ingest_archive(root, store, catalog).files_new == 0
    changed = [dataclasses.replace(s, forecast_series=s.forecast_series + ["26203"]) if s.id == "ina_72" else s for s in catalog]
    rep = ingest_archive(root, store, changed)
    assert rep.files_new == 1 and rep.files_skipped == 0
    assert store.forecasts("ina", "ina_72")


def test_unreadable_file_goes_to_errors(tmp_path, store, catalog, monkeypatch):
    root = tmp_path / "data"
    bad = _put(root, "ina", "obs_72_series72.json", _fix("ina/obs_72_series72.json"), hms="100000")
    _put(root, "ina", "obs_72_series921.json", _fix("ina/obs_72_series921.json"), hms="110000")
    orig = Path.read_bytes

    def flaky(self):
        if self == bad:
            raise PermissionError("denied")
        return orig(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    rep = ingest_archive(root, store, catalog)
    assert rep.files_new == 1 and len(rep.errors) == 1 and "PermissionError" in rep.errors[0]


def test_cli_ingest_prints_report_and_exit_codes(tmp_path, monkeypatch, capsys):
    root = tmp_path / "data"
    _put(root, "ina", "obs_72_series72.json", _fix("ina/obs_72_series72.json"))
    monkeypatch.setenv("CLIMAUY_ARCHIVE_DIR", str(root))
    monkeypatch.setenv("CLIMAUY_DB", str(tmp_path / "c.sqlite"))
    monkeypatch.setenv("CLIMAUY_OUT", str(tmp_path / "out"))
    assert main(["ingest"]) == 0
    rep = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rep["files_new"] == 1 and rep["errors"] == []
    # Only failing files in a fresh DB: non-zero exit.
    shutil.rmtree(root)
    _put(root, "ctm", "caudales_niveles.pdf", b"junk")
    monkeypatch.setenv("CLIMAUY_DB", str(tmp_path / "d.sqlite"))
    assert main(["ingest"]) == 1
    # Same failure again with nothing new: still reported, but a quiet run exits 0.
    capsys.readouterr()
    assert main(["ingest"]) == 0
    assert len(json.loads(capsys.readouterr().out.strip().splitlines()[-1])["errors"]) == 1
