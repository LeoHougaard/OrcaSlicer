#include <catch2/catch_all.hpp>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <sstream>

#include "test_data.hpp"
#include "test_utils.hpp"
#include "libslic3r/ContinuousPrint.hpp"
#include "libslic3r/Flow.hpp"
#include "libslic3r/GCodeReader.hpp"

using namespace Slic3r;
using Catch::Matchers::WithinAbs;

namespace {
struct ContinuousTestResources {
    std::string previous = resources_dir();
    ContinuousTestResources()
    {
        set_resources_dir((std::filesystem::path(TEST_DATA_DIR).parent_path().parent_path() / "resources").string());
    }
    ~ContinuousTestResources() { set_resources_dir(previous); }
};

DynamicPrintConfig continuous_config(double density)
{
    auto config = DynamicPrintConfig::full_print_config();
    // Recreate enum-vector options through their dynamic definitions before
    // deserializing printer overrides or serializing the G-code configuration.
    for (const auto &key : config.keys())
        if (config.option(key)->type() == coEnums)
            config.set_key_value(key, config.def()->get(key)->create_default_option());
    for (const auto &[key, value] : std::vector<std::pair<std::string, std::string>>{
        {"printer_settings_id", "Continuous test printer"}, {"print_settings_id", "Continuous test process"},
        {"filament_settings_id", "Continuous test filament"},
        {"filament_colour", "#4FB6D4"}, {"filament_type", "TPU"},
        {"filament_map", "1"}, {"physical_extruder_map", "0"},
        {"nozzle_type", "undefine"}, {"nozzle_volume", "0"},
        {"continuous_extrusion", "1"}, {"gcode_flavor", "klipper"}, {"ce_search_time", "30"},
        {"ce_omit_unreachable", "1"}, {"layer_height", "0.2"}, {"initial_layer_print_height", "0.2"},
        {"wall_loops", "2"}, {"top_shell_layers", "3"}, {"bottom_shell_layers", "3"},
        {"top_shell_thickness", "0"}, {"bottom_shell_thickness", "0"}, {"seam_position", "back"},
        {"enable_support", "0"}, {"enable_prime_tower", "0"}, {"skirt_loops", "0"}, {"brim_type", "no_brim"},
        {"machine_start_gcode", "G90"}, {"machine_end_gcode", ""}, {"use_relative_e_distances", "1"}})
        config.set_deserialize_strict(key, value);
    config.set_num_filaments(1);
    // Empty serialized text means zero strings; a printer has one empty script
    // per filament. The ordinary exporter indexes this array during startup.
    for (const auto *key : {"filament_start_gcode", "filament_end_gcode"})
        config.set_key_value(key, new ConfigOptionStrings(std::vector<std::string>{""}));
    config.set_deserialize_strict("sparse_infill_density", std::to_string(density) + "%");
    return config;
}
}

TEST_CASE_METHOD(ContinuousTestResources, "Continuous native export fills shells without object travel", "[ContinuousExtrusion]")
{
    const double density = GENERATE(0., 15., 40., 100.);
    auto config = continuous_config(density);
    Print print;
    print.is_BBL_printer() = false;
    Model model;
    Test::init_print({make_cube(24., 20., 4.)}, print, model, config);
    REQUIRE(print.validate().string.empty());
    INFO("Export native print");
    const auto gcode = Test::gcode(print);
    const auto &job = *print.objects().front()->continuous_job;
    REQUIRE(job.complete);
    REQUIRE(job.layers.size() == 20);
    for (size_t layer = 0; layer < job.layers.size(); ++layer) {
        const auto &coverage = job.layers[layer].coverage;
        if (layer < 3 || layer >= 17 || density == 100.)
            REQUIRE_THAT(coverage.intentional_void_area, WithinAbs(0., 1e-5));
        else
            REQUIRE(coverage.intentional_void_area > 0.);
    }
    INFO("Parse exported object moves");
    GCodeReader reader;
    reader.apply_config(config);
    bool active = false;
    size_t starts = 0, ends = 0, moves = 0;
    reader.parse_buffer(gcode, [&](GCodeReader &state, const GCodeReader::GCodeLine &line) {
        if (line.comment().find("CONTINUOUS_OBJECT_BEGIN") != std::string_view::npos) {
            active = true;
            ++starts;
        }
        if (line.comment().find("CONTINUOUS_OBJECT_END") != std::string_view::npos) {
            active = false;
            ++ends;
        }
        if (active && (line.cmd_is("G0") || line.cmd_is("G1")) &&
            (line.has(X) || line.has(Y) || line.has(Z) || line.has(E))) {
            REQUIRE(line.cmd_is("G1"));
            REQUIRE(line.has(E));
            REQUIRE(line.e() > 0.);
            const double distance = std::hypot(line.dist_XY(state), line.dist_Z(state));
            REQUIRE(distance > 0.);
            ++moves;
        }
    });
    REQUIRE(starts == 1);
    REQUIRE(ends == 1);
    REQUIRE(moves > 0);
}

