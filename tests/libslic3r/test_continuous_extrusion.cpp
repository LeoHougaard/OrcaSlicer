#include <catch2/catch_all.hpp>

#include "libslic3r/ContinuousExtrusion.hpp"
#include "libslic3r/ContinuousPrint.hpp"
#include "libslic3r/Preset.hpp"
#include "libslic3r/Flow.hpp"
#include "libslic3r/Geometry.hpp"
#include "libslic3r/TriangleMesh.hpp"
#include "libslic3r/TriangleMeshSlicer.hpp"
#include "libslic3r/Arachne/BeadingStrategy/BeadingStrategyFactory.hpp"

#include <cmath>
#include <limits>
#include <numeric>

using namespace Slic3r;
using Catch::Matchers::WithinAbs;

TEST_CASE("Continuous joins preserve fill without crossing retained contours", "[ContinuousCrossing][ContinuousInfill]")
{
    const bool hole = GENERATE(false, true);
    const bool solid = GENERATE(false, true);
    ExPolygon region(Polygon{Points{Point::new_scale(0., 0.), Point::new_scale(24., 0.),
        Point::new_scale(24., 20.), Point::new_scale(0., 20.)}});
    if (hole) {
        region.holes.emplace_back(Points{Point::new_scale(9., 7.), Point::new_scale(9., 13.),
            Point::new_scale(15., 13.), Point::new_scale(15., 7.)});
    }
    ContinuousExtrusionSettings settings;
    settings.closed_route = true;
    settings.infill_density = .15;
    settings.seam_positions = {Point::new_scale(24., 20.)};
    settings.inner_seam_positions = settings.seam_positions;
    if (solid)
        settings.solid_regions = {region};
    ContinuousRegionPlanner planner({region}, settings);
    planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(20));
    const auto &plan = planner.best();
    INFO(plan.reason);
    REQUIRE(plan.connected);
    REQUIRE(plan.contained);
    REQUIRE(plan.widths_valid);
    REQUIRE(plan.unresolved_paths.empty());
    REQUIRE(plan.coverage.missing_area / plan.coverage.target_area < .06);
    Lines edges;
    for (const auto &path : plan.paths)
        for (size_t i = 1; i < path.polyline.points.size(); ++i)
            edges.emplace_back(path.polyline.points[i - 1].to_point(), path.polyline.points[i].to_point());
    size_t crossings = 0;
    for (size_t i = 0; i < edges.size(); ++i)
        for (size_t j = i + 2; j < edges.size(); ++j) {
            if (i == 0 && j + 1 == edges.size())
                continue;
            crossings += Geometry::segments_intersect(edges[i].a, edges[i].b, edges[j].a, edges[j].b);
        }
    REQUIRE(crossings == 0);
}

TEST_CASE("Legacy continuous projects remain solid and new projects retain their density", "[ContinuousIntegration][Config]")
{
    DynamicPrintConfig legacy;
    legacy.set_deserialize_strict("continuous_extrusion", "1");
    legacy.set_deserialize_strict("sparse_infill_density", "15%");
    legacy.handle_legacy_composite();
    REQUIRE(legacy.opt_serialize("sparse_infill_density") == "100%");
    legacy.set_deserialize_strict("sparse_infill_density", "40%");
    legacy.handle_legacy_composite();
    REQUIRE(legacy.opt_serialize("sparse_infill_density") == "40%");
    auto ordinary = DynamicPrintConfig::full_print_config();
    ordinary.set_deserialize_strict("sparse_infill_density", "15%");
    ordinary.handle_legacy_composite();
    REQUIRE(ordinary.opt_serialize("sparse_infill_density") == "15%");
    DynamicPrintConfig old_mode;
    old_mode.set_deserialize_strict("slicing_mode", "constrained_bead_planner");
    old_mode.handle_legacy_composite();
    REQUIRE(old_mode.opt_bool("continuous_extrusion"));
    REQUIRE(old_mode.opt_serialize("slicing_mode") == "regular");
    REQUIRE(old_mode.opt_serialize("sparse_infill_density") == "100%");
}

