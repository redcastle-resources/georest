"""
USFS Enterprise Data Warehouse (EDW) REST API client.

Provides search, metadata inspection, and spatial feature queries against
the ArcGIS REST services at https://apps.fs.usda.gov/arcx/rest/services/EDW.

Quick start::

    import edw

    # Search for fire-related services
    services = edw.search_services("fire")

    # Get layer info
    info = edw.get_service_info("EDW_MTBS_01")

    # Query features as GeoJSON
    geojson = edw.query_features("EDW_MTBS_01", 15,
        where="FIRE_NAME LIKE '%CAMERON PEAK%'",
        out_fields="FIRE_NAME,ACRES,YEAR")
"""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from typing import Any

from ._http import fetch_json, fetch_text, post_json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EDW_BASE_URL = "https://apps.fs.usda.gov/arcx/rest/services/EDW"
_MAX_RECORD_COUNT = 2000  # ArcGIS server default max

# ---------------------------------------------------------------------------
# Theme catalog — maps EDW service names to thematic categories + descriptions.
# Sourced from https://data.fs.usda.gov/geodata/edw/datasets.php
# This enables search by theme keyword (e.g. "riparian" → inland waters services)
# ---------------------------------------------------------------------------

_SERVICE_THEMES: dict[str, dict[str, str]] = {
    # --- Biota ---
    "EDW_CicadaBroods_01": {"theme": "biota", "desc": "Active Periodical Cicada Broods of the United States"},
    "EDW_InvasiveSpecies_01": {"theme": "biota", "desc": "Current Invasive Plants"},
    "EDW_ExistingVegetation_01": {"theme": "biota", "desc": "Existing Vegetation (Region 5 CALVEG)"},
    "EDW_AquaticOrganismPassage_01": {"theme": "biota", "desc": "Aquatic Organism Passage: activities, habitat miles, surveys"},
    # --- Boundaries ---
    "EDW_ForestSystemBoundaries_01": {"theme": "boundaries", "desc": "Administrative Forest Boundaries"},
    "EDW_BipartisanInfrastructureLaw_01": {"theme": "boundaries", "desc": "Bipartisan Infrastructure Law Landscape Investments"},
    "EDW_ExperimentalForestandRange_01": {"theme": "boundaries", "desc": "Experimental Forest and Range Areas and Locations"},
    "EDW_ForestCommonNames_01": {"theme": "boundaries", "desc": "Forest Common Names"},
    "EDW_LandAndWaterConservationFundParcels_01": {"theme": "boundaries", "desc": "Forest Service LWCF Parcels"},
    "EDW_RegionalBoundaries_01": {"theme": "boundaries", "desc": "Forest Service Regional Boundaries"},
    "EDW_BasicOwnership_01": {"theme": "boundaries", "desc": "Surface Ownership Parcels (basic)"},
    "EDW_SurfaceOwnership_01": {"theme": "boundaries", "desc": "Surface Ownership Parcels (detailed)"},
    "EDW_NationalForestLands_01": {"theme": "boundaries", "desc": "National Forest System Land Units"},
    "EDW_NationalGrasslandUnits_01": {"theme": "boundaries", "desc": "National Grassland Units"},
    "EDW_Wilderness_01": {"theme": "boundaries", "desc": "National Wilderness Areas"},
    "EDW_WildernessFSOnly_01": {"theme": "boundaries", "desc": "Wilderness Areas: Legal Status (FS only)"},
    "EDW_WildScenicRiver_01": {"theme": "boundaries", "desc": "National Wild and Scenic Rivers: lines, segments, legal status"},
    "EDW_ProclaimedForestBoundaries_01": {"theme": "boundaries", "desc": "Original Proclaimed National Forest Boundaries"},
    "EDW_ProclaimedForestsAndGrasslands_01": {"theme": "boundaries", "desc": "Proclaimed National Forests and National Grasslands"},
    "EDW_RangerDistricts_01": {"theme": "boundaries", "desc": "Ranger District Boundaries"},
    "EDW_ResearchStations_01": {"theme": "boundaries", "desc": "Research Station Boundaries"},
    "EDW_RightofWay_01": {"theme": "boundaries", "desc": "Right of Way"},
    "EDW_SpecialInterestMgmtAreas_01": {"theme": "boundaries", "desc": "Special Interest Management Areas"},
    "EDW_SpecialStatusAreas_01": {"theme": "boundaries", "desc": "Special Status Areas"},
    "EDW_TribalLands_01": {"theme": "boundaries", "desc": "Tribal Ceded Lands"},
    "EDW_PADUS_01": {"theme": "boundaries", "desc": "PADUS FS Managed Surface Ownership, Designated Areas, Easements"},
    "EDW_ALPStatusAndEncumbrance_01": {"theme": "boundaries", "desc": "Land status, encumbrance, mineral rights, survey boundaries"},
    # --- Environment ---
    "EDW_ActivityRangeVegetationImprovement_01": {"theme": "environment", "desc": "Range Vegetation Improvement Activities"},
    "EDW_ActivityTimberHarvests_01": {"theme": "environment", "desc": "Timber Harvests"},
    "EDW_ActivityHazFuelTrt_01": {"theme": "environment", "desc": "Hazardous Fuel Treatment Reduction"},
    "EDW_ActivitySilvicultureTimberStandImprovement_01": {"theme": "environment", "desc": "Silviculture Timber Stand Improvement"},
    "EDW_SilvicultureReforestation_01": {"theme": "environment", "desc": "Silviculture Reforestation"},
    "EDW_SilvicultureReforestationNeeds_01": {"theme": "environment", "desc": "Silviculture Reforestation Needs"},
    "EDW_CFLRP_01": {"theme": "environment", "desc": "Collaborative Forest Landscape Restoration Program"},
    "EDW_StewardshipContracting_01": {"theme": "environment", "desc": "Stewardship Contracting"},
    "EDW_WesternBarkBeetleStrategy_01": {"theme": "environment", "desc": "Western Bark Beetle Strategy"},
    "EDW_HealthyForestRestorationAct_01": {"theme": "environment", "desc": "Healthy Forest Restoration Act Activities"},
    "EDW_RecreationSites_01": {"theme": "environment", "desc": "Recreation Sites Public Information"},
    "EDW_AerialFireRetardantAvoidanceAreas_Terrestrial_01": {"theme": "environment", "desc": "Aerial Fire Retardant Avoidance Areas: Terrestrial"},
    "EDW_ClimateShield_01": {"theme": "environment", "desc": "Climate Shield: Bull Trout and Cutthroat Trout habitat (1980/2040/2080)"},
    "EDW_TEUInventoryStatus_01": {"theme": "environment", "desc": "Ecosystem Terrestrial Ecological Unit Inventory Status"},
    "EDW_ActivityFactsCommonAttributes_01": {"theme": "environment", "desc": "FACTS Common Attributes (all regions)"},
    "EDW_FireOccurrence6thEdition_01": {"theme": "environment", "desc": "FIRESTAT Fire Occurrence (yearly update)"},
    "EDW_FireOccurrenceAndPerimeter_01": {"theme": "environment", "desc": "National USFS Fire Occurrence and Perimeter"},
    "EDW_CommWildfireDefenseGrant_01": {"theme": "environment", "desc": "Community Wildfire Defense Grant"},
    "EDW_Fireshed_01": {"theme": "environment", "desc": "Fireshed Registry: Fireshed and Project Areas"},
    "EDW_HazardousSites_01": {"theme": "environment", "desc": "Hazardous Sites"},
    "EDW_NorWeST_StreamTemperatures_01": {"theme": "environment", "desc": "NorWeST Stream Temperatures: observed points and predicted lines"},
    "EDW_RAVG_01": {"theme": "environment", "desc": "RAVG Postfire Vegetation Change Perimeters"},
    "EDW_MTBS_01": {"theme": "environment", "desc": "Monitoring Trends in Burn Severity: fire occurrence and burned area boundaries 1984-present"},
    "EDW_BurnedAreaEmergencyResponse_01": {"theme": "environment", "desc": "Burned Area Emergency Response (BAER)"},
    "EDW_HistoricalWoodlandDensity_01": {"theme": "environment", "desc": "Historical Woodland Density of the Conterminous U.S., 1873"},
    "EDW_BaileysEcoregions_01": {"theme": "environment", "desc": "Bailey's Ecoregions: provinces, sections, subsections"},
    "EDW_Ecomap2025_01": {"theme": "environment", "desc": "Ecomap 2025: domains, divisions, provinces, sections, subsections"},
    # --- Geoscientific Information ---
    "EDW_EcologicalProvinces_01": {"theme": "geoscientific", "desc": "Ecological Provinces"},
    "EDW_EcologicalSections_01": {"theme": "geoscientific", "desc": "Ecological Sections"},
    "EDW_EcologicalSubsections_01": {"theme": "geoscientific", "desc": "Ecological Subsections"},
    "EDW_TongassLandslide_01": {"theme": "geoscientific", "desc": "Tongass Landslide Areas and Initiation"},
    # --- Inland Waters ---
    "EDW_AerialFireRetardantAvoidanceAreas_Aquatic_01": {"theme": "inland_waters", "desc": "Aerial Fire Retardant Avoidance Areas: Aquatic"},
    "EDW_HydroPercentStreamFlowNFSAnnual_01": {"theme": "inland_waters", "desc": "Fraction of Runoff from Forest Service Lands (Annual)"},
    "EDW_HydroPercentStreamFlowNFSSummer_01": {"theme": "inland_waters", "desc": "Fraction of Runoff from Forest Service Lands (Summer)"},
    "EDW_GreatBasinMountainRangesWatersheds_01": {"theme": "inland_waters", "desc": "Great Basin Montane Watersheds: streams, valley bottoms, pour points"},
    "EDW_HydroFlowMetricsHistorical_01": {"theme": "inland_waters", "desc": "Hydro Flow Metrics: Historical"},
    "EDW_HydroFlowMetrics2040_01": {"theme": "inland_waters", "desc": "Hydro Flow Metrics: Mid-Century (2040)"},
    "EDW_HydroFlowMetrics2080_01": {"theme": "inland_waters", "desc": "Hydro Flow Metrics: End-of-Century (2080)"},
    "EDW_HydroFlowMetricsAbsChange2040_01": {"theme": "inland_waters", "desc": "Hydro Flow Metrics: Absolute Change by Mid-Century"},
    "EDW_HydroFlowMetricsAbsChange2080_01": {"theme": "inland_waters", "desc": "Hydro Flow Metrics: Absolute Change by End-of-Century"},
    "EDW_HydroFlowMetricsPercentChange2040_01": {"theme": "inland_waters", "desc": "Hydro Flow Metrics: Percent Change by Mid-Century"},
    "EDW_HydroFlowMetricsPercentChange2080_01": {"theme": "inland_waters", "desc": "Hydro Flow Metrics: Percent Change by End-of-Century"},
    "EDW_Watersheds_01": {"theme": "inland_waters", "desc": "Watershed Condition Classification"},
    "EDW_PriorityWatersheds_01": {"theme": "inland_waters", "desc": "Priority Watersheds"},
    # --- Planning Cadastre ---
    "EDW_LandUtilization_01": {"theme": "planning_cadastre", "desc": "Land Utilization"},
    "EDW_PLSS_01": {"theme": "planning_cadastre", "desc": "Public Land Survey System: corners, monuments, sections, townships"},
    # --- Structure ---
    "EDW_CommunicationsSites_01": {"theme": "structure", "desc": "Communications Sites Special Use Authorizations"},
    "EDW_DevelopedSites_01": {"theme": "structure", "desc": "Forest Service developed sites subject to regulation"},
    "EDW_ResearchStationFacilities_01": {"theme": "structure", "desc": "Research Station Facilities"},
    # --- Transportation ---
    "EDW_MVUM_Roads_01": {"theme": "transportation", "desc": "Motor Vehicle Use Map: Roads"},
    "EDW_MVUM_Trails_01": {"theme": "transportation", "desc": "Motor Vehicle Use Map: Trails"},
    "EDW_Roads_01": {"theme": "transportation", "desc": "National Forest System Roads"},
    "EDW_Trails_01": {"theme": "transportation", "desc": "National Forest System Trails"},
}