TEST_CASE_METHOD(ContinuousTestResources, "Continuous reslicing keeps flow tuning cheap and updates density", "[ContinuousExtrusion]")
{
    auto config = continuous_config(15.);
    Print print;
    print.is_BBL_printer() = false;
    Model model;
    Test::init_print({make_cube(16., 12., 4.)}, print, model, config);
    print.process();
    const auto cached = print.objects().front()->continuous_job;
    REQUIRE(cached->complete);
    const double elapsed = cached->elapsed;
    config.set_deserialize_strict("ce_flow_control", "volumetric");
    config.set_deserialize_strict("ce_volumetric_flow", "5");
    print.apply(model, config);
    print.process();
    REQUIRE(print.objects().front()->continuous_job == cached);
    REQUIRE_THAT(cached->elapsed, WithinAbs(elapsed, 1e-9));
    config.set_deserialize_strict("sparse_infill_density", "100%");
    print.apply(model, config);
    print.process();
    const auto updated = print.objects().front()->continuous_job;
    REQUIRE(updated != cached);
    REQUIRE(updated->complete);
    for (const auto &layer : updated->layers)
        REQUIRE_THAT(layer.coverage.intentional_void_area, WithinAbs(0., 1e-5));
    config.set_deserialize_strict("continuous_extrusion", "0");
    config.set_deserialize_strict("sparse_infill_density", "15%");
    print.apply(model, config);
    const auto ordinary = Test::gcode(print);
    REQUIRE(ordinary.find("CONTINUOUS_OBJECT_BEGIN") == std::string::npos);
    REQUIRE_FALSE(print.objects().front()->continuous_job);
    REQUIRE_THAT(print.objects().front()->printing_region(0).config().sparse_infill_density.value,
                 WithinAbs(15., 1e-9));
}

