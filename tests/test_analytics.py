"""outAnalytics payload construction for the queryAnalytic operation."""
import json
import unittest

from geoREST.RESTesri import edw


def build(*analytics):
    return json.loads(edw._build_out_analytics(list(analytics)))


class EsriKeyNames(unittest.TestCase):
    """queryAnalytic wants onAnalyticField / outAnalyticFieldName.

    The similar onStatisticField / outStatisticFieldName belong to the
    separate outStatistics and queryTopFeatures operations. Sending those
    made the server reject every analytic taking an input field, and
    silently ignore the requested output name for those that don't.
    """

    def test_aggregate_uses_analytic_key_names(self):
        entry = build({"type": "SUM", "field": "ACRES", "out_name": "total"})[0]
        self.assertEqual(entry["outAnalyticFieldName"], "total")
        self.assertEqual(entry["onAnalyticField"], "ACRES")

    def test_statistic_key_names_are_never_emitted(self):
        entry = build({"type": "SUM", "field": "ACRES", "out_name": "total"})[0]
        self.assertNotIn("outStatisticFieldName", entry)
        self.assertNotIn("onStatisticField", entry)


class FieldlessAnalytics(unittest.TestCase):
    """Ranking functions compute over the window, not over an input field."""

    def test_rank_drops_a_supplied_input_field(self):
        entry = build({"type": "RANK", "field": "ACRES",
                       "order_by": "ACRES DESC", "out_name": "rank_val"})[0]
        self.assertNotIn("onAnalyticField", entry)
        self.assertEqual(entry["outAnalyticFieldName"], "rank_val")
        self.assertEqual(entry["analyticParameters"]["orderBy"], "ACRES DESC")

    def test_every_fieldless_type_drops_its_field(self):
        for analytic_type in edw._FIELDLESS_ANALYTICS:
            with self.subTest(analytic_type):
                entry = build({"type": analytic_type, "field": "X",
                               "out_name": "out"})[0]
                self.assertNotIn("onAnalyticField", entry)

    def test_type_matching_is_case_insensitive(self):
        entry = build({"type": "rank", "field": "X", "out_name": "r"})[0]
        self.assertNotIn("onAnalyticField", entry)

    def test_aggregates_keep_their_field(self):
        for analytic_type in ("SUM", "AVG", "MIN", "MAX", "LAG"):
            with self.subTest(analytic_type):
                entry = build({"type": analytic_type, "field": "ACRES",
                               "out_name": "o"})[0]
                self.assertEqual(entry["onAnalyticField"], "ACRES")


class AnalyticParameters(unittest.TestCase):
    def test_params_are_merged_into_analytic_parameters(self):
        entry = build({"type": "LAG", "field": "ACRES", "out_name": "prev",
                       "params": {"offset": 1}})[0]
        self.assertEqual(entry["analyticParameters"]["offset"], 1)

    def test_order_by_joins_supplied_params(self):
        entry = build({"type": "NTILE", "out_name": "q",
                       "params": {"buckets": 4}, "order_by": "ACRES DESC"})[0]
        self.assertEqual(entry["analyticParameters"],
                         {"buckets": 4, "orderBy": "ACRES DESC"})

    def test_omitted_when_there_is_nothing_to_say(self):
        entry = build({"type": "SUM", "field": "A", "out_name": "o"})[0]
        self.assertNotIn("analyticParameters", entry)

    def test_multiple_analytics_are_preserved_in_order(self):
        entries = build({"type": "SUM", "field": "A", "out_name": "s"},
                        {"type": "AVG", "field": "B", "out_name": "a"})
        self.assertEqual([e["outAnalyticFieldName"] for e in entries], ["s", "a"])


class PartitionBy(unittest.TestCase):
    """partitionBy has no top-level request parameter.

    It must be nested inside each analytic's analyticParameters, or the
    server computes over the whole result set instead of per partition.
    """

    def setUp(self):
        self.sent = {}

        def capture(url, params, *args, **kwargs):
            self.sent.update(params)
            return {"features": []}

        self._saved = edw.post_json
        edw.post_json = capture

    def tearDown(self):
        edw.post_json = self._saved

    def _analytics(self, **kwargs):
        edw.query_features_analytic(
            "SVC", 0,
            out_analytics=[{"type": "RANK", "order_by": "A DESC",
                            "out_name": "r"}],
            **kwargs)
        return json.loads(self.sent["outAnalytics"])[0]

    def test_string_partition_is_nested(self):
        entry = self._analytics(partition_by="DISTRICT")
        self.assertEqual(entry["analyticParameters"]["partitionBy"], "DISTRICT")

    def test_list_partition_is_comma_joined(self):
        entry = self._analytics(partition_by=["FOREST", "YEAR"])
        self.assertEqual(entry["analyticParameters"]["partitionBy"], "FOREST,YEAR")

    def test_partition_does_not_clobber_order_by(self):
        entry = self._analytics(partition_by="DISTRICT")
        self.assertEqual(entry["analyticParameters"]["orderBy"], "A DESC")

    def test_no_top_level_partition_parameter_is_sent(self):
        self._analytics(partition_by="DISTRICT")
        self.assertNotIn("partitionBy", self.sent)

    def test_geojson_format_is_not_requested(self):
        # queryAnalytic returns 400 for f=geojson.
        self._analytics(partition_by="DISTRICT")
        self.assertEqual(self.sent["f"], "json")


class TopNPerGroup(unittest.TestCase):
    def setUp(self):
        self.sent = {}

        def capture(url, params, *args, **kwargs):
            self.sent.update(params)
            return {"features": []}

        self._saved = edw.post_json
        edw.post_json = capture

    def tearDown(self):
        edw.post_json = self._saved

    def test_filters_on_the_requested_output_name(self):
        # Not the "rank_expr0" the server invents when the key names are wrong.
        edw.top_n_per_group("SVC", 0, field="ACRES", group_by="D", n=3)
        self.assertEqual(self.sent["analyticWhere"], "rank_val <= 3")

    def test_rank_field_travels_via_order_by(self):
        edw.top_n_per_group("SVC", 0, field="ACRES", group_by="D")
        entry = json.loads(self.sent["outAnalytics"])[0]
        self.assertEqual(entry["analyticParameters"]["orderBy"], "ACRES DESC")

    def test_ascending_ranks_smallest_first(self):
        edw.top_n_per_group("SVC", 0, field="ACRES", group_by="D",
                            descending=False)
        entry = json.loads(self.sent["outAnalytics"])[0]
        self.assertEqual(entry["analyticParameters"]["orderBy"], "ACRES ASC")

    def test_n_is_coerced_to_an_int(self):
        edw.top_n_per_group("SVC", 0, field="A", group_by="D", n="5")
        self.assertEqual(self.sent["analyticWhere"], "rank_val <= 5")


if __name__ == "__main__":
    unittest.main()
