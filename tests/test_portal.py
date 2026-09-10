"""Offline tests for `georest/restesri/portal.py`.

Network-free: `fetch_json` is replaced on the `portal` module itself. The
canned responses are trimmed copies of real payloads (an ArcGIS Online
search result, an Esri 498 invalid-token body, a 404 error object served
with status 200).

The emphasis is on how failures reach the caller. Both public functions
used to report a dead host as `ConnectionError` in their docstrings while
never being able to raise one, and both used to hand an Esri error object
back to the caller as if it were data.
"""
from __future__ import annotations

import contextlib
import unittest

from georest.restesri import portal as P

from .support import RecordingFetch

AGOL = "https://www.arcgis.com"
SERVICE = "https://example.com/arcgis/rest/services/Thing/FeatureServer/0"


@contextlib.contextmanager
def patched_portal(**attrs):
    """Swap attributes on the `portal` module, restoring them afterwards."""
    saved = {name: getattr(P, name) for name in attrs}
    try:
        for name, value in attrs.items():
            setattr(P, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(P, name, value)


class ResolvePortalTests(unittest.TestCase):

    def test_short_names_resolve_to_base_urls(self):
        self.assertEqual(P._resolve_portal("agol"), AGOL)

    def test_full_urls_pass_through_without_a_trailing_slash(self):
        self.assertEqual(P._resolve_portal("https://gis.example.gov/portal/"),
                         "https://gis.example.gov/portal")

    def test_an_unknown_short_name_lists_the_known_ones(self):
        with self.assertRaises(KeyError) as ctx:
            P._resolve_portal("nope")
        self.assertIn("iipp", str(ctx.exception))


class SearchPortalTests(unittest.TestCase):

    RESULT = {"results": [{
        "id": "abc123", "title": "NAIP 2023", "type": "Image Service",
        "snippet": "imagery", "tags": ["naip"], "url": "https://x/ImageServer",
        "owner": "someone", "created": 1, "modified": 2, "thumbnail": "thumb.png",
    }]}

    def test_parses_items_into_the_documented_shape(self):
        with patched_portal(fetch_json=RecordingFetch(self.RESULT)):
            got = P.searchPortal("naip", portal="agol")
        self.assertEqual(len(got), 1)
        item = got[0]
        self.assertEqual(item["id"], "abc123")
        self.assertEqual(item["type"], "Image Service")
        self.assertEqual(item["_raw"], self.RESULT["results"][0])

    def test_thumbnail_is_expanded_to_an_absolute_url(self):
        with patched_portal(fetch_json=RecordingFetch(self.RESULT)):
            got = P.searchPortal("naip", portal="agol")
        self.assertEqual(
            got[0]["thumbnail"],
            f"{AGOL}/sharing/rest/content/items/abc123/info/thumb.png",
        )

    def test_a_missing_thumbnail_stays_none(self):
        payload = {"results": [{"id": "x"}]}
        with patched_portal(fetch_json=RecordingFetch(payload)):
            self.assertIsNone(P.searchPortal("x", portal="agol")[0]["thumbnail"])

    def test_data_only_appends_the_exclusion_list(self):
        fake = RecordingFetch(self.RESULT)
        with patched_portal(fetch_json=fake):
            P.searchPortal("naip", portal="agol")
        self.assertIn('-type:"Dashboard"', fake.last_params["q"])

    def test_data_only_false_leaves_the_query_alone(self):
        fake = RecordingFetch(self.RESULT)
        with patched_portal(fetch_json=fake):
            P.searchPortal("naip", portal="agol", data_only=False)
        self.assertEqual(fake.last_params["q"], "naip")

    def test_raw_q_overrides_everything(self):
        fake = RecordingFetch(self.RESULT)
        with patched_portal(fetch_json=fake):
            P.searchPortal("ignored", portal="agol", raw_q='type:"Feature Service"')
        self.assertEqual(fake.last_params["q"], 'type:"Feature Service"')

    def test_limit_is_clamped_to_the_portal_maximum(self):
        fake = RecordingFetch(self.RESULT)
        with patched_portal(fetch_json=fake):
            P.searchPortal("x", portal="agol", limit=500)
            P.searchPortal("x", portal="agol", limit=0)
        self.assertEqual(fake.calls[0][1]["num"], 100)
        self.assertEqual(fake.calls[1][1]["num"], 1)

    def test_extra_filters_are_forwarded_verbatim(self):
        fake = RecordingFetch(self.RESULT)
        with patched_portal(fetch_json=fake):
            P.searchPortal("x", portal="agol", sortField="title")
        self.assertEqual(fake.last_params["sortField"], "title")

    def test_an_empty_result_set_is_an_empty_list(self):
        with patched_portal(fetch_json=RecordingFetch({"results": []})):
            self.assertEqual(P.searchPortal("nothing", portal="agol"), [])

    def test_an_invalid_token_raises_instead_of_looking_like_no_matches(self):
        """Regression: a 498 body used to be swallowed into []."""
        body = {"error": {"code": 498, "message": "Invalid token.", "details": []}}
        with patched_portal(fetch_json=RecordingFetch(body)):
            with self.assertRaises(ValueError) as ctx:
                P.searchPortal("water", portal="agol", token="bogus")
        self.assertIn("498", str(ctx.exception))
        self.assertIn("Invalid token", str(ctx.exception))

    def test_an_unreachable_portal_raises_runtimeerror_naming_the_url(self):
        with patched_portal(fetch_json=RecordingFetch(RuntimeError("Request error: dns"))):
            with self.assertRaises(RuntimeError) as ctx:
                P.searchPortal("x", portal="agol")
        self.assertIn("/sharing/rest/search", str(ctx.exception))


class GetServiceMetadataTests(unittest.TestCase):

    META = {"name": "Earthquakes1970", "fields": [{"name": "magnitude"}]}

    def test_returns_the_parsed_metadata(self):
        with patched_portal(fetch_json=RecordingFetch(self.META)):
            self.assertEqual(P.getServiceMetadata(SERVICE), self.META)

    def test_trailing_slash_is_stripped_and_f_json_is_requested(self):
        fake = RecordingFetch(self.META)
        with patched_portal(fetch_json=fake):
            P.getServiceMetadata(SERVICE + "/")
        self.assertEqual(fake.last_url, SERVICE)
        self.assertEqual(fake.last_params["f"], "json")

    def test_token_is_forwarded(self):
        fake = RecordingFetch(self.META)
        with patched_portal(fetch_json=fake):
            P.getServiceMetadata(SERVICE, token="tok")
        self.assertEqual(fake.last_params["token"], "tok")

    def test_a_missing_service_raises_instead_of_returning_the_error_dict(self):
        """ArcGIS reports this with a 200 and an error object, not a 404."""
        body = {"error": {"code": 404, "message": "Service not found", "details": []}}
        with patched_portal(fetch_json=RecordingFetch(body)):
            with self.assertRaises(ValueError) as ctx:
                P.getServiceMetadata(SERVICE)
        self.assertIn("404", str(ctx.exception))

    def test_an_unreachable_host_raises_runtimeerror_naming_the_url(self):
        with patched_portal(fetch_json=RecordingFetch(RuntimeError("Request error: dns"))):
            with self.assertRaises(RuntimeError) as ctx:
                P.getServiceMetadata(SERVICE)
        self.assertIn(SERVICE, str(ctx.exception))


class DetectServiceTypeTests(unittest.TestCase):

    def test_url_segments_are_matched_without_a_request(self):
        fake = RecordingFetch({})
        with patched_portal(fetch_json=fake):
            self.assertEqual(P._detect_service_type("https://x/y/ImageServer"), "ImageServer")
            self.assertEqual(P._detect_service_type("https://x/y/FeatureServer/0"), "FeatureServer")
            self.assertEqual(P._detect_service_type("https://x/y/MapServer"), "MapServer")
        self.assertEqual(fake.calls, [], "URL detection must not hit the network")

    def test_supplied_metadata_is_used_when_the_url_is_ambiguous(self):
        self.assertEqual(
            P._detect_service_type("https://x/y/thing", meta={"serviceDataType": "esriImageServiceDataTypeThematic"}),
            "ImageServer",
        )

    def test_fields_imply_a_feature_service(self):
        self.assertEqual(P._detect_service_type("https://x/y/thing", meta={"fields": []}), "FeatureServer")

    def test_band_count_implies_an_image_service(self):
        self.assertEqual(P._detect_service_type("https://x/y/thing", meta={"bandCount": 3}), "ImageServer")

    def test_a_failed_metadata_fetch_degrades_to_unknown(self):
        """getServiceMetadata now raises on an error body; this must absorb it."""
        body = {"error": {"code": 404, "message": "gone", "details": []}}
        with patched_portal(fetch_json=RecordingFetch(body)):
            self.assertEqual(P._detect_service_type("https://x/y/thing"), "Unknown")

    def test_an_unreachable_host_degrades_to_unknown(self):
        with patched_portal(fetch_json=RecordingFetch(RuntimeError("dns"))):
            self.assertEqual(P._detect_service_type("https://x/y/thing"), "Unknown")


class ResolveUrlTests(unittest.TestCase):

    def test_a_string_is_stripped_of_its_trailing_slash(self):
        self.assertEqual(P._resolve_url("https://x/y/ImageServer/"), "https://x/y/ImageServer")

    def test_a_search_result_dict_yields_its_url(self):
        self.assertEqual(P._resolve_url({"url": "https://x/y/ImageServer"}), "https://x/y/ImageServer")

    def test_a_dict_without_a_url_points_at_the_raw_record(self):
        with self.assertRaises(ValueError) as ctx:
            P._resolve_url({"title": "no url"})
        self.assertIn("_raw", str(ctx.exception))

    def test_any_other_type_is_a_typeerror(self):
        with self.assertRaises(TypeError):
            P._resolve_url(123)


class DocstringContractTests(unittest.TestCase):

    PUBLIC = [P.searchPortal, P.getServiceMetadata]

    def test_no_docstring_promises_connectionerror(self):
        """It was documented but unreachable: _http raises RuntimeError."""
        offenders = [fn.__name__ for fn in self.PUBLIC if "ConnectionError" in (fn.__doc__ or "")]
        self.assertEqual(offenders, [])

    def test_both_document_the_real_transport_failure_mode(self):
        for fn in self.PUBLIC:
            with self.subTest(fn=fn.__name__):
                self.assertIn("RuntimeError", fn.__doc__)


if __name__ == "__main__":
    unittest.main()