TEST_CASE("Continuous flow migration preserves saved feed targets", "[ContinuousIntegration][Config]")
{
    auto defaults = DynamicPrintConfig::full_print_config();
    REQUIRE(defaults.opt_enum<ContinuousFlowControl>("ce_flow_control") == ContinuousFlowControl::Automatic);
    DynamicPrintConfig legacy;
    legacy.set_deserialize_strict("ce_filament_speed", "0.37");
    legacy.handle_legacy_composite();
    REQUIRE(legacy.opt_enum<ContinuousFlowControl>("ce_flow_control") == ContinuousFlowControl::Filament);
    REQUIRE_THAT(legacy.opt_float("ce_filament_speed"), WithinAbs(.37, 1e-12));
    for (const auto *mode : {"automatic", "volumetric", "filament", "process_speeds"}) {
        legacy.set_deserialize_strict("ce_flow_control", mode);
        legacy.handle_legacy_composite();
        REQUIRE(legacy.opt_serialize("ce_flow_control") == mode);
    }
}

TEST_CASE("Continuous settings survive the process preset filter", "[ContinuousIntegration][Config]")
{
    DynamicPrintConfig source = DynamicPrintConfig::full_print_config();
    source.set_deserialize_strict("ce_filament_speed", "0.37");
    source.set_deserialize_strict("ce_min_width", "0.35");
    source.set_deserialize_strict("continuous_extrusion", "1");
    DynamicPrintConfig preset;
    preset.apply_only(source, Preset::print_options());
    for (const auto &key : source.keys())
        if (key.rfind("ce_", 0) == 0 || key == "continuous_extrusion") {
            INFO(key);
            REQUIRE(preset.has(key));
            REQUIRE(preset.opt_serialize(key) == source.opt_serialize(key));
        }
}

namespace {
ExPolygon rectangle(double x, double y, double w, double h)
{
    ExPolygon result;
    result.contour.points = { Point::new_scale(x, y), Point::new_scale(x + w, y),
                              Point::new_scale(x + w, y + h), Point::new_scale(x, y + h) };
    return result;
}

ExtrusionPath strip_path(double y, double x0 = 0., double x1 = 10.)
{
    ExtrusionPath result(erSolidInfill, .2, 1.f, .2f);
    result.polyline.points = { Point3(Point::new_scale(x0, y), 0), Point3(Point::new_scale(x1, y), 0) };
    return result;
}
}

TEST_CASE("Continuous coverage counts duplicate deposition even when the union is unchanged", "[ContinuousExtrusion]")
{
    const ExPolygons region { rectangle(0., 0., 10., 1.) };
    auto single = continuous_coverage(region, { strip_path(.5) });
    REQUIRE_THAT(single.missing_area, WithinAbs(0., 1e-5));
    REQUIRE_THAT(single.outside_area, WithinAbs(0., 1e-5));
    REQUIRE_THAT(single.excess_area, WithinAbs(0., 1e-5));
    auto duplicate = continuous_coverage(region, { strip_path(.5), strip_path(.5) });
    REQUIRE_THAT(duplicate.missing_area, WithinAbs(0., 1e-5));
    REQUIRE_THAT(duplicate.excess_area, WithinAbs(10., 1e-5));
    REQUIRE_THAT(area(duplicate.excess) * SCALING_FACTOR * SCALING_FACTOR, WithinAbs(10., 1e-5));
}

TEST_CASE("Continuous coverage is invariant to straight path subdivision", "[ContinuousExtrusion]")
{
    const ExPolygons region { rectangle(0., 0., 10., 1.) };
    auto split = continuous_coverage(region, { strip_path(.5, 0., 4.), strip_path(.5, 4., 10.) });
    REQUIRE_THAT(split.error_area(), WithinAbs(0., 1e-5));
    REQUIRE_THAT(split.deposited_volume, WithinAbs(2., 1e-6));
}

TEST_CASE("Continuous coverage preserves holes and reports deposition through them", "[ContinuousExtrusion]")
{
    ExPolygon region = rectangle(0., 0., 10., 1.);
    region.holes.push_back(rectangle(4., .1, 2., .8).contour);
    region.holes.back().make_clockwise();
    auto coverage = continuous_coverage({ region }, { strip_path(.5) });
    REQUIRE_THAT(coverage.outside_area, WithinAbs(1.6, 1e-5));
    REQUIRE_THAT(coverage.target_area, WithinAbs(8.4, 1e-5));
}

