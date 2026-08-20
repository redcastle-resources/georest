"""
USFS Enterprise Data Warehouse (EDW) REST API client.

Provides search, metadata inspection, and spatial feature queries against
the ArcGIS REST services at https://apps.fs.usda.gov/arcx/rest/services/EDW.

Quick start::

    import edw

    # Search for fire-related services (EDW catalog only — see search_edw_services)
    services = edw.search_edw_services("fire")

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
# Regenerated 2026-08-07 against the live catalog (GET {EDW_BASE_URL}?f=json)
# and https://data.fs.usda.gov/geodata/edw/datasets.php (per-category pages
# + the default/unfiltered page, which together cover more of the catalog
# than any single category filter). Services the FS page doesn't document at
# all (no bulk-download option, e.g. WUI) fall back to their own MapServer
# `description`. A handful of themes are manually corrected where the FS
# site's own categorization is inconsistent with itself (see inline notes).
# This enables search by theme keyword (e.g. "riparian" → inland waters services)
# ---------------------------------------------------------------------------

_SERVICE_THEMES: dict[str, dict[str, str]] = {
    # --- Biota ---
    "EDW_EcomapSections_01": {"theme": "biota", "desc": "The EcoMap Provinces feature class contains ecological province polygons attributed with names and descriptions."},
    "EDW_ExistingVegetationRegion05_01": {"theme": "biota", "desc": "This polygon layer consists of boundaries for the ecological tile units and CALVEG (Classification and Assessment with Landsat of Visible Ecological…"},
    "EDW_InvasiveSpecies_01": {"theme": "biota", "desc": "The Current Invasive Plants (InvasivePlantCurrent) feature class contains only the most recent or latest invasive Plant Infestation polygons…"},
    "EDW_PeriodicalCicadaBroods_01": {"theme": "biota", "desc": "Active Periodical Cicada Broods of the United States."},
    # --- Boundaries ---
    "EDW_ALPStatusAndEncumbrance_01": {"theme": "boundaries", "desc": "A map service designed to portray US Forest Service Land Status Record System data."},
    "EDW_BILLandscapeInvestments_01": {"theme": "boundaries", "desc": "The Bipartisan Infrastructure Law (a.k.a Infrastructure Investment Jobs Act) and the Inflation Reduction Act include significant funding to execute…"},
    "EDW_BasicOwnership_01": {"theme": "boundaries", "desc": "This simplified dataset is appropriate for general mapping and analysis."},
    "EDW_BasicOwnership_02": {"theme": "boundaries", "desc": "A map service on the www depicting areas as surface ownership parcels dissolved on the same ownership classification."},
    "EDW_CERCLASite_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide a record of Forest Service CERCLA sites, where restricted uses or engineered waste repositories are located."},
    "EDW_CongressionalDistricts_01": {"theme": "boundaries", "desc": "A map service depicting the spatial representation of the United States Congressional Districts."},
    "EDW_CongressionalDistricts_04": {"theme": "boundaries", "desc": "A map service depicting the spatial representation of the United States Congressional Districts of the 116th Congress."},
    "EDW_CornersAndMonuments_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_County_01": {"theme": "boundaries", "desc": "A map service on the www depicting the spatial representation of the United States Counties and equivalent governmental units."},
    "EDW_DevelopedSite_01": {"theme": "boundaries", "desc": "This is not a comprehensive set of Forest Service buildings or facilities."},
    "EDW_ExperimentalForestandRange_01": {"theme": "boundaries", "desc": "This polygon feature class contains the boundaries of 86 of 87 experimental forests, ranges and watersheds, including cooperating experimental areas."},
    "EDW_FIALndcvCntyEst_01": {"theme": "boundaries", "desc": "This feature class represents forest area estimates (and percent sampling error) by county for the year 2015."},
    "EDW_ForestCommonNames_01": {"theme": "boundaries", "desc": "Basic Description: The FSCommonNames dataset contains the common names of the national forests and grasslands and their respective FS WWW URL…"},
    "EDW_ForestSystemBoundaries_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_GeopoliticalUnit_01": {"theme": "boundaries", "desc": "This dataset is a comprehensive representation of geopolitical data for the entire area of the United States and territories."},
    "EDW_LandAndWaterConservationFundParcels_01": {"theme": "boundaries", "desc": "Land and Water Conservation Fund (LWCF) data from surface ownership fund table is attached to surface ownership to create a base layer that is used…"},
    "EDW_LandManagementPlanningUnit_01": {"theme": "boundaries", "desc": "A land management plan provides a framework for integrated resource management and for guiding project and activity decision-making on a National…"},
    "EDW_LandUtilizationProject_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_LandsAreaStatistics_01": {"theme": "boundaries", "desc": "This dataset fulfills a request from multiple regional units as an operational aid that provides an authoritative companion to the Land Areas Report…"},
    "EDW_MAPLandEasement_01": {"theme": "boundaries", "desc": "The Act requires agencies to define a GIS transfer spatial data layer depicting access easements to federal land."},
    "EDW_MineralRights_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_NFSLandUnit_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_NationalGrassland_01": {"theme": "boundaries", "desc": "The purpose of the data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_OtherNationalDesignatedAreaStatus_01": {"theme": "boundaries", "desc": "This dataset represents the transactional boundaries of areas (excluding National Wilderness and National Wild & Scenic Rivers) which impose…"},
    "EDW_OtherNationalDesignatedArea_01": {"theme": "boundaries", "desc": "These show the current boundaries of areas which impose management or use restrictions on National Forest System (NFS) lands."},
    "EDW_OtherSubSurfaceRight_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_OtherSurfaceRight_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_PADUS_01": {"theme": "boundaries", "desc": "This is a staging dataset created for integration into the official PAD-US dataset and is not intended for regular use."},
    "EDW_PILT_ASR_01": {"theme": "boundaries", "desc": "Payments In Lieu of Taxes (PILT) and All Service Receipts (ASR) are combined into a base layer that is used in Forest Service business functions, as…"},
    "EDW_PacificIslandsAdminBoundaries_01": {"theme": "boundaries", "desc": "A map service on the www depicting the boundaries of the Pacific Island unincorporated territories (American Samoa, Commonwealth of the Northern…"},
    "EDW_ProclaimedForestBoundaries_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_ProclaimedForestsAndGrasslands_01": {"theme": "boundaries", "desc": "This layer includes both Proclaimed Forest and National Grassland boundary areas, which allows the two boundaries to be more easily used in tandem."},
    "EDW_PurchaseUnit_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_QuarterSection_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_RangerDistricts_01": {"theme": "boundaries", "desc": "The purpose of the data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_RangerDistricts_03": {"theme": "boundaries", "desc": "A map service on the www depicting the boundary that encompasses a Ranger District."},
    "EDW_RegionBoundaries_01": {"theme": "boundaries", "desc": "The purpose of the data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_ResearchStations_01": {"theme": "boundaries", "desc": "These data are a polygon feature class that represents the administrative boundaries of the US Forest Service Research and Development Stations."},
    "EDW_RightofWay_01": {"theme": "boundaries", "desc": "This dataset provides a preliminary determination of where and what type of rights of way exist."},
    "EDW_SMABoundary_01": {"theme": "boundaries", "desc": "This dataset indicates the location and condition of marked and posted boundaries on the ground."},
    "EDW_Section_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_SilvicultureReforestationNeeds_01": {"theme": "boundaries", "desc": "The SilvReforestation feature class represents activities associated with the following performance measure: Forest Vegetation Establishment…"},
    "EDW_SilvicultureTimberstandImprovementNeeds_01": {"theme": "boundaries", "desc": "The SilvTSI (Silviculture Timber Stand Improvement) feature class represents activities associated with the following performance measure: Forest…"},
    "EDW_SpecialInterestManagementArea_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_SpecialStatusArea_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_State_01": {"theme": "boundaries", "desc": "A map service on the www depicting the location and boundaries of the United States, the District of Columbia and the US territories."},
    "EDW_SurfaceOwnership_01": {"theme": "boundaries", "desc": "This dataset provides detailed information suitable for advanced mapping, analysis, and reporting of landownership status."},
    "EDW_Township_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_Tract_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    "EDW_TribalCessionLands_01": {"theme": "boundaries", "desc": "Sixty-seven maps from \"Indian Land Cessions in the United States,\" compiled by Charles C."},
    "EDW_TribalIndianLands_01": {"theme": "boundaries", "desc": "The TIGER/Line shapefiles and related database files (.dbf) are an extract of selected geographic and cartographic information from the U.S."},
    "EDW_WildScenicRiverActiveStudyRivers_01": {"theme": "boundaries", "desc": "A map service on the www that depicts the Active Study Reivers from the river corridors of each Wild and Scenic River designated by Congress or the…"},
    "EDW_WildScenicRiverEligibleSuitable_01": {"theme": "boundaries", "desc": "A map service on the www that depicts eligible, eligible/suitable, eligible/not suitable, and ineligible rivers found in wild and scenic river…"},
    "EDW_WildScenicRiverSegments_01": {"theme": "boundaries", "desc": "This polyline feature class depicts the river corridors of each Wild and Scenic River designated by Congress or the Secretary of the Interior for…"},
    "EDW_WildScenicRiverStatus_01": {"theme": "boundaries", "desc": "The boundaries in this data set provide detailed information suitable for advanced land status mapping and analysis."},
    "EDW_WildScenicRiver_01": {"theme": "boundaries", "desc": "River segments in this dataset are grouped together by Area Name and Area Type (wild, scenic, recreational), and are suitable for general mapping…"},
    "EDW_WildernessStatus_01": {"theme": "boundaries", "desc": "The boundaries in this dataset provide detailed information suitable for advanced land status mapping and analysis."},
    "EDW_WildernessStatus_02": {"theme": "boundaries", "desc": "A map service on the www depicting status of parcels for Forest Service land congressionally designated as wilderness such as National Wilderness…"},
    "EDW_Wilderness_01": {"theme": "boundaries", "desc": "These data show the current boundaries of National Wilderness areas, including all additions/deletions/modifications to boundaries which occurred…"},
    "EDW_Wilderness_02": {"theme": "boundaries", "desc": "A map service on the www depicting parcels of Forest Service land congressionally designated as wilderness such as National Wilderness Areas."},
    "EDW_Withdrawal_01": {"theme": "boundaries", "desc": "The purpose of these data is to provide display, identification, and analysis tools for determining current boundary information for Forest Service…"},
    # --- Environment ---
    "EDW_ActivityFactsCommonAttributes_01": {"theme": "environment", "desc": "The Forest Service's Natural Resource Manager (NRM) Forest Activity Tracking System (FACTS) is the agency standard for managing information about…"},
    "EDW_ActivityProjectAreas_01": {"theme": "environment", "desc": "Actv_ProjectArea_NEPA represents an area (polygon) within which one or more activities related to the National Environmental Policy Act (NEPA) are…"},
    "EDW_ActivityProjectAreas_FSNRCSJointChiefsLandscapeRestoration_01": {"theme": "environment", "desc": "FSNRC boundaries attributed with NRCS Regions"},
    "EDW_AerialFireRetardantAvoidanceAreas_Terrestrial_01": {"theme": "environment", "desc": "This data depicts terrestrial aerial fire retardant avoidance areas delivered as part of the 2011 Nationwide Aerial Application of Fire Retardant on…"},
    "EDW_AquaticOrganismPassage_01": {"theme": "environment", "desc": "This dataset provides USFS watershed improvement activities to barriers to upstream migration."},
    "EDW_BrushDisposal_01": {"theme": "environment", "desc": "The Brush Disposal Program (BD) was established in 1916."},
    "EDW_BurnedAreaEmergencyResponse_01": {"theme": "environment", "desc": "An initial Burned Area Reflectance Classification (BARC) dataset is created by analyzing a differenced Normalized Burn Ratio (dNBR) image, which was…"},
    "EDW_CFLRProjectAccomplishments_01": {"theme": "environment", "desc": "CFLRP_LN represents Collaborative Forest Landscape Restoration (CFLR) Program project activities in polyline vector format."},
    "EDW_ClimateShield_01": {"theme": "environment", "desc": "This feature class represents the historic (1980) scenario for bull trout, derived from the Climate Shield fish distribution models."},
    "EDW_ColoradoRoadlessAreas2012_01": {"theme": "environment", "desc": "This feature class describes the boundaries of Roadless Areas designated by the Colorado Roadless Rule of 2012 and managed by the U.S."},
    "EDW_CommWildfireDefenseGrant_01": {"theme": "environment", "desc": "Community Wildfire Defense Grants (CWDG) is a program administered by the Forest Service to help at-risk communities and Tribes plan for and reduce…"},
    "EDW_EPANonAttainmentAreaOzone_01": {"theme": "environment", "desc": "A map service on the www that shows areas in the U.S."},
    "EDW_EPANonAttainmentAreaPM25_01": {"theme": "environment", "desc": "A map service on the www showing areas in the U.S."},
    "EDW_Ecomap2025_01": {"theme": "environment", "desc": "The Ecomap 2025 contains a feature class for five different levels of the National Hierarchical Framework of Ecological Units (the Hierarchy."},
    "EDW_FireOccurrence6thEdition_01": {"theme": "environment", "desc": "This data publication contains a spatial database of wildfires that occurred in the United States from 1992 to 2020."},
    # NOTE: FS's datasets.php files this under "structure" -- inconsistent
    # with every other fire dataset (MTBS, FireOccurrence6thEdition,
    # FireshedRegistry), which it puts under "environment". Corrected here.
    "EDW_FireOccurrenceAndPerimeter_01": {"theme": "environment", "desc": "The FirePerimeterFinal polygon layer represents final mapped wildland fire perimeters."},
    "EDW_FireOccurrenceFIRESTAT_YRLY_01": {"theme": "environment", "desc": "The FIRESTAT (Fire Statistics System) Fire Occurrence point layer represents ignition points, or points of origin, from which individual wildland…"},
    "EDW_FireshedRegistry_01": {"theme": "environment", "desc": "The Fireshed Registry is a geospatial dashboard and decision tool built to organize information about wildfire transmission to communities and…"},
    "EDW_HFRA_EmergencySituationDetermination_01": {"theme": "environment", "desc": "Emergency Situation Determination (ESD) lands, per the Secretary's Memo 1078-006: Increasing Timber Production and Designating an Emergency…"},
    "EDW_HazardousFuelsTreatments_01": {"theme": "environment", "desc": "HazFuelTrt_LN (Hazardous Fuel Treatments - Line) represents activities of hazardous fuel treatment reduction."},
    "EDW_HealthyForestRestorationAct_01": {"theme": "environment", "desc": "The Healthy Forest Restoration Act feature class depicts National Forest System (NFS) Lands within 37 States designated under section 602 and 603 of…"},
    "EDW_HistoricalWoodlandsDensity_01": {"theme": "environment", "desc": "This dataset includes polygons with a minimum of 40 acres of woodlands per square mile as depicted in William H."},
    "EDW_HydroFlowMetrics2040_01": {"theme": "environment", "desc": "This file represents modeled streamflow across the contiguous United States, for the projected future mid-century time period (2030–2059), based on…"},
    "EDW_HydroFlowMetrics2080_01": {"theme": "environment", "desc": "This file represents modeled streamflow across the contiguous United States, for the projected future end-of-century time period (2070–2099), based…"},
    "EDW_HydroFlowMetricsAbsChange2040_01": {"theme": "environment", "desc": "This file represents modeled streamflow across the contiguous United States, for the absolute change between the historical (1977-2006) and…"},
    "EDW_HydroFlowMetricsAbsChange2080_01": {"theme": "environment", "desc": "This file represents modeled streamflow across the contiguous United States, for the absolute change between the historical (1977-2006) and…"},
    "EDW_HydroFlowMetricsHistorical_01": {"theme": "environment", "desc": "This file represents modeled streamflow across the contiguous United States, for the historical time period (1977-2006), based on gridded…"},
    "EDW_HydroFlowMetricsPercentChange2040_01": {"theme": "environment", "desc": "This file represents modeled streamflow across the contiguous United States, for the percent change between the historical (1977-2006) and projected…"},
    "EDW_HydroFlowMetricsPercentChange2080_01": {"theme": "environment", "desc": "This file represents modeled streamflow across the contiguous United States, for the percent change between the historical (1977-2006) and projected…"},
    "EDW_IRR_01": {"theme": "environment", "desc": "IRR_LN (Integrated Resource Restoration (IRR): Line) depicts the location of activities funded through the NFRR (National Forest Resource…"},
    "EDW_InfraGAOAProjects_01": {"theme": "environment", "desc": "This dataset contains the detailed information about the individual asset linear features such as roads and trails that make up Great American…"},
    "EDW_InfraNAMPPartnerAgreement_01": {"theme": "environment", "desc": "These data describe the approximate project location and financial elements of US Forest Service, Great American Outdoors Act (GAOA) projects that…"},
    "EDW_InfraRecreationSites_01": {"theme": "environment", "desc": "This dataset shows information about the USDA Forest Service constructed recreation sites used to populate the public facing webpages."},
    "EDW_InsectandDiseaseSurvey_01": {"theme": "environment", "desc": "A map service on the www depicting the locations of damage caused by insect, disease and other abiotic agents."},
    "EDW_InventoriedRoadlessAreas2001IdCo_01": {"theme": "environment", "desc": "The RoadlessArea_2001_ID_CO feature class describes the boundaries of all Roadless Areas managed by the U.S."},
    "EDW_InventoriedRoadlessAreas2001_01": {"theme": "environment", "desc": "This dataset is the official data for the 2001 Roadless Area Conservation Rule (36 CFR 294, Subpart B)."},
    "EDW_InventoriedRoadlessAreas2008Id_01": {"theme": "environment", "desc": "The BdyPlan_RoadlessArea_2008_ID feature class describes the boundaries of Roadless Areas designated by the Idaho Roadless Rule of 2008 and managed…"},
    "EDW_KnutsonVandenberg_01": {"theme": "environment", "desc": "no abstract available parent dataset: ActivityTrustFund"},
    "EDW_LandFASAB": {"theme": "environment", "desc": "In 2021, the Federal Accounting Standards Advisory Board (FASAB) initiated a tracking mechanism that requires all federal agencies that own or…"},
    "EDW_MBHRInitiatives_01": {"theme": "environment", "desc": "A map service depicting Monarch Butterfly Habitat Restoration (MBHR) activities as polygons."},
    "EDW_MTBS_01": {"theme": "environment", "desc": "The Monitoring Trends in Burn Severity (MTBS) Program assesses the frequency, extent, and magnitude (size and severity) of all large wildland fires…"},
    "EDW_NationalDisasterRecoveryEvents_01": {"theme": "environment", "desc": "Spatial data is collected by the National Disaster Recovery Team from each unit point of contact."},
    "EDW_NorWeST_ObservedPoints_01": {"theme": "environment", "desc": "This layer indicates the location of the observed stream temperature records used for the NorWeST database summaries."},
    "EDW_NorWeST_StreamTemperatures_01": {"theme": "environment", "desc": "This layer represents modeled stream temperatures derived from the NorWeST point feature class (NorWest_TemperaturePoints)."},
    "EDW_RAVG_v2_01": {"theme": "environment", "desc": "The USDA Forest Service Rapid Assessment of Vegetation Condition after Wildfire (RAVG) program produces geospatial and related data representing…"},
    "EDW_RangeManagement_01": {"theme": "environment", "desc": "Allotment is a feature class in the Rangeland Management data set."},
    "EDW_RangeVegImprovement_01": {"theme": "environment", "desc": "The RngVegImprove feature class depicts the area planned and accomplished areas treated as a part of the Range Vegetation Improvement program of…"},
    "EDW_RecInfraRecreationSites_02": {"theme": "environment", "desc": "This dataset shows information about the USDA Forest Service recreation sites used to populate the public facing web pages."},
    "EDW_RecreationAreaActivities_01": {"theme": "environment", "desc": "This dataset contains the recreation opportunity information that the Forest Service collects through the Recreation Portal and shares with the…"},
    "EDW_RecreationOpportunities_01": {"theme": "environment", "desc": "This dataset contains the recreation site opportunity information that the Forest Service collects through the Recreation Portal and shares with the…"},
    "EDW_SilvicultureReforestation_01": {"theme": "environment", "desc": "The SilvReforestation feature class represents activities associated with the following performance measure: Forest Vegetation Establishment…"},
    "EDW_SilvicultureTimberstandImprovement_01": {"theme": "environment", "desc": "The SilvTSI (Silviculture Timber Stand Improvement) feature class represents activities associated with the following performance measure: Forest…"},
    "EDW_StewardshipContracting_01": {"theme": "environment", "desc": "no abstract available parent dataset: ActivityInitiatives"},
    "EDW_TerrestrialEcologicalUnitsStatus_01": {"theme": "environment", "desc": "The purpose of this dataset is to display the extent of existing Terrestrial Ecological Unit inventory (TEUI) data internally to facilitate…"},
    "EDW_TimberAppraisalZones_01": {"theme": "environment", "desc": "Features in this dataset reprsent individual USFS Ranger Districts or USFS Administrative Forests Boundaries which compose a stumpage market."},
    "EDW_TimberHarvest_01": {"theme": "environment", "desc": "The TimeberHarvest feature class depicts the area planned and accomplished acres treated as a part of the Timber Harvest program of work, funded…"},
    "EDW_WBBS_01": {"theme": "environment", "desc": "WBBS_LN depicts the area of activities to implement the Western Bark Beetle Strategy."},
    "EDW_WUI_1990_01": {"theme": "environment", "desc": "The Wildland-Urban Interface (WUI) is the area where houses meet or intermingle with undeveloped wildland vegetation."},
    "EDW_WUI_2000_01": {"theme": "environment", "desc": "The Wildland-Urban Interface (WUI) is the area where houses meet or intermingle with undeveloped wildland vegetation."},
    "EDW_WUI_2010_01": {"theme": "environment", "desc": "The Wildland-Urban Interface (WUI) is the area where houses meet or intermingle with undeveloped wildland vegetation."},
    "EDW_WUI_2020_01": {"theme": "environment", "desc": "The Wildland-Urban Interface (WUI) is the area where houses meet or intermingle with undeveloped wildland vegetation."},
    # --- Geoscientific Information ---
    "EDW_BaileysEcoregionsSubregions_01": {"theme": "geoscientific", "desc": "A map service on the www depicting Bailey's ecoregions and ecosystems of regional extent in the United States, Puerto Rico, and the U.S."},
    "EDW_TongassLandslide_01": {"theme": "geoscientific", "desc": "A public map service that depicts once-over landslide inventory of the Tongass National Forest."},
    # --- Inland Waters ---
    "EDW_AerialFireRetardantAvoidanceAreas_Aquatic_01": {"theme": "inland_waters", "desc": "This data depicts aquatic aerial fire retardant avoidance areas delivered as part of the 2011 Nationwide Aerial Application of Fire Retardant on…"},
    "EDW_ForeststoFaucets_02": {"theme": "inland_waters", "desc": "Forests to Faucets 2.0 builds upon the national Forests to Faucets(2011) by updating base data and adding new threats including wildfire, invasive…"},
    "EDW_GreatBasinMountainRangesWatersheds_01": {"theme": "inland_waters", "desc": "Multiple research and management partners collaboratively developed a multiscale approach for assessing the geomorphic sensitivity of streams and…"},
    "EDW_HydroPercentStreamFlowNFSAnnual_01": {"theme": "inland_waters", "desc": "This feature class contains water runoff metrics from Forest Service lands."},
    "EDW_HydroPercentStreamFlowNFSSummer_01": {"theme": "inland_waters", "desc": "Map service depicting mean summer flow from Forest Service lands."},
    "EDW_PriorityWatersheds_01": {"theme": "inland_waters", "desc": "The Watershed Condition Classification feature class represents data on Watershed Condition on Forest Service lands in HUC12 (from the Watershed…"},
    "EDW_Watersheds_01": {"theme": "inland_waters", "desc": "A map service depicting comprehensive aggregated collection of hydrologic unit data consistent with the national criteria for delineation and…"},
    # --- Planning Cadastre ---
    "EDW_LandManagementPlanning_01": {"theme": "planning_cadastre", "desc": "Contextual Definition of Geographic Areas: It is required for every National Forest to have a Land Management Plan (LMP), often referred to as a…"},
    # --- Structure ---
    "EDW_FSOfficeLocations_01": {"theme": "structure", "desc": "This data includes offices where Forest Service employees work or where IT equipment is housed."},
    "EDW_SpecialUsesCommunicationsSites_01": {"theme": "structure", "desc": "The purpose of the data is to provide display, identification, and analysis tools for determining locations of designated communications sites…"},
    # --- Transportation ---
    "EDW_MVUM_01": {"theme": "transportation", "desc": "The feature class indicates the specific types of motorized vehicles allowed on the designated routes and their seasons of use."},
    "EDW_MVUM_02": {"theme": "transportation", "desc": "A map service on the www depicting Forest Service roads and trails that are designated for motor vehicle use under the official U.S."},
    "EDW_RoadBasic_01": {"theme": "transportation", "desc": "Existing Forest Service roads with attributes representing their characteristics."},
    "EDW_TrailNFSPublishWithDataStatus_01": {"theme": "transportation", "desc": "A map service on the world wide web that depicts National Forest Service trails that have been approved for publication."},
    "EDW_TrailNFSPublish_01": {"theme": "transportation", "desc": "The TrailNFS_Publish Layer is designed to provide information about National Forest System trail locations and characteristics to the public."},
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


def search_edw_services(query: str = "", theme: str = "") -> list[dict[str, str]]:
    """Search the USFS EDW catalog ONLY by keyword and/or theme.

    IMPORTANT — scope: this searches exactly one flat catalog,
    ``EDW_BASE_URL`` (https://apps.fs.usda.gov/arcx/rest/services/EDW). It
    does NOT search IIPP, ArcGIS Online, or any other portal. To search
    IIPP (or another portal) instead, use
    ``RESTesri.portal.searchPortal(query, portal="iipp")`` — that function
    hits the portal's ``/sharing/rest/search`` endpoint directly and has no
    EDW-specific theme/keyword-alias matching.

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

    Note:
        Some EDW layers 500 or silently return zero features
        (exceededTransferLimit=True with an empty features list) for a
        spatial query against a geometrically complex AOI — many disjoint
        polygon parts and/or holes, e.g. a national forest boundary — even
        though the AOI itself is valid (returnCountOnly on the identical
        geometry reports the correct match count). This is an upstream
        ArcGIS Server limitation, not a client-side bug. When detected,
        this function automatically falls back to fetching the matching
        object IDs (which isn't subject to the same limitation) and then
        re-fetching features by ID with a plain attribute filter.
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

    if geometry is not None and data.get("exceededTransferLimit") and not data.get("features"):
        id_field, ids = _query_object_ids(
            service_name, layer_id, geometry, geometry_type, spatial_rel, where, out_sr
        )
        if ids:
            return _fetch_features_by_ids(
                service_name, layer_id, id_field, ids, out_fields, out_sr, max_features
            )

    return _sanitize_geojson(data)


def _query_object_ids(
    service_name: str,
    layer_id: int,
    geometry: dict | str,
    geometry_type: str,
    spatial_rel: str,
    where: str,
    out_sr: int,
) -> tuple[str, list[int]]:
    """Fetch just the object IDs matching a spatial + attribute filter.

    returnIdsOnly is cheap enough that it doesn't hit the exceededTransferLimit
    empty-features bug that a full feature fetch can hit against a
    geometrically complex AOI (verified against the live EDW server: a
    161-part multipolygon returns 0 features via a normal query, but the
    correct 40 object IDs via returnIdsOnly).
    """
    url = f"{EDW_BASE_URL}/{service_name}/MapServer/{layer_id}/query"
    params: dict[str, str] = {
        "where": where,
        "f": "json",
        "returnIdsOnly": "true",
        "geometry": _convert_geometry(geometry, geometry_type),
        "geometryType": geometry_type,
        "spatialRel": spatial_rel,
        "inSR": str(out_sr),
    }
    data = post_json(url, params)
    if "error" in data:
        raise RuntimeError(f"EDW query error: {data['error'].get('message', data['error'])}")
    return data.get("objectIdFieldName", "OBJECTID"), data.get("objectIds") or []


def _fetch_features_by_ids(
    service_name: str,
    layer_id: int,
    id_field: str,
    ids: list[int],
    out_fields: str,
    out_sr: int,
    max_features: int,
    chunk_size: int = _MAX_RECORD_COUNT,
) -> dict:
    """Fetch features for a known list of object IDs, in chunks.

    A plain attribute IN (...) filter carries no geometry payload, so it
    sidesteps the complex-geometry query limitation entirely — used to
    retrieve attributes/geometry for IDs already resolved by
    _query_object_ids.
    """
    ids = ids[:max_features]
    all_features: list[dict] = []
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start : start + chunk_size]
        id_list = ",".join(str(oid) for oid in chunk)
        result = query_features(
            service_name,
            layer_id,
            where=f"{id_field} IN ({id_list})",
            out_fields=out_fields,
            max_features=chunk_size,
            out_sr=out_sr,
        )
        all_features.extend(result["features"])
    return _sanitize_geojson({"type": "FeatureCollection", "features": all_features})


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

    if partition_by:
        # queryAnalytic has no top-level partitionBy request parameter — the
        # partition must be nested in each analytic's own analyticParameters,
        # or the server silently computes the analytic over the whole result
        # set instead of per partition (verified against the live EDW server).
        partition_str = ",".join(partition_by) if isinstance(partition_by, list) else partition_by
        out_analytics = [
            {**a, "params": {**a.get("params", {}), "partitionBy": partition_str}}
            for a in out_analytics
        ]

    params: dict[str, str] = {
        "where": where,
        "outFields": out_fields,
        "outSR": str(out_sr),
        "f": "json",  # queryAnalytic does not support f=geojson (returns 400)
        "returnGeometry": "true" if return_geometry else "false",
        "outAnalytics": _build_out_analytics(out_analytics),
    }
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
        per group, with a `rank_expr0` field appended to each.

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
        # ArcGIS's queryAnalytic ignores outStatisticFieldName for RANK and
        # always names the computed field "rank_expr0" (verified against the
        # live EDW server) — filter on that name, not the requested out_name.
        analytic_where=f"rank_expr0 <= {n}",
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
