"""get_layer_roles / get_detail_layers: role tagging, grouping, null-safety."""
import unittest

from georest.restesri import edw

from .support import fake_layers, layer, load_layer_cache, patched


def roles_for(layers):
    with fake_layers(layers):
        return {lyr["id"]: lyr["role"] for lyr in edw.get_layer_roles("X")}


class RoleAssignment(unittest.TestCase):
    """Roles against the layer shapes EDW actually publishes."""

    def test_two_tier_extent_split(self):
        # EDW_County_01: a national overview layer plus the real one.
        self.assertEqual(
            roles_for([layer(0, "County - National Extent", max_scale=1155581),
                       layer(1, "County - Regional Extent", min_scale=1155580)]),
            {0: "coarse", 1: "detail"},
        )

    def test_coarse_tier_may_have_a_min_scale(self):
        # EDW_CongressionalDistricts_04 bands its national tier on both
        # sides. A coarse test of "minScale == 0 and maxScale > 0" would
        # miss it and fail to deduplicate the service.
        self.assertEqual(
            roles_for([
                layer(0, "Congressional Districts - National Extent",
                      min_scale=77000000, max_scale=1000000),
                layer(1, "Congressional Districts - Regional Extent",
                      min_scale=1000001),
            ]),
            {0: "coarse", 1: "detail"},
        )

    def test_three_tier_pyramid_under_a_group(self):
        # The EDW_HydroFlowMetrics* shape: Low/Med/Hi resolution siblings.
        self.assertEqual(
            roles_for([
                layer(0, "Flow", "Group Layer"),
                layer(1, "Flow (Low-Res)", parent=0, max_scale=4622325),
                layer(2, "Flow (Med-Res)", parent=0, min_scale=4622325,
                      max_scale=1250001),
                layer(3, "Flow (Hi-Res)", parent=0, min_scale=1250000),
            ]),
            {0: "group", 1: "coarse", 2: "coarse", 3: "detail"},
        )

    def test_group_layers_are_returned_and_tagged(self):
        # Regression: group layers used to vanish from the output entirely,
        # contradicting the documented "returns all layers unchanged".
        self.assertEqual(
            roles_for([layer(0, "Fire", "Group Layer"),
                       layer(1, "Perimeters", parent=0),
                       layer(2, "Ignition Points", parent=0)]),
            {0: "group", 1: "standalone", 2: "standalone"},
        )

    def test_no_split_means_everything_standalone(self):
        got = roles_for([layer(1, "Activities - Points"),
                         layer(2, "Activities - Lines"),
                         layer(3, "Activities - Polygons")])
        self.assertEqual(set(got.values()), {"standalone"})

    def test_banded_pair_without_a_detail_member_is_kept_whole(self):
        # EDW_ForestCommonNames_01: the most detailed tier is itself banded,
        # so no member qualifies as detail and nothing may be discarded.
        got = roles_for([
            layer(0, "National Forest - National Extent", max_scale=4622326),
            layer(1, "National Forest - Regional Extent",
                  min_scale=4622325, max_scale=600001),
        ])
        self.assertEqual(set(got.values()), {"standalone"})


class GroupKeyAuditing(unittest.TestCase):
    """groupKey exposes why two layers were treated as versions of each other."""

    def test_parent_group_key_is_shared_by_siblings(self):
        with fake_layers([layer(0, "Fire", "Group Layer"),
                          layer(1, "A", parent=0, min_scale=100),
                          layer(2, "B", parent=0, max_scale=100)]):
            keys = {l["id"]: l["groupKey"] for l in edw.get_layer_roles("X")}
        self.assertIsNone(keys[0], "group layers have no group of their own")
        self.assertEqual(keys[1], ("parent", 0))
        self.assertEqual(keys[2], ("parent", 0))

    def test_name_group_key_strips_the_extent_suffix(self):
        with fake_layers([layer(1, "County - National Extent", max_scale=100),
                          layer(2, "County - Regional Extent", min_scale=100)]):
            keys = {l["id"]: l["groupKey"] for l in edw.get_layer_roles("X")}
        self.assertEqual(keys[1], ("name", "County"))
        self.assertEqual(keys[2], ("name", "County"))


