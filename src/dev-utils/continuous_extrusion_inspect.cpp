// Standalone executables provide NanoSVG, which the GUI normally supplies.
#define NANOSVG_IMPLEMENTATION
#include "nanosvg/nanosvg.h"

#include "libslic3r/ContinuousExtrusion.hpp"
#include "libslic3r/TriangleMesh.hpp"
#include "libslic3r/TriangleMeshSlicer.hpp"
#include "libslic3r/GCode/GCodeProcessor.hpp"
#include "libslic3r/Utils.hpp"
#include "libslic3r/ContinuousPrint.hpp"
#include "libslic3r/Print.hpp"
#include "libslic3r/Model.hpp"
#include "libslic3r/Preset.hpp"
#include "libslic3r/Format/3mf.hpp"
#include "libslic3r/Format/bbs_3mf.hpp"


#include <nlohmann/json.hpp>
#include <tbb/parallel_for.h>
#include <tbb/task_arena.h>
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <filesystem>

using namespace Slic3r;
using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;

static Json preview_config()
{
    Json result = Json::object();
    const auto &config = FullPrintConfig::defaults();
    for (const auto &key : config.keys())
        result[key] = config.option(key)->serialize();
    return result;
}

static Json polygons_json(const ExPolygons &polygons)
{
    Json result = Json::array();
    for (const auto &polygon : polygons) {
        Json rings = Json::array();
        auto ring = [&](const Slic3r::Polygon &p) {
            Json points = Json::array();
            for (const auto &v : p.points)
                points.push_back({ unscale<double>(v.x()), unscale<double>(v.y()) });
            rings.push_back(std::move(points));
        };
        ring(polygon.contour);
        for (const auto &hole : polygon.holes)
            ring(hole);
        result.push_back(std::move(rings));
    }
    return result;
}

static Json paths_json(const ExtrusionPaths &paths)
{
    Json result = Json::array();
    for (const auto &path : paths) {
        Json points = Json::array();
        for (const auto &p : path.polyline.points)
            points.push_back({ unscale<double>(p.x()), unscale<double>(p.y()) });
        result.push_back({ { "points", std::move(points) }, { "width", path.width },
                          { "mm3_per_mm", path.mm3_per_mm }, { "role", ExtrusionEntity::role_to_string(path.role()) } });
    }
    return result;
}

