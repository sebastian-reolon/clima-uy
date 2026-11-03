import pytest

from climauy.mapa.gfm import GfmResult


@pytest.fixture(autouse=True)
def _no_live_gfm(monkeypatch):
    """Site builds in tests never query the satellite service: the default fetch says it failed."""
    monkeypatch.setattr("climauy.site.build._fetch_gfm", lambda *a, **kw: GfmResult("error", None, None, []))