TEST_CASE("Changing bead width changes toolhead speed while nominal filament feed stays fixed", "[ContinuousExtrusion]")
{
    ContinuousExtrusionSettings settings;
    double previous_speed = std::numeric_limits<double>::infinity();
    for (double width : { .3, .42, .63, .8 }) {
        Flow flow(float(width), .2f, .4f);
        double speed = continuous_extrusion_speed(settings, flow.mm3_per_mm());
        double filament_rate = speed * flow.mm3_per_mm() / (PI * std::pow(settings.filament_diameter * .5, 2));
        REQUIRE_THAT(filament_rate, WithinAbs(settings.filament_speed, 1e-12));
        REQUIRE(speed < previous_speed);
        previous_speed = speed;
    }
    REQUIRE_THROWS(continuous_extrusion_speed(settings, 0.));
    REQUIRE_THROWS(continuous_extrusion_speed(settings, std::numeric_limits<double>::quiet_NaN()));
}

TEST_CASE("Region coverage rejects ramps instead of counting their projection as a filled layer", "[ContinuousExtrusion]")
{
    auto path = strip_path(.5);
    path.polyline.points.back().z() = scale_(.2);
    REQUIRE_THROWS(continuous_coverage({ rectangle(0., 0., 10., 1.) }, { path }));
}

TEST_CASE("An expired continuous planning budget retains the current result", "[ContinuousExtrusion]")
{
    ContinuousRegionPlanner planner({ rectangle(0., 0., 6., 4.) }, {});
    planner.advance(std::chrono::steady_clock::now());
    REQUIRE(planner.attempts() == 0);
    REQUIRE(planner.best().paths.empty());
    REQUIRE_THAT(planner.best().coverage.missing_area, WithinAbs(24., 1e-5));
    planner.advance(std::chrono::steady_clock::now() + std::chrono::milliseconds(1));
    REQUIRE(planner.attempts() > 0);
    REQUIRE_FALSE(planner.best().paths.empty());
    const size_t attempts = planner.attempts();
    const double error = planner.best().coverage.error_area();
    planner.advance(std::chrono::steady_clock::now());
    REQUIRE(planner.attempts() == attempts);
    REQUIRE_THAT(planner.best().coverage.error_area(), WithinAbs(error, 1e-9));
    planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(20));
    REQUIRE(planner.finished());
    for (size_t i = 1; i < planner.best().paths.size(); ++i)
        REQUIRE(planner.best().paths[i - 1].last_point() == planner.best().paths[i].first_point());
}

TEST_CASE("Disconnected regions are never connected across empty space", "[ContinuousExtrusion]")
{
    ContinuousRegionPlanner planner({ rectangle(0., 0., 4., 4.), rectangle(10., 0., 4., 4.) }, {});
    planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(1));
    REQUIRE_FALSE(planner.best().connected);
    REQUIRE(planner.best().paths.empty());
    REQUIRE_THAT(planner.best().coverage.missing_area, WithinAbs(32., 1e-5));
}

TEST_CASE("Continuous omission leaves disconnected material unprinted", "[ContinuousExtrusion][ContinuousIntegration]")
{
    ContinuousExtrusionSettings settings;
    settings.omit_unreachable = true;
    settings.closed_route = true;
    ContinuousRegionPlanner planner({ rectangle(0., 0., 6., 4.), rectangle(12., 0., 2., 2.) }, settings);
    planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(5));
    const auto &plan = planner.best();
    REQUIRE(plan.connected);
    REQUIRE(plan.contained);
    REQUIRE(plan.widths_valid);
    REQUIRE(plan.paths.front().first_point() == plan.paths.back().last_point());
    REQUIRE(plan.coverage.missing_area >= 4.);
    for (const auto &path : plan.paths)
        for (const auto &point : path.polyline.points)
            REQUIRE(point.x() < scale_(7.));
}

