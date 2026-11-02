"""georest.to_frame: any georest result as a DataFrame."""

import unittest

import georest
from georest import frames

POLYGONS = {"type": "FeatureCollection", "exceededTransferLimit": True, "features": [
    {"type": "Feature", "id": 7, "properties": {"fire_name": "A", "acres": 10.0},
     "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}},
    {"type": "Feature", "id": 8, "properties": {"fire_name": "B", "acres": 20.0}, "geometry": None},
]}
POINTS = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"name": "gage"},
     "geometry": {"type": "Point", "coordinates": [-111.5, 36.8]}},
]}


class ToFrameTest(unittest.TestCase):
    def test_a_feature_collection_becomes_its_attribute_table(self):
        df = georest.to_frame(POLYGONS)
        self.assertEqual(list(df["fire_name"]), ["A", "B"])
        self.assertEqual(list(df["id"]), [7, 8])
        self.assertNotIn("geometry", df.columns)
        self.assertNotIn("lon", df.columns, "polygons have no single point to give")

    def test_members_beside_the_features_go_to_attrs(self):
        self.assertTrue(georest.to_frame(POLYGONS).attrs["exceededTransferLimit"])

    def test_points_get_lon_and_lat(self):
        df = georest.to_frame(POINTS)
        self.assertEqual((df["lon"].iloc[0], df["lat"].iloc[0]), (-111.5, 36.8))

    def test_geometry_can_be_kept(self):
        df = georest.to_frame(POLYGONS, geometry=True)
        self.assertEqual(df["geometry"].iloc[0]["type"], "Polygon")
        self.assertIsNone(df["geometry"].iloc[1])

    def test_plain_records_and_a_list_of_features(self):
        self.assertEqual(list(georest.to_frame([{"name": "x", "n": 1}]).columns), ["name", "n"])
        df = georest.to_frame(POLYGONS["features"])
        self.assertEqual(list(df["acres"]), [10.0, 20.0])

    def test_a_single_feature_and_nothing(self):
        self.assertEqual(len(georest.to_frame(POINTS["features"][0])), 1)
        self.assertTrue(georest.to_frame({"type": "FeatureCollection", "features": []}).empty)
        self.assertTrue(georest.to_frame([]).empty)

    def test_the_top_level_name_is_the_frames_function(self):
        self.assertIn("to_frame", georest.__all__)
        self.assertEqual(georest.to_frame(POINTS).to_dict(), frames.to_frame(POINTS).to_dict())


if __name__ == "__main__":
    unittest.main()