TEST_CASE_METHOD(ContinuousTestResources, "Continuous export preserves short moves and their extrusion volume", "[ContinuousExtrusion]")
{
    auto config = continuous_config(100.);
    config.set_deserialize_strict("filament_flow_ratio", "1");
    config.set_deserialize_strict("print_flow_ratio", "1");
    config.set_deserialize_strict("set_other_flow_ratios", "0");
    Print print;
    print.is_BBL_printer() = false;
    Model model;
    Test::init_print({make_cube(6., 4., .2)}, print, model, config);
    print.process();
    const auto cached = print.objects().front()->continuous_job;
    REQUIRE(cached);
    REQUIRE(cached->complete);
    REQUIRE(cached->layers.size() == 1);

    auto &route = cached->layers.front();
    REQUIRE_FALSE(route.paths.empty());
    const Point3 start(Point::new_scale(1.123457, 1.234569), route.paths.front().first_point3().z());
    const coord_t tiny = scaled<coord_t>(.00005), side = scaled<coord_t>(1.);
    const Points3 points{start, Point3(start.x() + coord_t(1), start.y(), start.z()), Point3(start.x() + tiny, start.y(), start.z()),
        Point3(start.x() + side, start.y(), start.z()), Point3(start.x() + side, start.y() + side, start.z()),
        Point3(start.x(), start.y() + side, start.z()), start};
    const Flow flow(.3f, .2f, .4f);
    ExtrusionPath path(erSolidInfill, flow.mm3_per_mm(), flow.width(), flow.height());
    path.polyline.points = points;
    route.paths = {path};
    const double planned_volume = path.length() * SCALING_FACTOR * path.mm3_per_mm;

    // Export the completed cache directly so planning cannot replace this route.
    ScopedTemporaryFile temporary(".gcode");
    print.export_gcode(temporary.string(), nullptr, nullptr);
    REQUIRE(print.objects().front()->continuous_job == cached);
    std::ifstream file(temporary.string());
    REQUIRE(file.good());

    // The normal GCodeReader stores float coordinates, which lose precision
    // on this short move at ordinary bed positions. Read serialized doubles here.
    Vec3d position = Vec3d::Zero();
    std::vector<Vec3d> deltas;
    std::vector<double> volumes;
    const double filament_area = PI * std::pow(print.config().filament_diameter.get_at(0) * .5, 2);
    bool active = false;
    size_t starts = 0, ends = 0;
    double total_volume = 0.;
    std::string line;
    while (std::getline(file, line)) {
        if (line == "; CONTINUOUS_OBJECT_BEGIN") {
            active = true;
            ++starts;
        } else if (line == "; CONTINUOUS_OBJECT_END") {
            active = false;
            ++ends;
        }
        std::istringstream words(line);
        std::string command;
        words >> command;
        if (command != "G0" && command != "G1")
            continue;
        Vec3d destination = position;
        bool has_motion = false, has_extrusion = false;
        double extrusion = 0.;
        std::string word;
        while (words >> word && word.front() != ';') {
            const auto axis = std::string("XYZ").find(word.front());
            if (axis != std::string::npos) {
                destination[axis] = std::stod(word.substr(1));
                has_motion = true;
            } else if (word.front() == 'E') {
                extrusion = std::stod(word.substr(1));
                has_extrusion = true;
            }
        }
        if (active && (has_motion || has_extrusion)) {
            REQUIRE(command == "G1");
            REQUIRE(has_extrusion);
            REQUIRE(extrusion > 0.);
            REQUIRE((destination - position).norm() > 0.);
            deltas.push_back(destination - position);
            volumes.push_back(extrusion * filament_area);
            total_volume += volumes.back();
        }
        position = destination;
    }
    REQUIRE(starts == 1);
    REQUIRE(ends == 1);
    REQUIRE(deltas.size() == points.size() - 1);
    for (size_t i = 0; i < deltas.size(); ++i) {
        const Vec3d expected = (points[i + 1] - points[i]).cast<double>() * SCALING_FACTOR;
        REQUIRE_THAT((deltas[i] - expected).norm(), WithinAbs(0., 1e-9));
        REQUIRE_THAT(volumes[i], WithinAbs(expected.norm() * path.mm3_per_mm, 1e-10));
    }
    REQUIRE_THAT(total_volume, WithinAbs(planned_volume, 1e-9));
}

TEST_CASE_METHOD(ContinuousTestResources, "Canceled continuous initialization retries every layer", "[ContinuousExtrusion]")
{
    auto config = continuous_config(15.);
    Print print;
    print.is_BBL_printer() = false;
    Model model;
    Test::init_print({make_cube(12., 10., 1.)}, print, model, config);
    print.process();
    auto &object = *print.get_object(size_t(0));
    REQUIRE(object.continuous_job->complete);
    object.continuous_job.reset();

    REQUIRE_THROWS_AS(apply_continuous_print(print, [] { throw CanceledException(); }), CanceledException);
    REQUIRE_FALSE(object.continuous_job);
    REQUIRE_NOTHROW(apply_continuous_print(print, [] {}));
    REQUIRE(object.continuous_job->complete);
    REQUIRE(object.continuous_job->layers.size() == object.layers().size());
    REQUIRE(object.continuous_job->layers.size() == 5);
}

TEST_CASE_METHOD(ContinuousTestResources, "Continuous validation reports commands on the active plate", "[ContinuousExtrusion]")
{
    const auto type = GENERATE(CustomGCode::PausePrint, CustomGCode::ColorChange, CustomGCode::Custom);
    auto config = continuous_config(15.);
    Print print;
    print.is_BBL_printer() = false;
    Model model;
    Test::init_print({make_cube(12., 10., 1.)}, print, model, config);
    const CustomGCode::Item command{.6, type, 1, "", "M400"};
    model.plates_custom_gcodes[model.curr_plate_index + 1].gcodes.push_back(command);
    print.apply(model, config);
    REQUIRE(continuous_print_validation(print).empty());
    model.plates_custom_gcodes[model.curr_plate_index].gcodes.push_back(command);
    print.apply(model, config);
    REQUIRE_FALSE(continuous_print_validation(print).empty());
    config.set_deserialize_strict("continuous_extrusion", "0");
    print.apply(model, config);
    REQUIRE(continuous_print_validation(print).empty());
}
