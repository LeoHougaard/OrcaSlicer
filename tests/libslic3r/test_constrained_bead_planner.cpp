#include <catch2/catch_all.hpp>

#include "libslic3r/ConstrainedBeadPlanner.hpp"
#include "libslic3r/Exception.hpp"
#include "libslic3r/ExPolygon.hpp"
#include "libslic3r/ExtrusionEntityCollection.hpp"
#include "libslic3r/Point.hpp"
#include "libslic3r/Preset.hpp"

#include <algorithm>
#include <cmath>
#include <utility>
#include <vector>

using namespace Slic3r;

namespace {

ExtrusionPath make_path(const Point &a, const Point &b, float width = 0.4f)
{
    ExtrusionPath path(erInternalInfill, 0.04, width, 0.2f);
    path.polyline.points = {
        Point3(a, 0),
        Point3(b, 0)
    };
    return path;
}

Point mm_point(const double x, const double y)
{
    return Point::new_scale(x, y);
}

ExtrusionLoop make_smooth_loop(const double radius, const size_t vertices, const float width = 0.4f)
{
    ExtrusionPath path(erExternalPerimeter, 0.04, width, 0.2f);
    path.polyline.points.reserve(vertices + 1);
    for (size_t i = 0; i < vertices; ++i) {
        const double angle = 2.0 * PI * double(i) / double(vertices);
        path.polyline.points.emplace_back(mm_point(radius * std::cos(angle), radius * std::sin(angle)), 0);
    }
    path.polyline.points.emplace_back(path.polyline.points.front());
    return ExtrusionLoop(std::move(path));
}

CBPSettings test_settings()
{
    CBPSettings settings;
    settings.enabled = true;
    settings.max_candidates = 8;
    settings.layer_operation_budget = 1000;
    settings.bead_model = true;
    settings.check_containment = false;
    settings.check_same_layer_collision = false;
    settings.check_centerline_crossing = false;
    settings.check_double_back = false;
    return settings;
}

const ExtrusionMultiPath *single_committed_multipath(const ExtrusionEntityCollection &collection)
{
    if (collection.entities.size() != 1)
        return nullptr;

    const auto *island = dynamic_cast<const ExtrusionEntityCollection*>(collection.entities.front());
    if (island == nullptr || island->entities.size() != 1)
        return nullptr;

    return dynamic_cast<const ExtrusionMultiPath*>(island->entities.front());
}

} // namespace

TEST_CASE("Continuous extrusion is configurable", "[ConstrainedBeadPlanner]")
{
    DynamicPrintConfig config = DynamicPrintConfig::full_print_config();
    REQUIRE_NOTHROW(config.set_deserialize_strict("continuous_extrusion", "1"));
    REQUIRE(config.opt_bool("continuous_extrusion"));
}

TEST_CASE("Continuous extrusion settings are print preset options", "[ConstrainedBeadPlanner]")
{
    const std::vector<std::string> &keys = Preset::print_options();
    for (const char *key : {
        "continuous_extrusion",
        "cbp_scope",
        "cbp_max_candidates",
        "cbp_layer_operation_budget",
        "cbp_check_same_layer_collision",
        "cbp_check_centerline_crossing",
        "cbp_check_double_back",
        "cbp_bridge_mode_handling",
    })
        REQUIRE(std::find(keys.begin(), keys.end(), key) != keys.end());
}

TEST_CASE("CBP slicing mode enables continuous extrusion", "[ConstrainedBeadPlanner]")
{
    DynamicPrintConfig config = DynamicPrintConfig::full_print_config();
    REQUIRE_NOTHROW(config.set_deserialize_strict("slicing_mode", "constrained_bead_planner"));
    REQUIRE(config.opt_enum<SlicingMode>("slicing_mode") == SlicingMode::ConstrainedBeadPlanner);

    PrintObjectConfig object_config;
    object_config.slicing_mode.value = SlicingMode::ConstrainedBeadPlanner;
    REQUIRE(constrained_bead_planner_enabled(object_config));
}

TEST_CASE("Continuous extrusion preset state normalizes to CBP slicing mode", "[ConstrainedBeadPlanner]")
{
    DynamicPrintConfig config = DynamicPrintConfig::full_print_config();
    config.set_key_value("continuous_extrusion", new ConfigOptionBool(true));
    config.set_key_value("slicing_mode", new ConfigOptionEnum<SlicingMode>(SlicingMode::Regular));

    Preset::normalize(config);

    REQUIRE(config.opt_bool("continuous_extrusion"));
    REQUIRE(config.opt_enum<SlicingMode>("slicing_mode") == SlicingMode::ConstrainedBeadPlanner);
}

TEST_CASE("CBP reverses bounded candidates only after successful planning", "[ConstrainedBeadPlanner]")
{
    ExtrusionEntityCollection collection;
    collection.append(make_path(mm_point(0.0, 0.0), mm_point(1.0, 0.0)));
    collection.append(make_path(mm_point(5.0, 0.0), mm_point(4.0, 0.0)));

    CBPSettings settings = test_settings();
    CBPPlanResult result = constrained_bead_planner_plan_collection(settings, ExPolygons{}, collection);

    REQUIRE(result.applied);
    REQUIRE(collection.entities.size() == 1);
    const auto *multipath = single_committed_multipath(collection);
    REQUIRE(multipath != nullptr);
    REQUIRE(multipath->paths.size() == 3);
    REQUIRE(multipath->paths[0].first_point() == mm_point(0.0, 0.0));
    REQUIRE(multipath->paths[0].last_point() == mm_point(1.0, 0.0));
    REQUIRE(multipath->paths[1].first_point() == mm_point(1.0, 0.0));
    REQUIRE(multipath->paths[1].last_point() == mm_point(4.0, 0.0));
    REQUIRE(multipath->paths[2].first_point() == mm_point(4.0, 0.0));
    REQUIRE(multipath->paths[2].last_point() == mm_point(5.0, 0.0));
}

