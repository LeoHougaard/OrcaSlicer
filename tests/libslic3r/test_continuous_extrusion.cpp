#include <catch2/catch_all.hpp>

#include "libslic3r/ContinuousExtrusion.hpp"
#include "libslic3r/ContinuousPrint.hpp"
#include "libslic3r/Preset.hpp"
#include "libslic3r/Flow.hpp"
#include "libslic3r/TriangleMesh.hpp"
#include "libslic3r/TriangleMeshSlicer.hpp"
#include "libslic3r/Arachne/BeadingStrategy/BeadingStrategyFactory.hpp"

#include <cmath>
#include <limits>
#include <numeric>

using namespace Slic3r;
using Catch::Matchers::WithinAbs;

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
    const auto routes = continuous_join_layers(plans, regions, zs, settings, 5., 2.);
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
