import gzip
import json
import shutil
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
    assert store.forecasts("glofas", "ana_77150000") or store.forecasts("glofas")
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
    _put(root, "glofas", "salto.json", gzip.decompress(_fix("glofas/salto.json.gz")), day="2026-11-01", hms="235900")
    ingest_archive(root, store, catalog)
    fc = store.forecasts("glofas")
    assert fc and {f.issued_utc.isoformat() for f in fc} == {"2026-11-01T00:00:00+00:00"}


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
