"""georest.usgs.wqp, offline: what is sent, and how the CSV is read."""

import unittest
import urllib.parse
from unittest import mock

from georest.usgs import wqp

STATIONS = (
    "Location_Identifier,Location_Name,Location_HUCEightDigitCode,"
    "Location_LatitudeStandardized,Location_LongitudeStandardized\n"
    "USGS-06732500,FALL RIVER,01190006,40.376,-105.526\n"
)
RESULTS = (
    "Location_Identifier,Activity_StartDate,Result_Characteristic,Result_Measure,"
    "Result_MeasureUnit,Result_ResultDetectionCondition,"
    "Location_LatitudeStandardized,Location_LongitudeStandardized\n"
    "USGS-09380000,2025-01-07,Temperature,10.4,deg C,,36.86,-111.59\n"
    "USGS-09380000,2025-02-05,Nitrate,,mg/L,Not Detected,36.86,-111.59\n"
)


class WqpTest(unittest.TestCase):
    def serve(self, body):
        calls = []

        def fake(url, params=None, **kwargs):
            calls.append(url)
            return body

        patcher = mock.patch.object(wqp, "fetch_text", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def sent(self, url):
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    def test_several_values_go_as_repeated_parameters_not_semicolons(self):
        calls = self.serve(STATIONS)
        wqp.find_sites(sites=["USGS-1", "USGS-2"], characteristic=["Nitrate", "pH"])
        sent = self.sent(calls[0])
        self.assertEqual(sent["siteid"], ["USGS-1", "USGS-2"])
        self.assertEqual(sent["characteristicName"], ["Nitrate", "pH"])

    def test_state_county_and_dates_are_translated(self):
        calls = self.serve(RESULTS)
        wqp.get_results(state="co", county="69", start="2025-01-01", end="2025-12-31")
        sent = self.sent(calls[0])
        self.assertEqual(sent["countycode"], ["US:08:069"])
        self.assertEqual((sent["startDateLo"], sent["startDateHi"]), (["01-01-2025"], ["12-31-2025"]))
        self.assertEqual(sent["dataProfile"], ["basicPhysChem"])

    def test_a_state_alone_and_a_bbox(self):
        calls = self.serve(STATIONS)
        wqp.find_sites(state="08", bbox=(-106, 40, -105, 41))
        sent = self.sent(calls[0])
        self.assertEqual((sent["statecode"], sent["bBox"]), (["US:08"], ["-106.0,40.0,-105.0,41.0"]))

    def test_bad_searches_fail_before_any_request(self):
        calls = self.serve(STATIONS)
        bad = ({}, {"characteristic": "pH"}, {"county": "069"}, {"state": "Colorado"},
               {"state": "CO", "county": "Larimer"}, {"sites": "USGS-1", "start": "1/1/2025"},
               {"bbox": (-105, 41, -106, 40)})
        for kwargs in bad:
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}), self.assertRaises(ValueError):
                wqp.get_results(**kwargs)
        self.assertEqual(calls, [])

    def test_identifiers_keep_their_leading_zeros(self):
        self.serve(STATIONS)
        df = wqp.find_sites(state="CO")
        self.assertEqual(df["Location_HUCEightDigitCode"].iloc[0], "01190006")
        self.assertEqual((df["lon"].iloc[0], df["lat"].iloc[0]), (-105.526, 40.376))

    def test_results_get_date_value_and_unit_with_non_detects_left_empty(self):
        self.serve(RESULTS)
        df = wqp.get_results(sites="USGS-09380000")
        self.assertEqual(list(df["date"]), ["2025-01-07", "2025-02-05"])
        self.assertEqual(df["value"].iloc[0], 10.4)
        self.assertTrue(df["value"].isna().iloc[1])
        self.assertEqual(df["Result_ResultDetectionCondition"].iloc[1], "Not Detected")
        self.assertEqual(list(df["unit"]), ["deg C", "mg/L"])

    def test_no_results_still_has_the_convenience_columns(self):
        self.serve("")
        df = wqp.get_results(sites="USGS-0")
        self.assertTrue(df.empty)
        for column in ("lon", "lat", "date", "value", "unit"):
            self.assertIn(column, df.columns)


if __name__ == "__main__":
    unittest.main()
