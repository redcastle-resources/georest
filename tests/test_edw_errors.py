"""EDW errors carry what ArcGIS said, even when its message is empty."""
import pytest

from georest.esri import edw


def test_an_empty_error_message_reports_the_details(monkeypatch):
    # ArcGIS often answers with an empty message and the cause in details.
    monkeypatch.setattr(edw, "post_json", lambda url, params: {
        "error": {"code": 400, "message": "", "details": ["Invalid geometry."]}})
    with pytest.raises(RuntimeError, match=r"EDW query error: 400 \(Invalid geometry\.\)$"):
        edw.query_features("X", 0, where="1=1")