TEST_CASE("Continuous layer ramps share exact extrusion endpoints", "[ContinuousExtrusion][ContinuousIntegration]")
{
    ContinuousExtrusionSettings settings;
    settings.omit_unreachable = true;
    settings.closed_route = true;
    const ExPolygons region { rectangle(0., 0., 8., 6.) };
    ContinuousRegionPlanner planner(region, settings);
    planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(5));
    const auto &plan = planner.best();
    REQUIRE(plan.connected);
    const auto layers = continuous_join_layers({plan, plan, plan}, {region, region, region}, {.2, .4, .6}, settings, 5., 2.);
    REQUIRE(layers.size() == 3);
    Point3 previous = layers.front().paths.front().polyline.points.front();
    bool ramp_found = false;
    for (const auto &layer : layers) {
        for (const auto &path : layer.paths) {
            REQUIRE(path.polyline.points.front() == previous);
            REQUIRE(path.mm3_per_mm > 0.);
            for (const auto &point : path.polyline.points) {
                REQUIRE(point.z() >= previous.z());
                ramp_found |= point.z() > previous.z();
                previous = point;
            }
        }
    }
    REQUIRE(ramp_found);
    REQUIRE(previous.z() == scale_(.6));
    auto displaced = plan;
    for (auto &path : displaced.paths)
        for (auto &point : path.polyline.points)
            point.x() += scale_(30.);
    REQUIRE_THROWS(continuous_join_layers({plan, displaced}, {region, {rectangle(30., 0., 8., 6.)}}, {.2, .4}, settings, 5., 2.));
}

TEST_CASE("Continuous routes connect independent shapes and moving cross sections", "[ContinuousGeneralization]")
{
    const int shape = GENERATE(0, 1, 2, 3, 4);
    const double size = GENERATE(1., 1.5, 2.);
    const double angle = GENERATE(0., .37);
    INFO("Shape " << shape << ", scale " << size << ", angle " << angle);
    ContinuousExtrusionSettings settings;
    settings.omit_unreachable = true;
    settings.closed_route = true;
    std::vector<ExPolygons> regions;
    for (int i = 0; i < 12; ++i) {
        ExPolygon region;
        if (shape == 0) region = rectangle(0., 0., 8., 6.);
        if (shape == 1) region = rectangle(0., 0., 1.2, 12.);
        if (shape == 2) {
            for (int j = 0; j < 64; ++j) {
                const double angle = j * 2. * PI / 64.;
                region.contour.points.push_back(Point::new_scale(8. * std::cos(angle), 8. * std::sin(angle)));
            }
            Polygon hole;
            for (int j = 63; j >= 0; --j) {
                const double angle = j * 2. * PI / 64.;
                hole.points.push_back(Point::new_scale(5. * std::cos(angle), 5. * std::sin(angle)));
            }
            region.holes.push_back(std::move(hole));
        }
        if (shape == 3) region = rectangle(i * .6, i * .15, 4., 3.);
        if (shape == 4) region = rectangle(i * .15, i * .15, 5. - i * .3, 5. - i * .3);
        region.scale(size);
        region.rotate(angle);
        region.translate(Point::new_scale(17.3, -23.7));
        regions.push_back({std::move(region)});
    }
    std::vector<ContinuousRegionPlan> plans;
    std::vector<double> zs;
    for (size_t i = 0; i < regions.size(); ++i) {
        ContinuousRegionPlanner planner(regions[i], settings);
        planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(20));
        INFO("Layer " << i << ": " << planner.best().reason);
        REQUIRE(planner.finished());
        REQUIRE(planner.best().connected);
        REQUIRE(planner.best().contained);
        REQUIRE(planner.best().widths_valid);
        plans.push_back(planner.best());
        zs.push_back((i + 1) * .2);
    }
    std::vector<ContinuousLayerRoute> routes;
    REQUIRE_NOTHROW(routes = continuous_join_layers(plans, regions, zs, settings, 5., 2.));
    REQUIRE(routes.size() == regions.size());
    // Layer attachment must preserve the selected fill. Its connection/ramp
    // volume is accounted separately, not used to disguise planar gaps.
    for (size_t i = 0; i < routes.size(); ++i) {
        REQUIRE(routes[i].coverage.missing_area == plans[i].coverage.missing_area);
        REQUIRE(routes[i].coverage.excess_area == plans[i].coverage.excess_area);
        REQUIRE(routes[i].coverage.outside_area == plans[i].coverage.outside_area);
        REQUIRE(routes[i].coverage.target_area == plans[i].coverage.target_area);
    }
    Point3 previous = routes.front().paths.front().polyline.points.front();
    for (const auto &route : routes)
        for (const auto &path : route.paths) {
            REQUIRE(path.polyline.points.front() == previous);
            for (const auto &point : path.polyline.points) {
                REQUIRE(point.z() >= previous.z());
                previous = point;
            }
        }
    REQUIRE(previous.z() == scaled<coord_t>(zs.back()));
}

