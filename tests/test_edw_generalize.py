"""edw.query_features(max_allowable_offset=...): simplified geometry for maps.

The complex-AOI fallback re-fetches features by object id. A parameter
honored on the first request but dropped on the fallback would hand a map
full-detail geometry exactly when the area is most complex, which is when
it matters most.
"""
import unittest

from georest.esri import edw

from .support import patched


class Generalize(unittest.TestCase):
    def setUp(self):
        self.sent = []

    def _server(self, fallback):
        def post_json(url, params, *args, **kwargs):
            self.sent.append(dict(params))
            if params.get("returnIdsOnly") == "true":
                return {"objectIdFieldName": "objectid", "objectIds": [1, 2]}
            if fallback and " IN (" not in params.get("where", ""):
                # The upstream bug: a complex AOI returns no features at all.
                return {"type": "FeatureCollection", "features": [], "exceededTransferLimit": True}
            return {"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"objectid": 1}, "geometry": None},
                {"type": "Feature", "properties": {"objectid": 2}, "geometry": None}]}
        return patched(post_json=post_json, get_layer_info=lambda s, l: {"maxRecordCount": 1000})

    def test_sent_when_asked(self):
        with self._server(fallback=False):
            edw.query_features("X", 0, max_allowable_offset=0.001)
        self.assertEqual(self.sent[0]["maxAllowableOffset"], "0.001")

    def test_not_sent_by_default(self):
        with self._server(fallback=False):
            edw.query_features("X", 0)
        self.assertNotIn("maxAllowableOffset", self.sent[0])

    def test_the_id_fallback_keeps_it(self):
        with self._server(fallback=True):
            fc = edw.query_features("X", 0, geometry="-106,40,-105,41", max_allowable_offset=0.002)
        self.assertEqual(len(fc["features"]), 2)
        by_id = [p for p in self.sent if " IN (" in p.get("where", "")]
        self.assertTrue(by_id, "the fallback never ran")
        self.assertEqual(by_id[0]["maxAllowableOffset"], "0.002")


if __name__ == "__main__":
    unittest.main()
