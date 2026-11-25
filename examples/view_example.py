"""georest.view: Map.addLayer takes anything, and works out what it was given.

   - an ArcGIS feature layer, map service or image service URL, with its
     options in viz (where, layers, time, reducer, ...)
   - an XYZ tile URL; Query shows the color under the click, and the
     classLegendDict class it matches
   - GeoJSON as a URL, as text, or as a dict

Map.addTimeLapse plays a time-enabled ArcGIS service, one frame per time step.
No Earth Engine: geeViz's Map takes Earth Engine objects and asset ids too.
"""
import json

from georest.view import Map

# ArcGIS: the URL says which kind of service it is.
# A feature layer (MTBS fire perimeters), filtered in viz
Map.addLayer("https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MTBS_01/MapServer/63",
             {"where": "year >= 2015", "chartField": "fire_type", "canAreaChart": True}, "Fires since 2015 (feature layer)")

# A map service, one sublayer (FEMA flood hazard zones; they draw from zoom 14 in)
Map.addLayer("https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer", {"layers": "show:28"},
             "Flood hazard (map service)")

# An image service with a reducer: every Landsat scene from summer 2020,
# combined per pixel by the minimum (clouds, the brightest pixels, drop out).
# The reducer is "first", "last", "min", "max", "mean", "sum" or "blend".
Map.addLayer("https://landsat2.arcgis.com/arcgis/rest/services/Landsat/MS/ImageServer",
             {"time": ("2020-06-01", "2020-09-30"), "reducer": "min",
              "renderingRule": {"rasterFunction": "Natural Color with DRA"}},
             "Landsat summer 2020, min (image service)", visible=False)

# A time-enabled image service as a time lapse: use addTimeLapse, not addLayer
Map.addTimeLapse("https://imagery.geoplatform.gov/iipp/rest/services/Vegetation/USFS_EDW_LCMS_AnnualChange_CONUS/ImageServer",
                 {"canAreaChart": True, "areaChartParams": {"line": True, "sankey": True}},
                 "LCMS annual change (time lapse)", visible=False)

# XYZ tiles: Query reads the color under the click. With classLegendDict, it
# names the class whose color is nearest; without it, the color itself.
Map.addLayer("https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}",
             {"classLegendDict": {"Water body": "cbe6ff", "Stream or river": "78b0f0"}, "maxZoom": 16},
             "USGS hydrography (tiles, with a legend)")

# GeoJSON: a URL (loaded by the viewer), text, or a dict
Map.addLayer("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson",
             {"strokeColor": "d62728", "pointRadius": 6}, "Earthquakes this week (GeoJSON URL)", visible=False)
Map.addLayer({"type": "Feature", "properties": {"name": "Cameron Peak study area"},
              "geometry": {"type": "Polygon", "coordinates": [[[-105.9, 40.5], [-105.5, 40.5], [-105.5, 40.75],
                                                               [-105.9, 40.75], [-105.9, 40.5]]]}},
             {"strokeColor": "ffffff", "strokeWeight": 2}, "Study area (GeoJSON dict)")
Map.addLayer(json.dumps({"type": "Feature", "properties": {"name": "Horsetooth Reservoir"},
                         "geometry": {"type": "Point", "coordinates": [-105.16208, 40.56226]}}),
             {"strokeColor": "1f77b4"}, "A point (GeoJSON text)")

Map.setCenter(-105.4, 40.6, 11)
Map.turnOnInspector()
Map.setTitle("Everything addLayer takes")

if __name__ == "__main__":
    import time
    print(Map.view(), flush=True)
    while True:  # keep the local server (and its /proxy) up while the page is open
        time.sleep(3600)