TEST_CASE("CBP fails without mutating order when centerline crossing is unavoidable", "[ConstrainedBeadPlanner]")
{
    ExtrusionEntityCollection collection;
    collection.append(make_path(mm_point(0.0, 0.0), mm_point(10.0, 10.0)));
    collection.append(make_path(mm_point(0.0, 10.0), mm_point(10.0, 0.0)));

    CBPSettings settings = test_settings();
    settings.check_centerline_crossing = true;

    REQUIRE_THROWS_AS(constrained_bead_planner_plan_collection(settings, ExPolygons{}, collection), SlicingError);
    REQUIRE(collection.entities[0]->first_point() == mm_point(0.0, 0.0));
    REQUIRE(collection.entities[1]->first_point() == mm_point(0.0, 10.0));
}

TEST_CASE("CBP same-layer bead collision rejects overlapping candidates", "[ConstrainedBeadPlanner]")
{
    ExtrusionEntityCollection collection;
    collection.append(make_path(mm_point(0.0, 0.0), mm_point(10.0, 0.0)));
    collection.append(make_path(mm_point(0.0, 0.0), mm_point(10.0, 0.0)));

    CBPSettings settings = test_settings();
    settings.check_same_layer_collision = true;
    settings.max_adjacent_overlap = 0.35;

    REQUIRE_THROWS_AS(constrained_bead_planner_plan_collection(settings, ExPolygons{}, collection), SlicingError);
    REQUIRE(collection.entities[0]->first_point() == mm_point(0.0, 0.0));
    REQUIRE(collection.entities[1]->first_point() == mm_point(0.0, 0.0));
}

TEST_CASE("CBP accepts closed loop seam as adjacent geometry", "[ConstrainedBeadPlanner]")
{
    ExtrusionEntityCollection collection;
    collection.append(make_smooth_loop(10.0, 72));

    CBPSettings settings = test_settings();
    settings.check_same_layer_collision = true;

    CBPPlanResult result = constrained_bead_planner_plan_collection(settings, ExPolygons{}, collection);

    REQUIRE(result.applied);
    REQUIRE(collection.entities.size() == 1);
    REQUIRE(single_committed_multipath(collection) != nullptr);
}

TEST_CASE("CBP accepts endpoint-only continuation as one continuous multipath", "[ConstrainedBeadPlanner]")
{
    ExtrusionEntityCollection collection;
    collection.append(make_path(mm_point(0.0, 0.0), mm_point(10.0, 0.0)));
    collection.append(make_path(mm_point(10.0, 0.0), mm_point(20.0, 0.0)));

    CBPSettings settings = test_settings();
    settings.check_same_layer_collision = true;
    settings.check_centerline_crossing = true;
    settings.check_double_back = true;

    CBPPlanResult result = constrained_bead_planner_plan_collection(settings, ExPolygons{}, collection);

    REQUIRE(result.applied);
    REQUIRE(collection.entities.size() == 1);
    const auto *multipath = single_committed_multipath(collection);
    REQUIRE(multipath != nullptr);
    REQUIRE(multipath->paths.size() == 2);
    REQUIRE(multipath->paths[0].last_point() == multipath->paths[1].first_point());
}

TEST_CASE("CBP rejects connectors across the empty space between islands", "[ConstrainedBeadPlanner]")
{
    auto *island_a = new ExtrusionEntityCollection();
    island_a->append(make_path(mm_point(0.0, 0.0), mm_point(5.0, 0.0)));
    island_a->append(make_path(mm_point(5.0, 0.0), mm_point(10.0, 0.0)));

    auto *island_b = new ExtrusionEntityCollection();
    island_b->append(make_path(mm_point(100.0, 0.0), mm_point(105.0, 0.0)));
    island_b->append(make_path(mm_point(105.0, 0.0), mm_point(110.0, 0.0)));

    ExtrusionEntityCollection collection;
    collection.entities.emplace_back(island_a);
    collection.entities.emplace_back(island_b);

    CBPSettings settings = test_settings();
    settings.check_containment = true;

    ExPolygon printable_a;
    printable_a.contour.points = {
        mm_point(-1.0, -1.0),
        mm_point(11.0, -1.0),
        mm_point(11.0, 1.0),
        mm_point(-1.0, 1.0),
    };
    ExPolygon printable_b;
    printable_b.contour.points = {
        mm_point(99.0, -1.0),
        mm_point(111.0, -1.0),
        mm_point(111.0, 1.0),
        mm_point(99.0, 1.0),
    };

    REQUIRE_THROWS_AS(constrained_bead_planner_plan_collection(settings, { printable_a, printable_b }, collection), SlicingError);
    REQUIRE(collection.entities.size() == 2);
    REQUIRE(collection.entities[0] == island_a);
    REQUIRE(collection.entities[1] == island_b);
    REQUIRE(island_a->entities.size() == 2);
    REQUIRE(island_b->entities.size() == 2);
}