TEST_CASE("Continuous sparse infill retains walls and decreases deposited volume", "[ContinuousInfill]")
{
    double previous_volume = 0.;
    for (double density : {0., .15, .4, 1.}) {
        ContinuousExtrusionSettings settings;
        settings.infill_density = density;
        settings.closed_route = true;
        settings.omit_unreachable = true;
        ContinuousRegionPlanner planner({rectangle(0., 0., 20., 16.)}, settings);
        planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(10));
        const auto &plan = planner.best();
        INFO(density << ": " << plan.reason);
        REQUIRE(plan.connected);
        REQUIRE(plan.contained);
        REQUIRE(plan.widths_valid);
        REQUIRE(plan.paths.front().first_point() == plan.paths.back().last_point());
        REQUIRE(plan.coverage.deposited_volume > previous_volume);
        previous_volume = plan.coverage.deposited_volume;
        REQUIRE(plan.coverage.missing_area / plan.coverage.target_area < .06);
        REQUIRE((plan.coverage.intentional_void_area > 1.) == (density < 1.));
        REQUIRE(std::any_of(plan.paths.begin(), plan.paths.end(), [](const auto &p) { return p.role() == erExternalPerimeter; }));
        for (size_t i = 1; i < plan.paths.size(); ++i)
            REQUIRE(plan.paths[i - 1].last_point() == plan.paths[i].first_point());
    }
}

TEST_CASE("Continuous sparse infill fills required solid surfaces", "[ContinuousInfill]")
{
    ContinuousExtrusionSettings settings;
    settings.infill_density = .15;
    settings.closed_route = true;
    settings.omit_unreachable = true;
    const ExPolygons region{rectangle(0., 0., 12., 10.)};
    settings.solid_regions = region;
    ContinuousRegionPlanner planner(region, settings);
    planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(10));
    const auto &plan = planner.best();
    REQUIRE(plan.connected);
    REQUIRE(plan.contained);
    REQUIRE_THAT(plan.coverage.intentional_void_area, WithinAbs(0., 1e-9));
    REQUIRE(plan.coverage.missing_area / plan.coverage.target_area < .04);
    REQUIRE(std::none_of(plan.paths.begin(), plan.paths.end(), [](const auto &p) { return p.role() == erInternalInfill; }));
}

TEST_CASE("Continuous sparse paths respect holes and changing layer density", "[ContinuousInfill]")
{
    ExPolygon ring = rectangle(0., 0., 24., 20.);
    ring.holes.push_back(rectangle(9., 7., 6., 6.).contour);
    ring.holes.back().make_clockwise();
    ContinuousExtrusionSettings settings;
    settings.closed_route = true;
    settings.omit_unreachable = true;
    settings.infill_density = .15;
    std::vector<ContinuousRegionPlan> plans;
    for (bool solid : {true, false, false, true}) {
        settings.solid_regions = solid ? ExPolygons{ring} : ExPolygons{};
        ContinuousRegionPlanner planner({ring}, settings);
        planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(10));
        const auto &plan = planner.best();
        INFO(plan.reason);
        REQUIRE(plan.connected);
        REQUIRE(plan.contained);
        REQUIRE(plan.widths_valid);
        REQUIRE(plan.coverage.outside_area < 1.);
        plans.push_back(plan);
    }
    const auto routes = continuous_join_layers(plans, std::vector<ExPolygons>(4, {ring}), {.2, .4, .6, .8}, settings, 10., 2.);
    for (size_t layer = 1; layer < routes.size(); ++layer)
        REQUIRE(routes[layer - 1].paths.back().last_point3() == routes[layer].paths.front().first_point3());
}

TEST_CASE("Continuous wall attachments follow the requested seam side", "[ContinuousSeam]")
{
    for (double y : {0., 12.}) {
        ContinuousExtrusionSettings settings;
        settings.closed_route = true;
        settings.omit_unreachable = true;
        settings.seam_positions = {Point::new_scale(10., y)};
        ContinuousRegionPlanner planner({rectangle(0., 0., 20., 12.)}, settings);
        planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(10));
        const auto &plan = planner.best();
        REQUIRE(plan.connected);
        REQUIRE(plan.contained);
        size_t attachments = 0;
        for (size_t i = 0; i < plan.paths.size(); ++i) {
            const auto &p = plan.paths[i];
            const auto &next = plan.paths[(i + 1) % plan.paths.size()];
            if (p.role() == erExternalPerimeter && next.role() != erExternalPerimeter) {
                ++attachments;
                REQUIRE(std::abs(unscale<double>(p.last_point().y()) - y) < 1.);
            }
        }
        REQUIRE(attachments == 1);
    }
}

