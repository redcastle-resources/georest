"""Live checks against the real EDW server. Enable with EDW_LIVE=1.

The headline check here is catalog drift: _SERVICE_THEMES is a hand-curated
table of 143 services, and EDW publishes and retires services without notice.
When it drifts, search_edw_services quietly degrades — an untyped service
still appears, but as theme "uncategorized" with no description for keyword
matching, so theme filters and alias searches stop finding it.
"""
import unittest

from georest.restesri import edw

from .support import requires_live, retry, theme_drift


@requires_live
class CatalogThemeDrift(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.services = retry(lambda: edw.search_edw_services())
        cls.drift = theme_drift(cls.services)

    def test_catalog_is_a_plausible_size(self):
        self.assertGreater(len(self.services), 100)

    def test_no_mapserver_is_missing_a_theme_entry(self):
        self.assertEqual(
            self.drift["untyped"], [],
            "\nThese live MapServer services have no _SERVICE_THEMES entry, "
            "so they search as 'uncategorized' with no description:\n  "
            + "\n  ".join(self.drift["untyped"])
            + "\n\nRun `georest-check-themes --emit` for "
              "ready-to-paste entries.",
        )

    def test_non_mapserver_services_are_the_known_set(self):
        """A new service type is worth noticing: the client assumes MapServer.

        get_service_info, get_layer_info and query_features all build
        /MapServer URLs, so anything else needs handling before it can be
        used, even though search_edw_services will happily list it.
        """
        self.assertEqual(self.drift["non_mapserver"],
                         ["RAVG_DataExtract_01 (GPServer)"])

    def test_no_theme_entry_points_at_a_retired_service(self):
        self.assertEqual(
            self.drift["orphaned"], [],
            "\nThese _SERVICE_THEMES entries name services no longer in the "
            "catalog:\n  " + "\n  ".join(self.drift["orphaned"]),
        )

    def test_no_malformed_entries(self):
        self.assertEqual(self.drift["bad_theme"], [])
        self.assertEqual(self.drift["missing_desc"], [])

    def test_search_returns_the_whole_catalog_when_unfiltered(self):
        again = retry(lambda: edw.search_edw_services())
        self.assertEqual(len(again), len(self.services))


@requires_live
class LiveSmoke(unittest.TestCase):
    """A thin end-to-end pass over each public entry point."""

    SERVICE = "EDW_MTBS_01"
    ALL_YEARS_POLYGONS = 63   # "Burned Area Boundaries (All Years)"
    ALL_YEARS_POINTS = 62     # "Fire Occurrence Locations (All Years)"

    def test_service_info(self):
        info = retry(lambda: edw.get_service_info(self.SERVICE))
        self.assertTrue(info["layers"])
        self.assertNotIn("geometryType", info["layers"][0],
                         "documented as absent; use get_layer_info instead")

    def test_layer_roles_cover_every_layer(self):
        info = retry(lambda: edw.get_service_info(self.SERVICE))
        roles = retry(lambda: edw.get_layer_roles(self.SERVICE))
        self.assertEqual(len(roles), len(info["layers"]))
        for lyr in roles:
            self.assertIn(lyr["role"], edw.LAYER_ROLES)

    def test_layer_info_field_names_are_lowercase(self):
        """The uppercase forms are aliases; passing them to outFields 400s."""
        info = retry(lambda: edw.get_layer_info(self.SERVICE,
                                                self.ALL_YEARS_POLYGONS))
        names = {f["name"] for f in info["fields"]}
        self.assertIn("fire_name", names)
        self.assertNotIn("FIRE_NAME", names)

    def test_layer_metadata(self):
        meta = retry(lambda: edw.get_layer_metadata(self.SERVICE,
                                                    self.ALL_YEARS_POLYGONS))
        self.assertTrue(meta["abstract"])
        self.assertTrue(meta["attributes"])

    def test_attribute_query_returns_expected_feature(self):
        got = retry(lambda: edw.query_features(
            self.SERVICE, self.ALL_YEARS_POLYGONS,
            where="fire_name LIKE '%CAMERON PEAK%'",
            out_fields="objectid,fire_name,acres,year"))
        self.assertEqual(len(got["features"]), 1)
        props = got["features"][0]["properties"]
        self.assertEqual(props["fire_name"], "CAMERON PEAK")
        self.assertEqual(props["year"], 2020)

    def test_feature_ids_track_the_real_object_id(self):
        got = retry(lambda: edw.query_features(
            self.SERVICE, self.ALL_YEARS_POLYGONS,
            where="fire_name LIKE 'CAMERON%'",
            out_fields="objectid,fire_name", max_features=5))
        for feature in got["features"]:
            self.assertEqual(feature["id"],
                             str(feature["properties"]["objectid"]))

    def test_multipart_geometry_is_split_not_holed(self):
        """A fire with disjoint parts must not become one polygon of holes."""
        got = retry(lambda: edw.query_features(
            self.SERVICE, self.ALL_YEARS_POLYGONS,
            where="fire_name LIKE '%CAMERON PEAK%'", out_fields="objectid"))
        geometry = got["features"][0]["geometry"]
        self.assertIn(geometry["type"], ("Polygon", "MultiPolygon"))
        if geometry["type"] == "MultiPolygon":
            solid = sum(1 for part in geometry["coordinates"] if len(part) == 1)
            self.assertGreater(solid, 0)

    def test_count_only(self):
        got = retry(lambda: edw.query_features(
            self.SERVICE, self.ALL_YEARS_POLYGONS, where="year = 2020",
            return_count_only=True))
        self.assertGreater(got["count"], 0)

    def test_out_sr_does_not_reinterpret_the_input_geometry(self):
        bbox = "-106.0,40.4,-105.2,40.9"   # WGS84 lon/lat
        counts = [
            retry(lambda sr=sr: edw.query_features(
                self.SERVICE, self.ALL_YEARS_POLYGONS, geometry=bbox,
                out_sr=sr, return_count_only=True))["count"]
            for sr in (4326, 3857)
        ]
        self.assertEqual(counts[0], counts[1])

    def test_aggregate_analytics_execute(self):
        """SUM/AVG fail outright if the outAnalytics key names are wrong."""
        got = retry(lambda: edw.query_features_analytic(
            self.SERVICE, self.ALL_YEARS_POLYGONS,
            out_analytics=[{"type": "SUM", "field": "acres",
                            "out_name": "total_acres"}],
            partition_by="year", where="year IN (2019, 2020)",
            out_fields="year", return_geometry=False))
        self.assertTrue(got["features"])
        self.assertIn("total_acres", got["features"][0]["properties"])

    def test_top_n_per_group_uses_the_requested_rank_field(self):
        got = retry(lambda: edw.top_n_per_group(
            self.SERVICE, self.ALL_YEARS_POLYGONS, field="acres",
            group_by="year", n=1,
            where="year IN (2019, 2020) AND acres IS NOT NULL",
            out_fields="objectid,fire_name,year,acres"))
        self.assertTrue(got["features"])
        for feature in got["features"]:
            self.assertEqual(feature["properties"]["rank_val"], 1)

    def test_pagination_crosses_the_server_page_boundary(self):
        page = edw._get_max_record_count(self.SERVICE, self.ALL_YEARS_POINTS)
        got = retry(lambda: edw.query_features_with_pagination(
            self.SERVICE, self.ALL_YEARS_POINTS, where="1=1",
            out_fields="objectid,fire_name", max_features=page * 2 + 100))
        features = got["features"]
        self.assertEqual(len(features), page * 2 + 100)
        ids = [f["properties"]["objectid"] for f in features]
        self.assertEqual(len(set(ids)), len(ids), "duplicates across pages")


if __name__ == "__main__":
    unittest.main()
