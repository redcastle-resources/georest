"""An unknown theme used to return no services, silently: the docstring's own
example (theme="recreation") found nothing."""
import pytest

from georest.usfs import edw


def test_an_unknown_theme_is_an_error_naming_the_valid_ones(monkeypatch):
    monkeypatch.setattr(edw.portal, "getServiceMetadata", lambda *a, **k: pytest.fail("checked before any request"))
    with pytest.raises(ValueError) as e:
        edw.search_edw_services(theme="recreation")
    for t in edw.VALID_THEMES:
        assert t in str(e.value)


def test_a_valid_theme_still_filters(monkeypatch):
    catalog = {"services": [{"name": "EDW/EDW_RoadBasic_01", "type": "MapServer"}]}
    monkeypatch.setattr(edw.portal, "getServiceMetadata", lambda *a, **k: catalog)
    edw.search_edw_services(theme="Transportation")