TEST_CASE("Continuous inner attachments can move independently of the outer seam", "[ContinuousSeam]")
{
    ContinuousExtrusionSettings settings;
    settings.closed_route = true;
    settings.omit_unreachable = true;
    settings.seam_positions = {Point::new_scale(10., 0.)};
    settings.inner_seam_positions = {Point::new_scale(10., 12.)};
    ContinuousRegionPlanner planner({rectangle(0., 0., 20., 12.)}, settings);
    planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(10));
    const auto &plan = planner.best();
    REQUIRE(plan.connected);
    REQUIRE(plan.contained);
    bool inner_at_back = false;
    for (size_t i = 0; i < plan.paths.size(); ++i) {
        const auto &path = plan.paths[i];
        const auto &next = plan.paths[(i + 1) % plan.paths.size()];
        if (path.role() == erExternalPerimeter && next.role() != erExternalPerimeter)
            REQUIRE(unscale<double>(path.last_point().y()) < 1.);
        if (path.role() == erPerimeter && next.role() != erPerimeter)
            inner_at_back |= unscale<double>(path.last_point().y()) > 10.;
    }
    REQUIRE(inner_at_back);
}

TEST_CASE("Layer 111 omits the narrow neck instead of exceeding bead bounds", "[ContinuousExtrusion][ContinuousIntegration]")
{
    TriangleMesh mesh;
    REQUIRE(mesh.ReadSTLFile((std::string(TEST_DATA_DIR) + "/continuous_extrusion/hardest_part_metres.stl").c_str()));
    mesh.scale(1000.f);
    const auto regions = slice_mesh_ex(mesh.its, {22.1f});
    ContinuousExtrusionSettings settings;
    settings.omit_unreachable = true;
    settings.closed_route = true;
    ContinuousRegionPlanner planner(regions.front(), settings);
    planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(20));
    const auto &plan = planner.best();
    INFO(plan.reason);
    REQUIRE(plan.connected);
    REQUIRE(plan.contained);
    REQUIRE(plan.widths_valid);
    REQUIRE(plan.paths.front().first_point() == plan.paths.back().last_point());
    REQUIRE(plan.coverage.missing_area > 0.);
    REQUIRE(plan.coverage.missing_area / plan.coverage.target_area < .04);
}

TEST_CASE("Continuous beading redistributes odd strokes without losing material", "[ContinuousExtrusion]")
{
    auto strategy = Arachne::BeadingStrategyFactory::makeStrategy(
        scale_(.42), scale_(.42), scale_(.4), .5f, false, 0, 0, .5, .5, 100, 0, 2, .5, true);
    auto ordinary = Arachne::BeadingStrategyFactory::makeStrategy(
        scale_(.42), scale_(.42), scale_(.4), .5f, false, 0, 0, .5, .5, 100, 0, 2);
    for (coord_t count : { 3, 5, 7 }) {
        const coord_t thickness = scale_(.42) * count;
        auto paired = strategy->compute(thickness, count);
        auto normal = ordinary->compute(thickness, count);
        REQUIRE(paired.bead_widths[size_t(count / 2)] == 0);
        REQUIRE(normal.bead_widths[size_t(count / 2)] > 0);
        REQUIRE(std::accumulate(paired.bead_widths.begin(), paired.bead_widths.end(), coord_t(0)) == thickness);
        REQUIRE(paired.left_over == 0);
    }
}