# Keyword aliases: map common search terms to themes or service name fragments.
# This allows searches like "riparian" to find relevant services even when the
# exact word doesn't appear in the service name.
_KEYWORD_ALIASES: dict[str, list[str]] = {
    # Water-related
    "riparian": ["inland_waters", "stream", "hydro", "watershed", "aquatic", "norwest"],
    "wetland": ["inland_waters", "hydro", "watershed"],
    "river": ["wild scenic river", "inland_waters", "hydro"],
    "creek": ["stream", "inland_waters", "hydro"],
    "lake": ["inland_waters", "hydro", "watershed"],
    "fish": ["aquatic organism", "climate shield", "norwest", "biota"],
    "aquatic": ["aquatic", "inland_waters", "stream", "climate shield"],
    # Fire-related
    "fire": ["fire", "mtbs", "burn", "ravg", "fireshed", "retardant"],
    "burn": ["mtbs", "burn", "baer", "ravg", "fire"],
    "wildfire": ["fire", "mtbs", "burn", "ravg", "fireshed"],
    # Vegetation / ecology
    "vegetation": ["vegetation", "existing vegetation", "biota", "calveg"],
    "timber": ["timber", "silviculture", "harvest"],
    "ecology": ["ecological", "ecoregion", "ecomap", "bailey", "geoscientific"],
    "habitat": ["climate shield", "aquatic organism", "biota"],
    "invasive": ["invasive", "biota"],
    # Land management
    "ownership": ["ownership", "basic ownership", "surface ownership", "padus"],
    "wilderness": ["wilderness", "roadless"],
    "recreation": ["recreation", "trail", "mvum"],
    "grazing": ["range vegetation", "grassland"],
    "mining": ["mineral", "hazardous"],
    "trail": ["trail", "mvum", "transportation"],
    "road": ["road", "mvum", "transportation"],
    # Administrative
    "ranger": ["ranger district", "boundaries"],
    "boundary": ["boundaries", "forest system", "proclaimed", "regional"],
    "tribal": ["tribal", "ceded"],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_services(query: str = "", theme: str = "") -> list[dict[str, str]]:
    """Search EDW services by keyword and/or theme.

    Uses three matching strategies (results are deduplicated):
    1. Substring match on service name (e.g. "mtbs" → EDW_MTBS_01)
    2. Keyword alias expansion (e.g. "riparian" → inland_waters theme + stream services)
    3. Theme filter (e.g. theme="inland_waters")

    Args:
        query: Search keyword (case-insensitive). Matches against service names,
               theme descriptions, and keyword aliases.
        theme: Filter by theme category. Valid themes: biota, boundaries,
               environment, geoscientific, inland_waters, planning_cadastre,
               structure, transportation. Pass "" to skip theme filtering.

    Returns a list of dicts with keys: name, type, url, theme, description.
    If both query and theme are empty, returns all services.
    """
    data = fetch_json(EDW_BASE_URL, {"f": "pjson"})
    services = data.get("services", [])

    query_lower = query.lower().strip()
    theme_lower = theme.lower().strip()

    # Build expanded search terms from keyword aliases
    alias_terms: list[str] = []
    alias_themes: list[str] = []
    if query_lower and query_lower in _KEYWORD_ALIASES:
        for term in _KEYWORD_ALIASES[query_lower]:
            if term in (
                "biota", "boundaries", "environment", "geoscientific",
                "inland_waters", "planning_cadastre", "structure", "transportation",
            ):
                alias_themes.append(term)
            else:
                alias_terms.append(term.lower())

    seen = set()
    results = []

    for svc in services:
        full_name = svc.get("name", "")  # e.g. "EDW/EDW_MTBS_01"
        svc_type = svc.get("type", "MapServer")
        short_name = full_name.split("/", 1)[-1] if "/" in full_name else full_name

        if short_name in seen:
            continue

        # Look up theme metadata
        meta = _SERVICE_THEMES.get(short_name, {})
        svc_theme = meta.get("theme", "")
        svc_desc = meta.get("desc", "")

        # Apply theme filter
        if theme_lower and svc_theme != theme_lower:
            continue

        matched = False

        if not query_lower:
            matched = True
        else:
            # Strategy 1: substring match on service name
            if query_lower in short_name.lower():
                matched = True
            # Strategy 2: substring match on theme description
            elif query_lower in svc_desc.lower():
                matched = True
            # Strategy 3: keyword alias — match expanded terms against name + desc
            elif alias_terms:
                name_and_desc = (short_name + " " + svc_desc).lower()
                if any(t in name_and_desc for t in alias_terms):
                    matched = True
            # Strategy 4: keyword alias — match expanded themes
            if not matched and alias_themes and svc_theme in alias_themes:
                matched = True

        if matched:
            seen.add(short_name)
            results.append(
                {
                    "name": short_name,
                    "type": svc_type,
                    "url": f"{EDW_BASE_URL}/{short_name}/{svc_type}",
                    "theme": svc_theme or "uncategorized",
                    "description": svc_desc,
                }
            )

    return results


def get_service_info(service_name: str) -> dict[str, Any]:
    """Get metadata for an EDW MapServer service: description, layers, spatial ref.

    Args:
        service_name: Short service name, e.g. "EDW_MTBS_01".

    Returns:
        Dict with keys: name, description, spatialReference, layers (id, name,
        geometryType, defaultVisibility, minScale, maxScale).
    """
    url = f"{EDW_BASE_URL}/{service_name}/MapServer"
    data = fetch_json(url, {"f": "pjson"})

    if "error" in data:
        raise RuntimeError(f"EDW service error: {data['error'].get('message', data['error'])}")

    layers = []
    for lyr in data.get("layers", []):
        layers.append(
            {
                "id": lyr.get("id"),
                "name": lyr.get("name", ""),
                "defaultVisibility": lyr.get("defaultVisibility", False),
                "minScale": lyr.get("minScale", 0),
                "maxScale": lyr.get("maxScale", 0),
            }
        )

    return {
        "name": data.get("mapName", service_name),
        "description": data.get("description", "").strip() or data.get("serviceDescription", "").strip(),
        "spatialReference": data.get("spatialReference", {}),
        "fullExtent": data.get("fullExtent", {}),
        "layers": layers,
    }


def get_layer_info(service_name: str, layer_id: int) -> dict[str, Any]:
    """Get detailed metadata for a specific layer: fields, geometry type, capabilities.

    Args:
        service_name: Short service name, e.g. "EDW_MTBS_01".
        layer_id: Layer ID within the service.

    Returns:
        Dict with keys: name, geometryType, description, fields, extent,
        maxRecordCount, supportedQueryFormats, capabilities (list of
        supported operations, e.g. ["Map", "Query", "Data"]),
        advancedQueryCapabilities (dict of supportsX booleans, e.g.
        supportsQueryAnalytic, supportsStatistics — varies by service).
    """
    url = f"{EDW_BASE_URL}/{service_name}/MapServer/{layer_id}"
    data = fetch_json(url, {"f": "pjson"})

    if "error" in data:
        raise RuntimeError(f"EDW layer error: {data['error'].get('message', data['error'])}")

    fields = []
    for f in data.get("fields", []):
        fields.append(
            {
                "name": f.get("name"),
                "type": f.get("type", "").replace("esriFieldType", ""),
                "alias": f.get("alias", ""),
            }
        )

    capabilities = [c.strip() for c in data.get("capabilities", "").split(",") if c.strip()]

    return {
        "name": data.get("name", ""),
        "geometryType": data.get("geometryType", ""),
        "description": data.get("description", ""),
        "fields": fields,
        "extent": data.get("extent", {}),
        "maxRecordCount": data.get("maxRecordCount", _MAX_RECORD_COUNT),
        "capabilities": capabilities,
        "advancedQueryCapabilities": data.get("advancedQueryCapabilities", {}),
        "supportedQueryFormats": data.get("supportedQueryFormats", ""),
    }

def get_layer_metadata(
    service_name: str, layer_id: int, include_domains: bool = False
) -> dict[str, Any]:
    """Fetch and parse a layer's FGDC/ISO metadata XML (the `/metadata` operation).

    This is a separate document from get_layer_info's REST JSON — it carries
    things the layer JSON doesn't: a dataset abstract/purpose, keywords, and
    per-field definitions (e.g. what "Post_ID" actually means).

    Args:
        service_name: Short service name, e.g. "EDW_MTBS_01".
        layer_id: Layer ID within the service.
        include_domains: If True, add a `domain` key to each attribute with
            its enumerated/range domain (see `attributes` below). Free-text
            ("udom") domains are not structured data and are ignored.

    Returns:
        Dict with keys:
            title, abstract, purpose, credit (str, "" if not present)
            keywords (sorted list of theme keywords)
            attributes: list of {name, alias, type, definition,
                definition_source} per field. `definition` is "" for
                fields the data provider didn't document (e.g. OBJECTID
                clones, raw thresholds). If `include_domains` is True, each
                entry also has `domain`: None, or a dict with either/both:
                    - "coded_values": [{"value", "definition",
                      "definition_source"}, ...] for enumerated domains
                    - "range": {"min", "max"} for numeric range domains
                A field can have both (e.g. a numeric field using -9999 as
                a documented "no data" sentinel alongside its real range).

    Raises:
        RuntimeError: If the request fails or the response isn't valid XML
            (some layers have no metadata document configured).
    """
    url = f"{EDW_BASE_URL}/{service_name}/MapServer/{layer_id}/metadata"
    raw = fetch_text(url)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise RuntimeError(f"Layer {service_name}/{layer_id} has no parsable metadata document") from exc

    data_id = root.find("dataIdInfo")
    title = ""
    if data_id is not None:
        title = (data_id.findtext("idCitation/resTitle") or "").strip()

    def _clean(text: str | None) -> str:
        text = html.unescape(text or "")
        return re.sub(r"<[^>]+>", " ", text).strip()
        # idAbs/idPurp/idCredit embed literal HTML markup in their text content

    def _parse_domain(attr_el: ET.Element) -> dict[str, Any] | None:
        coded_values = [
            {
                "value": edom.findtext("edomv", ""),
                "definition": _clean(edom.findtext("edomvd")),
                "definition_source": edom.findtext("edomvds", ""),
            }
            for domv in attr_el.findall("attrdomv")
            for edom in domv.findall("edom")
        ]
        rdom_el = attr_el.find("attrdomv/rdom")
        domain: dict[str, Any] = {}
        if coded_values:
            domain["coded_values"] = coded_values
        if rdom_el is not None:
            domain["range"] = {
                "min": rdom_el.findtext("rdommin"),
                "max": rdom_el.findtext("rdommax"),
            }
        return domain or None

    keywords = sorted(
        {
            kw.text.strip()
            for keys in root.findall("dataIdInfo/themeKeys")
            for kw in keys.findall("keyword")
            if kw.text and kw.text.strip()
        }
    )

    attributes = []
    for attr in root.findall("eainfo/detailed/attr"):
        entry = {
            "name": attr.findtext("attrlabl", ""),
            "alias": attr.findtext("attalias", ""),
            "type": attr.findtext("attrtype", ""),
            "definition": _clean(attr.findtext("attrdef")),
            "definition_source": attr.findtext("attrdefs", ""),
        }
        if include_domains:
            entry["domain"] = _parse_domain(attr)
        attributes.append(entry)

    return {
        "title": title,
        "abstract": _clean(data_id.findtext("idAbs") if data_id is not None else None),
        "purpose": _clean(data_id.findtext("idPurp") if data_id is not None else None),
        "credit": _clean(data_id.findtext("idCredit") if data_id is not None else None),
        "keywords": keywords,
        "attributes": attributes,
    }


def query_features(
    service_name: str,
    layer_id: int,
    geometry: dict | str | None = None,
    geometry_type: str = "esriGeometryEnvelope",
    spatial_rel: str = "esriSpatialRelIntersects",
    where: str = "1=1",
    out_fields: str = "*",
    max_features: int = 1000,
    out_sr: int = 4326,
    return_count_only: bool = False,
) -> dict:
    """Query features from an EDW layer, optionally filtered by spatial intersection.

    Args:
        service_name: Short service name, e.g. "EDW_MTBS_01".
        layer_id: Layer ID within the service.
        geometry: Geometry for spatial filter. Can be:
            - A GeoJSON geometry dict (Point, Polygon, Envelope-style bbox)
            - An Esri JSON geometry string/dict
            - A bbox string "xmin,ymin,xmax,ymax"
            - None for no spatial filter
        geometry_type: Esri geometry type. Common values:
            esriGeometryPoint, esriGeometryEnvelope, esriGeometryPolygon
        spatial_rel: Spatial relationship. Default: esriSpatialRelIntersects.
        where: SQL WHERE clause. Default: "1=1" (all features).
        out_fields: Comma-separated field names or "*" for all.
        max_features: Maximum features to return (capped at server max).
        out_sr: Output spatial reference WKID. Default: 4326 (WGS84).
        return_count_only: If True, return only the count of matching features.

    Returns:
        GeoJSON FeatureCollection dict, or {"count": N} if return_count_only.
    """
    url = f"{EDW_BASE_URL}/{service_name}/MapServer/{layer_id}/query"

    params: dict[str, str] = {
        "where": where,
        "outFields": out_fields,
        "outSR": str(out_sr),
        "f": "geojson",
        "resultRecordCount": str(min(max_features, _MAX_RECORD_COUNT)),
    }

    if return_count_only:
        params["returnCountOnly"] = "true"
        params["f"] = "json"

    if geometry is not None:
        geom_str = _convert_geometry(geometry, geometry_type)
        params["geometry"] = geom_str
        params["geometryType"] = geometry_type
        params["spatialRel"] = spatial_rel
        params["inSR"] = str(out_sr)

    # Use POST for large payloads (polygon geometries can be big)
    data = post_json(url, params)

    if "error" in data:
        raise RuntimeError(f"EDW query error: {data['error'].get('message', data['error'])}")

    if return_count_only:
        return {"count": data.get("count", 0)}

    return _sanitize_geojson(data)


def _build_out_analytics(out_analytics: list[dict[str, Any]]) -> str:
    """Translate simplified analytic defs into Esri's outAnalytics JSON.

    Each input dict has keys: type, field (optional for COUNT/ROW_NUMBER),
    out_name, and optionally order_by (for LAG/LEAD/ranking functions) and
    params (dict merged into analyticParameters, e.g. {"offset": 1} for
    LAG/LEAD or {"buckets": 4} for NTILE).
    """
    analytics = []
    for a in out_analytics:
        entry: dict[str, Any] = {
            "analyticType": a["type"],
            "outStatisticFieldName": a["out_name"],
        }
        if "field" in a:
            entry["onStatisticField"] = a["field"]

        analytic_params: dict[str, Any] = dict(a.get("params", {}))
        if "order_by" in a:
            analytic_params["orderBy"] = a["order_by"]
        if analytic_params:
            entry["analyticParameters"] = analytic_params

        analytics.append(entry)
    return json.dumps(analytics)


def query_features_analytic(
    service_name: str,
    layer_id: int,
    out_analytics: list[dict[str, Any]],
    partition_by: str | list[str] | None = None,
    order_by_fields: str | None = None,
    where: str = "1=1",
    analytic_where: str | None = None,
    geometry: dict | str | None = None,
    geometry_type: str = "esriGeometryEnvelope",
    spatial_rel: str = "esriSpatialRelIntersects",
    out_fields: str = "*",
    out_sr: int = 4326,
    return_geometry: bool = True,
) -> dict:
    """Run SQL window-function analytics (rank, running total, LAG/LEAD, ...)
    against an EDW layer via the ArcGIS queryAnalytic operation.

    Unlike query_features, rows are not collapsed — each analytic value is
    appended as a new field on its source feature. Use `analytic_where` to
    filter down to specific computed rows (e.g. "rank_val = 1" to keep only
    the top-ranked feature per partition).

    Args:
        service_name: Short service name, e.g. "EDW_ActivityTimberHarvests_01".
        layer_id: Layer ID within the service.
        out_analytics: List of analytic definitions, each a dict with:
            - type (str): Esri analytic type, e.g. "RANK", "SUM", "LAG",
              "ROW_NUMBER", "PERCENTILE_CONT".
            - field (str, optional): Input field the analytic operates on
              (omit for COUNT/ROW_NUMBER).
            - out_name (str): Output field name for the computed value.
            - order_by (str, optional): Field ordering within each
              partition, e.g. "ACRES DESC" (required by ranking and
              LAG/LEAD functions).
            - params (dict, optional): Extra analyticParameters, e.g.
              {"offset": 1} for LAG/LEAD, {"buckets": 4} for NTILE.
        partition_by: Field name(s) to group rows into separate windows,
            e.g. "RANGER_DISTRICT" or ["FOREST_NAME", "YEAR"]. Omit to
            treat the whole result set as one partition.
        order_by_fields: Sort order applied before analytics are computed
            (separate from an analytic's own `order_by`).
        where: SQL WHERE clause filtering the source rows.
        analytic_where: SQL filter applied *after* analytics are computed,
            evaluated against output field names (e.g. "acres_rank = 1").
        geometry, geometry_type, spatial_rel: Spatial AOI filter, same as
            query_features.
        out_fields: Comma-separated field names or "*" for all.
        out_sr: Output spatial reference WKID. Default: 4326 (WGS84).
        return_geometry: If False, omit geometry from results (cheaper when
            only the computed analytic values are needed).

    Returns:
        GeoJSON FeatureCollection dict with analytic fields appended to
        each feature's properties.

    Example::

        # Largest timber harvest per ranger district within an AOI
        result = query_features_analytic(
            "EDW_ActivityTimberHarvests_01", 0,
            out_analytics=[{
                "type": "RANK", "field": "ACRES",
                "order_by": "ACRES DESC", "out_name": "acres_rank",
            }],
            partition_by="RANGER_DISTRICT",
            analytic_where="acres_rank = 1",
            geometry=aoi_bbox,
        )
    """
    url = f"{EDW_BASE_URL}/{service_name}/MapServer/{layer_id}/queryAnalytic"

    params: dict[str, str] = {
        "where": where,
        "outFields": out_fields,
        "outSR": str(out_sr),
        "f": "json",  # queryAnalytic does not support f=geojson (returns 400)
        "returnGeometry": "true" if return_geometry else "false",
        "outAnalytics": _build_out_analytics(out_analytics),
    }
    if partition_by:
        params["partitionBy"] = (
            ",".join(partition_by) if isinstance(partition_by, list) else partition_by
        )
    if order_by_fields:
        params["orderByFields"] = order_by_fields
    if analytic_where:
        params["analyticWhere"] = analytic_where

    if geometry is not None:
        geom_str = _convert_geometry(geometry, geometry_type)
        params["geometry"] = geom_str
        params["geometryType"] = geometry_type
        params["spatialRel"] = spatial_rel
        params["inSR"] = str(out_sr)

    data = post_json(url, params)

    if "error" in data:
        raise RuntimeError(f"EDW queryAnalytic error: {data['error'].get('message', data['error'])}")

    return _sanitize_geojson(_esri_json_to_geojson(data))


def top_n_per_group(
    service_name: str,
    layer_id: int,
    field: str,
    group_by: str | list[str],
    n: int = 1,
    descending: bool = True,
    where: str = "1=1",
    geometry: dict | str | None = None,
    geometry_type: str = "esriGeometryEnvelope",
    spatial_rel: str = "esriSpatialRelIntersects",
    out_fields: str = "*",
    out_sr: int = 4326,
) -> dict:
    """Get the top N features by `field` within each `group_by` partition.

    Convenience wrapper around query_features_analytic using RANK() +
    analytic_where to collapse results to just the top N rows per group,
    e.g. the largest timber harvest per ranger district.

    Args:
        service_name: Short service name, e.g. "EDW_ActivityTimberHarvests_01".
        layer_id: Layer ID within the service.
        field: Field to rank by, e.g. "ACRES".
        group_by: Field name(s) defining each group/partition, e.g.
            "RANGER_DISTRICT" or ["FOREST_NAME", "YEAR"].
        n: How many top features to keep per group. Default: 1.
        descending: If True (default), rank largest-first (top = max value).
            If False, rank smallest-first (top = min value).
        where: SQL WHERE clause filtering the source rows.
        geometry, geometry_type, spatial_rel: Spatial AOI filter, same as
            query_features.
        out_fields: Comma-separated field names or "*" for all.
        out_sr: Output spatial reference WKID. Default: 4326 (WGS84).

    Returns:
        GeoJSON FeatureCollection dict containing only the top N features
        per group, with a `rank_val` field appended to each.

    Example::

        # Largest timber harvest per ranger district within an AOI
        result = top_n_per_group(
            "EDW_ActivityTimberHarvests_01", 0,
            field="ACRES", group_by="RANGER_DISTRICT",
            geometry=aoi_bbox,
        )
    """
    direction = "DESC" if descending else "ASC"
    return query_features_analytic(
        service_name,
        layer_id,
        out_analytics=[{
            "type": "RANK",
            "field": field,
            "order_by": f"{field} {direction}",
            "out_name": "rank_val",
        }],
        partition_by=group_by,
        analytic_where=f"rank_val <= {n}",
        where=where,
        geometry=geometry,
        geometry_type=geometry_type,
        spatial_rel=spatial_rel,
        out_fields=out_fields,
        out_sr=out_sr,
    )


def _convert_geometry(geometry: dict | str, geometry_type: str) -> str:
    """Convert geometry input to Esri-compatible JSON string for query params.

    Handles:
    - Bbox string "xmin,ymin,xmax,ymax" → Esri envelope JSON
    - GeoJSON geometry dict → Esri JSON
    - Already-Esri JSON dict → pass through
    - String → pass through
    """
    # Simple bbox string
    if isinstance(geometry, str):
        # Check if it's a simple bbox: "xmin,ymin,xmax,ymax"
        parts = geometry.split(",")
        if len(parts) == 4:
            try:
                xmin, ymin, xmax, ymax = [float(p.strip()) for p in parts]
                return json.dumps(
                    {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}
                )
            except ValueError:
                pass
        return geometry  # pass through as-is

    if isinstance(geometry, dict):
        # Already Esri-style envelope
        if "xmin" in geometry:
            return json.dumps(geometry)

        # Already Esri-style rings/points
        if "rings" in geometry or "x" in geometry or "points" in geometry:
            return json.dumps(geometry)

        # GeoJSON → Esri JSON conversion
        geom_type = geometry.get("type", "")
        coords = geometry.get("coordinates")

        if geom_type == "Point" and coords:
            return json.dumps({"x": coords[0], "y": coords[1]})

        if geom_type == "Polygon" and coords:
            # GeoJSON polygon rings → Esri rings
            return json.dumps({"rings": coords})

        if geom_type == "MultiPolygon" and coords:
            # Flatten all rings
            rings = []
            for polygon in coords:
                rings.extend(polygon)
            return json.dumps({"rings": rings})

        # Fallback: serialize as-is
        return json.dumps(geometry)

    return json.dumps(geometry)


def _esri_geometry_to_geojson(geometry: dict | None) -> dict | None:
    """Convert an Esri JSON geometry (x/y, points, paths, or rings) to GeoJSON.

    Polygons are converted as a flat list of rings without hole/multipart
    detection — fine for simple polygons, but a ring-orientation pass would
    be needed to correctly split true multipart polygons or interior holes.
    """
    if not geometry:
        return None
    if "x" in geometry and "y" in geometry:
        return {"type": "Point", "coordinates": [geometry["x"], geometry["y"]]}
    if "points" in geometry:
        return {"type": "MultiPoint", "coordinates": geometry["points"]}
    if "paths" in geometry:
        paths = geometry["paths"]
        if len(paths) == 1:
            return {"type": "LineString", "coordinates": paths[0]}
        return {"type": "MultiLineString", "coordinates": paths}
    if "rings" in geometry:
        return {"type": "Polygon", "coordinates": geometry["rings"]}
    return None


def _esri_json_to_geojson(data: dict) -> dict:
    """Convert an Esri JSON (f=json) query response into a GeoJSON FeatureCollection."""
    features = [
        {
            "type": "Feature",
            "properties": feat.get("attributes", {}),
            "geometry": _esri_geometry_to_geojson(feat.get("geometry")),
        }
        for feat in data.get("features", [])
    ]
    return {"type": "FeatureCollection", "features": features}


def _sanitize_geojson(geojson: dict) -> dict:
    """Sanitize a GeoJSON FeatureCollection for downstream compatibility.

    - Removes properties with dots in the name (e.g. 'SHAPE.LEN') — many
      consumers (e.g. Earth Engine) reject dotted property keys
    - Ensures each feature has a string 'id'
    """
    for i, feat in enumerate(geojson.get("features", [])):
        props = feat.get("properties", {})
        bad_keys = [k for k in props if "." in k]
        for k in bad_keys:
            del props[k]
        feat["id"] = str(i)
    return geojson


def query_features_with_pagination(
    service_name: str,
    layer_id: int,
    geometry: dict | str | None = None,
    geometry_type: str = "esriGeometryEnvelope",
    spatial_rel: str = "esriSpatialRelIntersects",
    where: str = "1=1",
    out_fields: str = "*",
    max_features: int = 5000,
    out_sr: int = 4326,
) -> dict:
    """Query features with automatic pagination to get more than 2000 results.

    Same args as query_features, but max_features can exceed the server limit.
    Returns a combined GeoJSON FeatureCollection.
    """
    all_features = []
    offset = 0
    page_size = min(max_features, _MAX_RECORD_COUNT)

    while len(all_features) < max_features:
        url = f"{EDW_BASE_URL}/{service_name}/MapServer/{layer_id}/query"
        params: dict[str, str] = {
            "where": where,
            "outFields": out_fields,
            "outSR": str(out_sr),
            "f": "geojson",
            "resultRecordCount": str(page_size),
            "resultOffset": str(offset),
        }

        if geometry is not None:
            geom_str = _convert_geometry(geometry, geometry_type)
            params["geometry"] = geom_str
            params["geometryType"] = geometry_type
            params["spatialRel"] = spatial_rel
            params["inSR"] = str(out_sr)

        data = post_json(url, params)

        if "error" in data:
            raise RuntimeError(f"EDW query error: {data['error'].get('message', data['error'])}")

        features = data.get("features", [])
        if not features:
            break

        all_features.extend(features)
        offset += len(features)

        # If we got fewer than page_size, we've hit the end
        if len(features) < page_size:
            break

    # Trim to max_features
    all_features = all_features[:max_features]

    return _sanitize_geojson({
        "type": "FeatureCollection",
        "features": all_features,
    })