class DegradedResponses(unittest.TestCase):
    """An empty layer list is a degraded response, not a layerless service."""

    def test_empty_or_missing_layers_raises(self):
        for label, payload in [("empty list", {"layers": []}),
                               ("key absent", {}),
                               ("explicit null", {"layers": None})]:
            with self.subTest(label), patched(
                fetch_json=lambda url, params=None, _p=payload, *a, **k: _p
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    edw.get_layer_roles("X")
                self.assertIn("no layers", str(ctx.exception).lower())

    def test_service_error_still_raises(self):
        with patched(fetch_json=lambda url, params=None, *a, **k:
                     {"error": {"message": "Layer not found"}}):
            with self.assertRaises(RuntimeError) as ctx:
                edw.get_layer_roles("X")
        self.assertIn("Layer not found", str(ctx.exception))


class NullSafety(unittest.TestCase):
    """ArcGIS emits keys that are present-but-null; dict defaults don't cover those."""

    CASES = {
        "minScale null": [dict(layer(1, "A"), minScale=None),
                          layer(2, "B", min_scale=1000)],
        "maxScale null": [dict(layer(1, "A"), maxScale=None),
                          layer(2, "B", min_scale=1000)],
        "both scales null": [dict(layer(1, "A"), minScale=None, maxScale=None),
                             dict(layer(2, "B"), minScale=None, maxScale=None)],
        "id null": [dict(layer(1, "A"), id=None), layer(2, "B")],
        "id absent": [{"name": "A", "type": "Feature Layer"}, layer(2, "B")],
        "name null": [dict(layer(1, "A"), name=None), layer(2, "B")],
        "parentLayerId null": [dict(layer(1, "A"), parentLayerId=None),
                               layer(2, "B")],
        "type null": [dict(layer(1, "A"), type=None), layer(2, "B")],
        "scales as strings": [layer(1, "A", min_scale="0", max_scale="1155581"),
                              layer(2, "A", min_scale="1155580", max_scale="0")],
    }

    def test_malformed_layers_do_not_crash(self):
        for label, layers in self.CASES.items():
            with self.subTest(label), fake_layers(layers):
                edw.get_layer_roles("X")
                edw.get_detail_layers("X")

    def test_null_id_is_reported_not_invented(self):
        with fake_layers([dict(layer(1, "A"), id=None), layer(2, "B")]):
            ids = [l["id"] for l in edw.get_layer_roles("X")]
        self.assertIn(None, ids)

    def test_null_scales_count_as_zero(self):
        # Neither coarse nor detail, so no member may be discarded.
        with fake_layers([dict(layer(1, "A"), minScale=None, maxScale=None),
                          dict(layer(2, "A"), minScale=None, maxScale=None)]):
            self.assertEqual(len(edw.get_detail_layers("X")), 2)


def _legacy_detail_layers(raw):
    """The pre-annotation implementation, preserved as a behavioural oracle."""
    layers = [{"id": l.get("id"), "name": l.get("name") or "",
               "type": l.get("type") or "",
               "parentLayerId": edw._int_or(l.get("parentLayerId"), -1),
               "minScale": edw._int_or(l.get("minScale"), 0),
               "maxScale": edw._int_or(l.get("maxScale"), 0)} for l in raw]
    group_ids = {l["id"] for l in layers if l["type"] == "Group Layer"}
    groups: dict = {}
    for lyr in layers:
        if lyr["type"] == "Group Layer":
            continue
        key = (("parent", lyr["parentLayerId"])
               if lyr["parentLayerId"] in group_ids
               else ("name", edw._EXTENT_SUFFIX_RE.sub("", lyr["name"]).strip()))
        groups.setdefault(key, []).append(lyr)
    kept = []
    for members in groups.values():
        detail = [m for m in members if m["minScale"] > 0 and m["maxScale"] == 0]
        coarse = [m for m in members if m["maxScale"] > 0]
        kept.extend(detail if detail and coarse else members)
    kept.sort(key=lambda l: edw._int_or(l["id"], -1))
    return [l["id"] for l in kept]


class RealCatalogRegression(unittest.TestCase):
    """Every real service's layer tree, replayed offline from a fixture."""

    @classmethod
    def setUpClass(cls):
        cls.cache = {k: v for k, v in load_layer_cache().items() if v}

    def test_fixture_covers_a_meaningful_slice_of_the_catalog(self):
        self.assertGreater(len(self.cache), 100)

    def test_detail_layers_matches_the_legacy_algorithm(self):
        """Annotation must be purely additive: the filter still picks the same layers."""
        for service, raw in self.cache.items():
            with self.subTest(service), fake_layers(raw):
                self.assertEqual(
                    [l["id"] for l in edw.get_detail_layers(service)],
                    _legacy_detail_layers(raw),
                )

    def test_every_layer_gets_a_known_role(self):
        for service, raw in self.cache.items():
            with self.subTest(service), fake_layers(raw):
                for lyr in edw.get_layer_roles(service):
                    self.assertIn(lyr["role"], edw.LAYER_ROLES)

    def test_roles_partition_the_layer_list(self):
        """Nothing is dropped: roles account for every layer the server sent."""
        for service, raw in self.cache.items():
            with self.subTest(service), fake_layers(raw):
                self.assertEqual(len(edw.get_layer_roles(service)), len(raw))

    def test_coarse_layers_always_have_a_detail_sibling(self):
        """A layer is only set aside when something supersedes it."""
        for service, raw in self.cache.items():
            with self.subTest(service), fake_layers(raw):
                by_group: dict = {}
                for lyr in edw.get_layer_roles(service):
                    if lyr["role"] != "group":
                        by_group.setdefault(lyr["groupKey"], []).append(lyr["role"])
                for key, group_roles in by_group.items():
                    if "coarse" in group_roles:
                        self.assertIn("detail", group_roles,
                                      f"{service} {key} discards with no replacement")

    def test_the_redesign_surfaces_previously_hidden_layers(self):
        hidden = 0
        for service, raw in self.cache.items():
            with fake_layers(raw):
                hidden += sum(1 for l in edw.get_layer_roles(service)
                              if l["role"] in ("group", "coarse"))
        self.assertGreater(hidden, 100,
                           "expected many group/coarse layers to become visible")


if __name__ == "__main__":
    unittest.main()