TEST_CASE("Hardest part preserves branches and reports the narrow opening", "[ContinuousExtrusion][ContinuousAcceptance]")
{
    TriangleMesh mesh;
    REQUIRE(mesh.ReadSTLFile((std::string(TEST_DATA_DIR) + "/continuous_extrusion/hardest_part_metres.stl").c_str()));
    mesh.scale(1000.f);
    REQUIRE_THAT(mesh.bounding_box().size().z(), WithinAbs(40., .001));
    const auto layers = slice_mesh_ex(mesh.its, { .1f, 20.1f, 22.1f, 39.9f });
    REQUIRE(layers.size() == 4);
    REQUIRE(layers[1].size() == 1);
    REQUIRE(layers[1][0].holes.size() == 1);
    for (size_t i = 0; i < layers.size(); ++i) {
        DYNAMIC_SECTION("Cross-section " << i) {
            ContinuousRegionPlanner planner(layers[i], {});
            planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(15));
            const auto &plan = planner.best();
            INFO("Candidates: " << planner.attempts() << "; " << plan.reason);
            REQUIRE(plan.connected);
            REQUIRE(plan.widths_valid);
            if (i == 2) {
                // The horizontal neck at the hole opening is about 0.13 mm.
                // A 0.30 mm bead cannot fit within the 0.05 mm tolerance here.
                // Connectivity must not disguise this unresolved geometry.
                REQUIRE_FALSE(plan.contained);
            } else {
                REQUIRE(plan.contained);
            }
            // Regression guard against treating a mixed real/zero-width inset
            // as an empty contour. This is not the final print-quality gate.
            REQUIRE(plan.coverage.missing_area / plan.coverage.target_area < .03);
            REQUIRE(plan.coverage.excess_area / plan.coverage.target_area < .03);
            REQUIRE_FALSE(plan.paths.empty());
            for (size_t j = 1; j < plan.paths.size(); ++j)
                REQUIRE(plan.paths[j - 1].last_point() == plan.paths[j].first_point());
        }
    }
}

TEST_CASE("Sparse layer attachments can cross a solid shoulder", "[ContinuousInfill][ContinuousGeneralization]")
{
    ContinuousExtrusionSettings settings;
    settings.closed_route = true;
    settings.omit_unreachable = true;
    settings.infill_density = .15;
    const std::vector<ExPolygons> regions = {
        {rectangle(0., 0., 20., 20.)}, {rectangle(4., 4., 12., 12.)}, {rectangle(4., 4., 12., 12.)}};
    std::vector<ContinuousRegionPlan> plans;
    for (size_t layer = 0; layer < regions.size(); ++layer) {
        settings.solid_regions = layer == 0 ? regions[layer] : ExPolygons{};
        ContinuousRegionPlanner planner(regions[layer], settings);
        planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(10));
        REQUIRE(planner.best().connected);
        REQUIRE(planner.best().contained);
        plans.push_back(planner.best());
    }
    const auto routes = continuous_join_layers(plans, regions, {.2, .4, .6}, settings, 5., 2.);
    REQUIRE(routes.size() == regions.size());
    for (size_t i = 1; i < routes.size(); ++i)
        REQUIRE(routes[i - 1].paths.back().last_point() == routes[i].paths.front().first_point());
}

TEST_CASE("Moving walls rise during the incoming connection", "[ContinuousCrossing][ContinuousIntegration]")
{
    ContinuousExtrusionSettings settings;
    settings.closed_route = true;
    settings.infill_density = .15;
    const std::vector<ExPolygons> regions{{rectangle(0., 0., 12., 10.)}, {rectangle(.3, 0., 12., 10.)}};
    std::vector<ContinuousRegionPlan> plans;
    for (const auto &region : regions) {
        ContinuousRegionPlanner planner(region, settings);
        planner.advance(std::chrono::steady_clock::now() + std::chrono::seconds(10));
        REQUIRE(planner.best().connected);
        REQUIRE(planner.best().unresolved_paths.empty());
        plans.push_back(planner.best());
    }
    const auto layers = continuous_join_layers(plans, regions, {.2, .4}, settings, 10., 2.);
    const auto &entry = layers[1].paths.front();
    REQUIRE(entry.first_point3().z() == scaled<coord_t>(.2));
    REQUIRE(entry.last_point3().z() == scaled<coord_t>(.4));
    REQUIRE(entry.polyline.points[1].z() > entry.polyline.points[0].z());
    // The ramp follows the moving outline at its interpolated height, even
    // where a 2D intersection of the two sections would reject the connection.
    for (const auto &point : entry.polyline.points) {
        const double t = (unscale<double>(point.z()) - .2) / .2;
        REQUIRE(unscale<double>(point.x()) - entry.width * .5 >= .3 * t - settings.boundary_tolerance);
        REQUIRE(unscale<double>(point.x()) + entry.width * .5 <= 12. + .3 * t + settings.boundary_tolerance);
    }
}