static void write_preview(const std::string &path, const std::vector<float> &zs,
                          const std::vector<ExPolygons> &regions,
                          const std::vector<ContinuousRegionPlanner> &planners, double elapsed,
                          const ContinuousExtrusionSettings &settings)
{
    Json data = { { "elapsed", elapsed }, { "layers", Json::array() },
        { "gcode_config", preview_config() },
        { "settings", { { "layer_height", settings.layer_height }, { "filament_diameter", settings.filament_diameter },
                        { "filament_speed", settings.filament_speed }, { "nominal_width", settings.nominal_width },
                        { "min_width", settings.min_width }, { "max_width", settings.max_width },
                        { "nozzle_diameter", settings.nozzle_diameter }, { "boundary_tolerance", settings.boundary_tolerance },
                        { "resolution", settings.resolution } } } };
    for (size_t i = 0; i < planners.size(); ++i) {
        const auto &p = planners[i].best();
        data["layers"].push_back({ { "z", zs[i] }, { "region", polygons_json(regions[i]) },
            { "paths", paths_json(p.paths) }, { "unresolved", paths_json(p.unresolved_paths) },
            { "missing", polygons_json(p.coverage.missing) }, { "outside", polygons_json(p.coverage.outside) },
            { "excess", polygons_json(p.coverage.excess) },
            { "area", p.coverage.target_area }, { "missing_area", p.coverage.missing_area },
            { "outside_area", p.coverage.outside_area }, { "excess_area", p.coverage.excess_area },
            { "connected", p.connected }, { "widths_valid", p.widths_valid }, { "contained", p.contained },
            { "attempts", planners[i].attempts() }, { "search_finished", planners[i].finished() }, { "reason", p.reason } });
    }
    std::ofstream json_file(path + ".json");
    json_file.exceptions(std::ios::badbit | std::ios::failbit);
    json_file << data.dump();
    std::ofstream html(path);
    html.exceptions(std::ios::badbit | std::ios::failbit);
    html << R"HTML(<!doctype html><meta charset="utf-8"><title>Continuous extrusion preview</title>
<style>
body{font:16px system-ui;margin:24px;color:#dae4ed;background:#151b23}h1{font-size:24px;margin-bottom:8px}
main{display:grid;grid-template-columns:minmax(400px,1fr) 300px;gap:24px}canvas{width:100%;height:72vh;background:#202a36;border-radius:8px}
label{display:block;margin:18px 0}input[type=range]{width:100%}p{line-height:1.5}small{color:#aeb9c8}#stats{white-space:pre-line;line-height:1.8}
@media(max-width:800px){main{display:block}canvas{height:55vh}}
</style><h1>Continuous extrusion preview</h1>
<p>Region planning experiment. Layer transitions and physical motion are not verified. This is not printable G-code.</p>
<main><canvas id="view"></canvas><aside>
<label>Layer <span id="layerLabel"></span><input id="layer" type="range" min="0" value="0"></label>
<label>Path progress <input id="progress" type="range" min="0" max="100" value="100"></label>
<button id="reset">Reset view</button><small> Scroll to zoom; drag to pan.</small>
<label><input id="gaps" type="checkbox" checked> Show missing material in red</label>
<label><input id="unresolved" type="checkbox" checked> Show unresolved paths in amber</label>
<label><input id="outside" type="checkbox" checked> Show outside material in magenta</label>
<label><input id="excess" type="checkbox"> Show repeated deposition in violet</label>
<div id="stats"></div><p id="reason"></p><small>Blue: connected route. Gray: target part. Error areas use a uniform-height deposition approximation. Refresh this page after continuing the planner.</small>
</aside></main><script>const data=)HTML" << data.dump() << R"HTML(;
const $=id=>document.getElementById(id),c=$('view'),ctx=c.getContext('2d');let zoom=1,pan=[0,0],drag=null;
$('layer').max=data.layers.length-1;
function draw(){
 const d=data.layers[+$('layer').value];if(!d)return;
 const r=c.getBoundingClientRect(),dpi=devicePixelRatio||1;c.width=r.width*dpi;c.height=r.height*dpi;
 const points=d.region.flat(2);if(!points.length)return;
 const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
 const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
 const s=zoom*Math.min((c.width-50*dpi)/(x1-x0),(c.height-50*dpi)/(y1-y0));
 const xy=p=>[(p[0]-(x0+x1)/2)*s+c.width/2+pan[0],c.height/2-(p[1]-(y0+y1)/2)*s+pan[1]];
 function fill(polys,color){ctx.fillStyle=color;for(const poly of polys){ctx.beginPath();for(const ring of poly){ring.forEach((p,i)=>{const q=xy(p);i?ctx.lineTo(...q):ctx.moveTo(...q)});ctx.closePath()}ctx.fill('evenodd')}}
 function paths(paths,color,fraction=1){ctx.strokeStyle=color;ctx.lineJoin='round';ctx.lineCap='round';let remain=Math.floor(paths.reduce((n,p)=>n+p.points.length-1,0)*fraction);for(const path of paths){if(remain<=0)break;ctx.lineWidth=Math.max(.5*dpi,path.width*s);ctx.beginPath();ctx.moveTo(...xy(path.points[0]));for(let j=1;j<path.points.length&&remain>0;j++,remain--)ctx.lineTo(...xy(path.points[j]));ctx.stroke()}}
 fill(d.region,'#55616f');paths(d.paths,'#4fb6d4',+$('progress').value/100);
 if($('unresolved').checked)paths(d.unresolved,'#f1b552');
 if($('gaps').checked)fill(d.missing,'#eb625fcc');if($('outside').checked)fill(d.outside,'#ea64ce');
 if($('excess').checked)fill(d.excess,'#bd88fb');
 $('layerLabel').textContent=`${+$('layer').value+1}/${data.layers.length}, Z ${d.z.toFixed(2)} mm`;
 const pct=d.area?100*(1-d.missing_area/d.area):0;
 $('stats').textContent=`Covered: ${pct.toFixed(2)}%\nMissing: ${d.missing_area.toFixed(3)} mm²\nOutside: ${d.outside_area.toFixed(3)} mm²\nRepeated deposition: ${d.excess_area.toFixed(3)} mm²\nCandidates tried: ${d.attempts}\nConnected: ${d.connected?'yes':'no'}\nWidths within limits: ${d.widths_valid?'yes':'no'}\nBoundary within tolerance: ${d.contained?'yes':'no'}\nTotal search: ${data.elapsed.toFixed(1)} s`;
 $('reason').textContent=d.reason+(d.search_finished?' All candidate widths have been tested.':'');
}
document.querySelectorAll('input').forEach(e=>e.addEventListener('input',draw));window.addEventListener('resize',draw);
$('reset').onclick=()=>{zoom=1;pan=[0,0];draw()};
c.addEventListener('wheel',e=>{e.preventDefault();const r=c.getBoundingClientRect(),dpi=devicePixelRatio||1,next=Math.max(1,Math.min(30,zoom*Math.exp(-e.deltaY*.002))),ratio=next/zoom,m=[(e.clientX-r.left)*dpi,(e.clientY-r.top)*dpi];pan=pan.map((p,i)=>(p+(i?c.height:c.width)/2-m[i])*ratio-(i?c.height:c.width)/2+m[i]);zoom=next;draw()},{passive:false});
c.onpointerdown=e=>{drag=[e.clientX,e.clientY];c.setPointerCapture(e.pointerId)};
c.onpointermove=e=>{if(!drag)return;const dpi=devicePixelRatio||1;pan[0]+=(e.clientX-drag[0])*dpi;pan[1]+=(e.clientY-drag[1])*dpi;drag=[e.clientX,e.clientY];draw()};
c.onpointerup=c.onpointercancel=()=>{drag=null};draw();
</script>)HTML";
}

int main(int argc, char **argv)
{
    const auto resources = std::filesystem::absolute(argv[0]).parent_path() / "resources";
    if (std::filesystem::exists(resources))
        set_resources_dir(resources.string());
    if (argc == 2 && std::string(argv[1]) == "--preview-config") {
        std::cout << preview_config().dump(2) << '\n';
        return 0;
    }
    if (argc == 6 && (std::string(argv[1]) == "--slice-native" || std::string(argv[1]) == "--project-native" || std::string(argv[1]) == "--reslice-native")) {
        try {
            std::cerr << "Loading native slicing configuration\n";
            DynamicPrintConfig config = DynamicPrintConfig::full_print_config();
            // Static enum-vector defaults omit their dynamic string maps.
            // Recreate them through the definition before applying a project,
            // so saving preserves names such as nozzle type and fan threshold.
            for (const auto &key : config.keys())
                if (config.option(key)->type() == coEnums)
                    config.set_key_value(key, config.def()->get(key)->create_default_option());
            std::ifstream input(argv[5]);
            const auto overrides = Json::parse(input);
            std::cerr << "Loading and scaling model\n";
            Model model;
            ModelObject *object = nullptr;
            if (std::filesystem::path(argv[2]).extension() == ".3mf") {
                ConfigSubstitutionContext substitutions(ForwardCompatibilitySubstitutionRule::Enable);
                DynamicPrintConfig project_config;
                PlateDataPtrs plates;
                std::vector<Preset *> presets;
                Semver version;
                bool is_project = false;
                ScopeGuard cleanup([&] {
                    release_PlateData_list(plates);
                    for (Preset *preset : presets)
                        delete preset;
                });
                model = Model::read_from_file(argv[2], &project_config, &substitutions,
                    LoadStrategy::AddDefaultInstances | LoadStrategy::LoadModel | LoadStrategy::LoadConfig,
                    &plates, &presets, &is_project, &version);
                config.apply(project_config);
                if (model.objects.size() != 1)
                    throw std::runtime_error("Evaluation requires a single project object");
                object = model.objects.front();
            } else {
                TriangleMesh mesh;
                if (!mesh.ReadSTLFile(argv[2]))
                    throw std::runtime_error("Could not read STL");
                if (mesh.its.indices.empty())
                    throw std::runtime_error("STL contains no triangles after mesh repair");
                const double scale = std::stod(argv[4]);
                if (!std::isfinite(scale) || scale <= 0.)
                    throw std::runtime_error("Scale must be positive and finite");
                mesh.scale(float(scale));
                const auto bounds = mesh.bounding_box();
                mesh.translate(Vec3f(float(60. - bounds.center().x()), float(60. - bounds.center().y()), float(-bounds.min.z())));
                object = model.add_object();
                object->name = std::filesystem::path(argv[2]).stem().string();
                object->add_volume(std::move(mesh));
                object->add_instance();
                object->ensure_on_bed();
            }
            std::cerr << "Applying printer and process settings\n";
            for (auto item = overrides.begin(); item != overrides.end(); ++item)
                config.set_deserialize_strict(item.key(), item.value().get<std::string>());
            Print print;
            print.is_BBL_printer() = false;
            print.auto_assign_extruders(object);
            std::cerr << "Applying model\n";
            print.apply(model, config);
            std::cerr << "Validating print\n";
            if (auto error = print.validate(); !error.string.empty())
                throw std::runtime_error(error.string);
            const std::string project = std::string(argv[3]) + ".3mf";
            // Export the evaluated process settings, including overrides of an
            // imported project's inherited preset. Its old dirty-key list may
            // otherwise make the GUI restore system values on opening.
            auto &different = config.option<ConfigOptionStrings>("different_settings_to_system", true)->values;
            if (different.empty())
                different.resize(1);
            different.front() = escape_strings_cstyle(Preset::print_options());
            PlateData plate;
            plate.plate_index = 0;
            plate.objects_and_instances.emplace_back(0, 0);
            plate.filament_maps = {1};
            StoreParams store;
            store.path = project.c_str();
            store.model = &model;
            store.config = &config;
            store.plate_data_list = {&plate};
            Preset process_preset(Preset::TYPE_PRINT, config.opt_string("print_settings_id"));
            process_preset.config.apply_only(config, Preset::print_options());
            process_preset.config.set_key_value("print_settings_id", new ConfigOptionString(process_preset.name));
            Preset filament_preset(Preset::TYPE_FILAMENT, config.opt_string("filament_settings_id", 0u));
            filament_preset.config.apply_only(config, Preset::filament_options());
            filament_preset.config.set_key_value("filament_settings_id", new ConfigOptionStrings(std::vector<std::string>{filament_preset.name}));
            store.project_presets = {&process_preset, &filament_preset};
            if (!store_bbs_3mf(store))
                throw std::runtime_error("Could not save editable project");
            if (std::string(argv[1]) == "--project-native") {
                std::cout << "Editable project: " << project << '\n';
                return 0;
            }
            std::cerr << "Slicing\n";
            try {
                print.process();
            } catch (...) {
                if (const auto job = print.objects().front()->continuous_job) {
                    Json diagnostic = Json::array();
                    for (size_t i = 0; i < job->planners.size(); ++i) {
                        const auto &planner = job->planners[i];
                        const auto &p = planner.best();
                        diagnostic.push_back({{"layer", i + 1}, {"attempts", planner.attempts()},
                            {"connected", p.connected}, {"contained", p.contained}, {"reason", p.reason},
                            {"missing_area", p.coverage.missing_area}, {"target_area", p.coverage.target_area}});
                    }
                    std::ofstream(std::string(argv[3]) + ".failure.json") << diagnostic.dump(2);
                }
                throw;
            }
            GCodeProcessorResult result;
            print.export_gcode(argv[3], &result);
            const auto &job = *print.objects().front()->continuous_job;
            Json layers = Json::array();
            for (size_t i = 0; i < job.layers.size(); ++i) {
                const auto &layer = job.layers[i];
                layers.push_back({ {"layer", i + 1}, {"z", layer.print_z}, {"missing_area", layer.coverage.missing_area},
                    {"target_area", layer.coverage.target_area}, {"outside_area", layer.coverage.outside_area},
                    {"excess_area", layer.coverage.excess_area}, {"intentional_void_area", layer.coverage.intentional_void_area},
                    {"transition_volume", layer.transition_volume},
                    {"attempts", job.planners[i].attempts()} });
            }
            std::ofstream report(std::string(argv[3]) + ".json");
            report << Json { {"complete", job.complete}, {"search_seconds", job.elapsed}, {"layers", layers} }.dump(2);
            if (std::string(argv[1]) == "--reslice-native") {
                const auto cached = print.objects().front()->continuous_job;
                const double search_seconds = cached->elapsed;
                config.set_deserialize_strict("ce_flow_control", "volumetric");
                config.set_deserialize_strict("ce_volumetric_flow", "2.5");
                print.apply(model, config);
                print.process();
                if (print.objects().front()->continuous_job != cached || cached->elapsed != search_seconds)
                    throw std::runtime_error("Flow tuning restarted continuous geometry planning");
                print.export_gcode(std::string(argv[3]) + ".resliced.gcode", &result);
                std::cout << "Flow tuning retained geometry and search time\n";
            }
            std::cout << "Native slicing and export complete: " << argv[3] << "\nEditable project: " << project << '\n';
            return 0;
        } catch (const std::exception &e) {
            std::cerr << e.what() << '\n';
            return 1;
        }
    }
    if (argc == 3 && (std::string(argv[1]) == "--check-gcode" || std::string(argv[1]) == "--check-gcode-klipper")) {
        try {
            GCodeProcessor::s_IsBBLPrinter = std::string(argv[1]) == "--check-gcode";
            GCodeProcessor processor;
            processor.init_filament_maps_and_nozzle_type_when_import_only_gcode();
            processor.process_file(argv[2]);
            std::set<unsigned int> layers;
            std::set<std::string> roles;
            size_t extrusions = 0, travels = 0;
            double min_width = 1e30, max_width = 0., min_speed = 1e30, max_speed = 0., min_flow = 1e30, max_flow = 0.;
            for (const auto &move : processor.get_result().moves) {
                if (move.type == EMoveType::Travel)
                    ++travels;
                if (move.type != EMoveType::Extrude)
                    continue;
                ++extrusions;
                layers.insert(move.layer_id);
                roles.insert(ExtrusionEntity::role_to_string(move.extrusion_role));
                min_width = std::min(min_width, double(move.width));
                max_width = std::max(max_width, double(move.width));
                min_speed = std::min(min_speed, double(move.feedrate));
                max_speed = std::max(max_speed, double(move.feedrate));
                min_flow = std::min(min_flow, double(move.volumetric_rate()));
                max_flow = std::max(max_flow, double(move.volumetric_rate()));
            }
            if (!extrusions)
                throw std::runtime_error("Orca did not recognize any extrusion moves");
            std::cout << Json { { "layers", layers.size() }, { "extrusions", extrusions }, { "travels", travels },
                { "roles", roles }, { "width_mm", { min_width, max_width } }, { "speed_mm_s", { min_speed, max_speed } },
                { "nominal_flow_mm3_s", { min_flow, max_flow } } }.dump(2) << '\n';
            return 0;
        } catch (const std::exception &e) {
            std::cerr << e.what() << '\n';
            return 1;
        }
    }
    if (argc < 5 || argc > 7) {
        std::cerr << "Usage: continuous-extrusion-inspect model.stl preview.html scale seconds [settings.json] [interactive]\n"
                  << "Scale is explicit: 1 for millimetres, 1000 for metres.\n";
        return 2;
    }
    try {
        const double scale = std::stod(argv[3]);
        double budget = std::stod(argv[4]);
        ContinuousExtrusionSettings settings;
        bool interactive = false;
        for (int i = 5; i < argc; ++i) {
            if (std::string(argv[i]) == "interactive") {
                interactive = true;
                continue;
            }
            std::ifstream input(argv[i]);
            if (!input)
                throw std::runtime_error("Could not open settings file");
            const Json config = Json::parse(input);
            if (!config.is_object())
                throw std::invalid_argument("Settings must be a JSON object");
            const std::map<std::string, double *> fields {
                { "nominal_width", &settings.nominal_width }, { "min_width", &settings.min_width },
                { "max_width", &settings.max_width }, { "layer_height", &settings.layer_height },
                { "nozzle_diameter", &settings.nozzle_diameter }, { "filament_diameter", &settings.filament_diameter },
                { "filament_speed", &settings.filament_speed }, { "boundary_tolerance", &settings.boundary_tolerance },
                { "resolution", &settings.resolution }
            };
            for (auto entry = config.begin(); entry != config.end(); ++entry) {
                auto field = fields.find(entry.key());
                if (field == fields.end())
                    throw std::invalid_argument("Unknown continuous extrusion setting: " + entry.key());
                *field->second = entry.value().get<double>();
            }
        }
        // Validate before using a configured height to generate slicing planes.
        ContinuousRegionPlanner validate_settings({}, settings);
        continuous_extrusion_speed(settings, 1.);
        if (!std::isfinite(scale) || scale <= 0. || !std::isfinite(budget) || budget < 0.)
            throw std::invalid_argument("Scale must be positive and time budget non-negative");
        TriangleMesh mesh;
        if (!mesh.ReadSTLFile(argv[1]))
            throw std::runtime_error("Could not read STL");
        mesh.scale(float(scale));
        const auto box = mesh.bounding_box();
        if (!box.min.allFinite() || !box.max.allFinite())
            throw std::invalid_argument("Scaled model bounds are not finite");
        if ((box.max.z() - box.min.z()) / settings.layer_height > 10000.)
            throw std::runtime_error("Model exceeds 10000 layers; check scale");
        std::vector<float> zs;
        for (double z = box.min.z() + settings.layer_height / 2.; z < box.max.z(); z += settings.layer_height)
            zs.push_back(float(z));
        if (zs.empty() || zs.size() > 10000)
            throw std::runtime_error("Model has no printable layers or exceeds 10000 layers; check scale");
        auto regions = slice_mesh_ex(mesh.its, zs);
        std::vector<ContinuousRegionPlanner> planners;
        for (const auto &region : regions)
            planners.emplace_back(region, settings);
        double elapsed = 0.;
        tbb::task_arena workers(4);
        do {
            const auto start = Clock::now();
            const auto deadline = start + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(budget));
            // Give every layer an initial candidate, then prioritize unresolved
            // regions. Each planner belongs to one worker at a time; candidate
            // order and scoring do not depend on thread scheduling.
            while (Clock::now() < deadline && !std::all_of(planners.begin(), planners.end(), [](const auto &p) { return p.finished(); })) {
                std::vector<size_t> pending;
                for (size_t i = 0; i < planners.size(); ++i)
                    if (planners[i].attempts() == 0)
                        pending.push_back(i);
                if (pending.empty())
                    for (size_t i = 0; i < planners.size(); ++i) {
                        const auto &p = planners[i].best();
                        if (!planners[i].finished() && !(p.connected && p.widths_valid && p.contained))
                            pending.push_back(i);
                    }
                if (pending.empty())
                    for (size_t i = 0; i < planners.size(); ++i)
                        if (!planners[i].finished())
                            pending.push_back(i);
                workers.execute([&] {
                    tbb::parallel_for(size_t(0), pending.size(), [&](size_t i) {
                        planners[pending[i]].advance(std::min(deadline, Clock::now() + std::chrono::milliseconds(1)));
                    });
                });
            }
            elapsed += std::chrono::duration<double>(Clock::now() - start).count();
            write_preview(argv[2], zs, regions, planners, elapsed, settings);
            size_t evaluated = std::count_if(planners.begin(), planners.end(), [](const auto &p) { return p.attempts() != 0; });
            size_t connected = std::count_if(planners.begin(), planners.end(), [](const auto &p) { return p.best().connected; });
            size_t feasible = std::count_if(planners.begin(), planners.end(), [](const auto &p) {
                return p.best().connected && p.best().widths_valid && p.best().contained;
            });
            std::cout << evaluated << '/' << planners.size() << " layers evaluated, " << connected
                      << " connected, " << feasible << " within width and boundary limits. Search time "
                      << elapsed << " s. Preview: " << argv[2] << '\n';
            if (std::all_of(planners.begin(), planners.end(), [](const auto &p) { return p.finished(); })) {
                std::cout << "All candidate widths have been tested. Additional time will not improve this search.\n";
                break;
            }
            if (!interactive)
                break;
            std::cout << "Additional search seconds (0 to finish): " << std::flush;
            if (!(std::cin >> budget) || !std::isfinite(budget) || budget <= 0.)
                break;
        } while (true);
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
    return 0;
}
