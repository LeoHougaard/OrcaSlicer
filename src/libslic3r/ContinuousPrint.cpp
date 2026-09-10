#include "ContinuousPrint.hpp"
#include "ClipperUtils.hpp"
#include "Layer.hpp"
#include "Print.hpp"

#include <tbb/parallel_for.h>
#include <tbb/task_arena.h>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <limits>
#include <optional>
#include <sstream>

namespace Slic3r {
namespace {
using Clock = std::chrono::steady_clock;

ExtrusionPaths segments(const ExtrusionPaths &paths)
{
    ExtrusionPaths result;
    for (const auto &path : paths)
        for (size_t i = 1; i < path.polyline.points.size(); ++i) {
            if (path.polyline.points[i - 1] == path.polyline.points[i])
                continue;
            auto segment = path;
            segment.polyline.points = { path.polyline.points[i - 1], path.polyline.points[i] };
            result.push_back(std::move(segment));
        }
    return result;
}

void rotate_cycle(ExtrusionPaths &paths, size_t edge, const Point3 &point)
{
    std::rotate(paths.begin(), paths.begin() + edge, paths.end());
    if (point == paths.front().polyline.points.front())
        return;
    auto tail = paths.front();
    tail.polyline.points.back() = point;
    paths.front().polyline.points.front() = point;
    if (paths.front().polyline.points.front() == paths.front().polyline.points.back())
        paths.erase(paths.begin());
    paths.push_back(std::move(tail));
}

bool footprint_inside(const ExtrusionPath &path, const ExPolygons &allowed)
{
    Polygons footprint;
    path.polygons_covered_by_width(footprint, 0.f);
    return diff_ex(footprint, allowed).empty();
}

std::optional<Point> interior_reference(const ExPolygons &region)
{
    if (region.empty())
        return {};
    // Approximate a point with maximum boundary clearance. A contour vertex
    // of one fixed inset can be arbitrarily close to a narrow branch or hole.
    ExPolygons core = region;
    const auto box = get_extents(region);
    double low = 0., high = unscale<double>(std::min(box.size().x(), box.size().y())) * .5;
    for (int i = 0; i < 10; ++i) {
        const double radius = (low + high) * .5;
        auto inset = offset_ex(region, -float(scale_(radius)));
        if (inset.empty())
            high = radius;
        else {
            low = radius;
            core = std::move(inset);
        }
    }
    const auto largest = std::max_element(core.begin(), core.end(), [](const auto &a, const auto &b) { return a.area() < b.area(); });
    const Point center = largest->contour.centroid();
    return largest->contains(center) ? center : largest->contour.points.front();
}
}

bool continuous_print_enabled(const PrintObjectConfig &config)
{
    return config.continuous_extrusion.value || config.slicing_mode.value == SlicingMode::ConstrainedBeadPlanner;
}

std::string continuous_print_validation(const Print &print)
{
    if (std::none_of(print.objects().begin(), print.objects().end(), [](const auto *o) { return continuous_print_enabled(o->config()); }))
        return {};
    if (print.objects().size() != 1 || print.objects().front()->instances().size() != 1)
        return "Continuous extrusion requires exactly one object and one instance on the plate.";
    const auto &object = *print.get_object(size_t(0));
    const auto &config = object.config();
    if (print.config().gcode_flavor.value != gcfKlipper)
        return "Continuous extrusion currently supports standard Klipper G-code.";
    if (print.extruders().size() != 1 || object.num_printing_regions() != 1)
        return "Continuous extrusion currently requires one material and one print region.";
    if (object.has_support_material() || config.raft_layers.value > 0 || print.config().spiral_mode.value)
        return "Disable supports, raft, and spiral vase for continuous extrusion.";
    if (print.has_wipe_tower())
        return "Disable the prime tower for continuous extrusion.";
    if (print.config().skirt_loops.value > 0 &&
        (print.config().skirt_height.value > 1 || print.config().draft_shield.value == dsEnabled))
        return "Continuous extrusion supports a first-layer skirt. Disable the draft shield and set skirt height to one layer.";
    if (config.ce_min_width.value < std::max(config.layer_height.value, print.config().initial_layer_print_height.value) ||
        config.ce_min_width.value > config.ce_nominal_width.value || config.ce_nominal_width.value > config.ce_max_width.value)
        return "Continuous extrusion requires layer height <= minimum bead width <= preferred bead width <= maximum bead width.";
    return {};
}

std::vector<ContinuousLayerRoute> continuous_join_layers(
    const std::vector<ContinuousRegionPlan> &plans, const std::vector<ExPolygons> &regions,
    const std::vector<double> &print_zs, const ContinuousExtrusionSettings &settings,
    double ramp_length, double max_connection, const std::function<void()> &throw_on_cancel)
{
    if (plans.size() != regions.size() || plans.size() != print_zs.size() ||
        !std::isfinite(ramp_length) || ramp_length <= 0. || !std::isfinite(max_connection) || max_connection <= 0.)
        throw std::invalid_argument("Invalid continuous layer transition settings");
    std::vector<ContinuousLayerRoute> result;
    // A stable interior attachment avoids seam drift onto sloping walls.
    // Prefer a material column shared by all layers, with room for the bead
    // and connector. If none exists, use the local endpoint search below.
    ExPolygons common = regions.empty() ? ExPolygons{} : regions.front();
    for (size_t i = 1; i < regions.size() && !common.empty(); ++i) {
        throw_on_cancel();
        common = intersection_ex(common, regions[i]);
    }
    const auto reference = interior_reference(common);
    for (size_t layer = 0; layer < plans.size(); ++layer) {
        throw_on_cancel();
        if (!std::isfinite(print_zs[layer]) || print_zs[layer] <= 0.)
            throw SlicingError("Continuous extrusion requires positive finite layer heights");
        const auto &plan = plans[layer];
        if (!(plan.connected && plan.contained && plan.widths_valid) || plan.paths.empty())
            throw SlicingError("Continuous extrusion layer " + std::to_string(layer + 1) +
                               " has no valid continuous route. Increase the planning budget or adjust bead limits.");
        auto paths = segments(plan.paths);
        if (paths.empty() || paths.front().polyline.points.front() != paths.back().polyline.points.back())
            throw SlicingError("Continuous extrusion requires closed layer routes for layer connections.");
        for (size_t p = 1; p < paths.size(); ++p)
            if (paths[p - 1].polyline.points.back() != paths[p].polyline.points.front())
                throw SlicingError("Disconnected continuous extrusion route");
        ExtrusionPaths connection;
        const auto next_material = layer + 1 < regions.size() ? intersection_ex(regions[layer], regions[layer + 1]) : regions[layer];
        const auto next_centers = offset_ex(next_material, -float(scale_(std::max(0., settings.min_width * .5 - settings.boundary_tolerance))));
        const auto can_leave = [&](const Point &point) {
            return layer + 1 == regions.size() || std::any_of(next_centers.begin(), next_centers.end(), [&](const auto &r) { return r.contains(point); });
        };
        const auto target_reference = reference ? reference : interior_reference(next_material);
        if (layer == 0 && target_reference) {
            size_t edge = 0;
            Point nearest;
            double best_distance = std::numeric_limits<double>::infinity();
            for (size_t i = 0; i < paths.size(); ++i) {
                const Vec2d a = paths[i].polyline.points.front().to_point().cast<double>();
                const Vec2d d = (paths[i].polyline.points.back().to_point() - paths[i].polyline.points.front().to_point()).cast<double>();
                const double t = std::clamp((target_reference->cast<double>() - a).dot(d) / d.squaredNorm(), 0., 1.);
                const Point point = (a + t * d).cast<coord_t>();
                const double distance = target_reference->distance_to(point);
                if (can_leave(point) && distance < best_distance) {
                    best_distance = distance;
                    nearest = point;
                    edge = i;
                }
            }
            if (!std::isfinite(best_distance))
                throw SlicingError("Continuous extrusion has no first-layer attachment that reaches the next layer.");
            rotate_cycle(paths, edge, Point3(nearest, 0));
        } else if (layer == 0) {
            // Start on the middle of a long outer pass, away from a corner.
            size_t edge = 0;
            for (size_t i = 1; i < paths.size(); ++i)
                if (paths[i].role() == erExternalPerimeter &&
                    (paths[edge].role() != erExternalPerimeter || paths[i].length() > paths[edge].length()))
                    edge = i;
            const Point3 midpoint((paths[edge].polyline.points.front() + paths[edge].polyline.points.back()) / coord_t(2));
            rotate_cycle(paths, edge, midpoint);
        } else {
            if (!std::isfinite(print_zs[layer]) || print_zs[layer] <= print_zs[layer - 1])
                throw SlicingError("Continuous extrusion requires increasing layer heights");
            const auto &previous = result.back().paths.back();
            const Point a = previous.polyline.points.back().to_point();
            const Point target = target_reference.value_or(a);
            struct Candidate { size_t edge; Point point; double distance; };
            std::vector<Candidate> candidates;
            for (size_t i = 0; i < paths.size(); ++i) {
                const Vec2d b = paths[i].polyline.points.front().to_point().cast<double>();
                const Vec2d d = (paths[i].polyline.points.back().to_point() - paths[i].polyline.points.front().to_point()).cast<double>();
                for (const auto &projection : {target, a}) {
                    const double t = std::clamp((projection.cast<double>() - b).dot(d) / d.squaredNorm(), 0., 1.);
                    const Point point = (b + t * d).cast<coord_t>();
                    const double distance = unscale<double>(a.distance_to(point));
                    if (distance <= max_connection && can_leave(point))
                        candidates.push_back({i, point, unscale<double>(target.distance_to(point))});
                }
            }
            std::stable_sort(candidates.begin(), candidates.end(), [](const auto &a, const auto &b) { return a.distance < b.distance; });
            const auto allowed = intersection_ex(
                offset_ex(regions[layer - 1], float(scale_(settings.boundary_tolerance))),
                offset_ex(regions[layer], float(scale_(settings.boundary_tolerance))));
            bool joined = false;
            for (const auto &candidate : candidates) {
                throw_on_cancel();
                auto connector = paths[candidate.edge];
                connector.width = float(settings.min_width);
                connector.mm3_per_mm = Flow(connector.width, connector.height, float(settings.nozzle_diameter)).mm3_per_mm();
                connector.polyline.points = {Point3(a, 0), Point3(candidate.point, 0)};
                if (a != candidate.point && !footprint_inside(connector, allowed))
                    continue;
                rotate_cycle(paths, candidate.edge, Point3(candidate.point, 0));
                if (a != candidate.point)
                    connection.push_back(std::move(connector));
                joined = true;
                break;
            }
            if (!joined)
                throw SlicingError("Continuous extrusion cannot connect layer " + std::to_string(layer + 1) +
                                   " inside the model. No travel was inserted. Adjust the layer connection limit or geometry.");
        }
        ContinuousLayerRoute route;
        route.coverage = plan.coverage;
        route.print_z = print_zs[layer];
        double length = 0.;
        for (const auto &path : paths)
            length += unscale<double>(path.length());
        const double ramp = std::min(ramp_length, length);
        const double base_z = layer ? print_zs[layer - 1] : print_zs[layer];
        for (auto &path : connection) {
            route.transition_volume += unscale<double>(path.length()) * path.mm3_per_mm;
            for (auto &point : path.polyline.points)
                point.z() = scale_(base_z);
            route.paths.push_back(std::move(path));
        }
        double distance = 0.;
        for (auto &path : paths) {
            const double segment_length = unscale<double>(path.length());
            const Point3 first = path.polyline.points.front(), last = path.polyline.points.back();
            if (layer && distance < ramp && distance + segment_length > ramp) {
                const double t = (ramp - distance) / segment_length;
                Point3 cut((first.cast<double>() + t * (last - first).cast<double>()).cast<coord_t>());
                if (cut != first && cut != last)
                    path.polyline.points.insert(path.polyline.points.begin() + 1, cut);
            }
            for (auto &point : path.polyline.points) {
                const double along = distance + unscale<double>(point.to_point().distance_to(first.to_point()));
                point.z() = scale_(base_z + (print_zs[layer] - base_z) * std::min(1., along / ramp));
            }
            if (layer && distance < ramp)
                route.transition_volume += std::min(segment_length, ramp - distance) * path.mm3_per_mm;
            distance += segment_length;
            route.paths.push_back(std::move(path));
        }
        if (layer && result.back().paths.back().polyline.points.back() != route.paths.front().polyline.points.front())
            throw SlicingError("Continuous extrusion layer endpoint mismatch");
        result.push_back(std::move(route));
    }
    return result;
}

void apply_continuous_print(Print &print, const std::function<void()> &throw_on_cancel)
{
    if (std::none_of(print.objects().begin(), print.objects().end(), [](const auto *o) { return continuous_print_enabled(o->config()); }))
        return;
    if (auto error = continuous_print_validation(print); !error.empty())
        throw SlicingError(error);
    auto &object = *print.get_object(size_t(0));
    const auto &config = object.config();
    const unsigned int filament = print.extruders().front();
    ContinuousExtrusionSettings settings;
    settings.nominal_width = config.ce_nominal_width.value;
    settings.min_width = config.ce_min_width.value;
    settings.max_width = config.ce_max_width.value;
    settings.filament_speed = config.ce_filament_speed.value;
    settings.filament_diameter = print.config().filament_diameter.get_at(filament);
    settings.nozzle_diameter = print.config().nozzle_diameter.get_at(0);
    settings.resolution = config.ce_resolution.value;
    settings.boundary_tolerance = config.ce_boundary_tolerance.value;
    settings.omit_unreachable = config.ce_omit_unreachable.value;
    settings.closed_route = true;
    settings.missing_weight = config.ce_missing_weight.value;
    settings.excess_weight = config.ce_excess_weight.value;
    std::string signature;
    for (const auto &key : config.keys())
        if (key.rfind("ce_", 0) == 0 && key != "ce_search_time" && key != "ce_flow_control" &&
            key != "ce_volumetric_flow" && key != "ce_filament_speed")
            signature += key + "=" + config.opt_serialize(key) + ";";
    signature += print.config().nozzle_diameter.serialize();
    if (!object.continuous_job || object.continuous_job->signature != signature) {
        object.continuous_job = std::make_shared<ContinuousPrintJob>();
        auto &job = *object.continuous_job;
        job.signature = signature;
        job.settings = settings;
        for (const auto *layer : object.layers()) {
            settings.layer_height = layer->height;
            job.planners.emplace_back(layer->lslices, settings);
        }
    }
    auto &job = *object.continuous_job;
    if (job.complete && (job.elapsed >= config.ce_search_time.value ||
        std::all_of(job.planners.begin(), job.planners.end(), [](const auto &p) { return p.finished(); })))
        return;
    const auto start = Clock::now();
    const auto deadline = start + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(std::max(0., config.ce_search_time.value - job.elapsed)));
    tbb::task_arena workers(4);
    while (Clock::now() < deadline) {
        std::vector<size_t> pending;
        for (size_t i = 0; i < job.planners.size(); ++i)
            if (job.planners[i].attempts() == 0)
                pending.push_back(i);
        if (pending.empty())
            for (size_t i = 0; i < job.planners.size(); ++i) {
                const auto &p = job.planners[i].best();
                if (!job.planners[i].finished() && !(p.connected && p.widths_valid && p.contained))
                    pending.push_back(i);
            }
        if (pending.empty())
            for (size_t i = 0; i < job.planners.size(); ++i)
                if (!job.planners[i].finished())
                    pending.push_back(i);
        if (pending.empty())
            break;
        const auto evaluated = std::count_if(job.planners.begin(), job.planners.end(), [](const auto &p) { return p.attempts() > 0; });
        print.set_status(75, "Continuous extrusion: " + std::to_string(evaluated) + "/" + std::to_string(job.planners.size()) +
                         " layers evaluated, " + std::to_string(int(job.elapsed + std::chrono::duration<double>(Clock::now() - start).count())) + " s");
        std::atomic<size_t> completed {0};
        workers.execute([&] {
            tbb::parallel_for(size_t(0), pending.size(), [&](size_t i) {
                job.planners[pending[i]].advance(std::min(deadline, Clock::now() + std::chrono::milliseconds(1)), throw_on_cancel);
                const auto count = ++completed;
                if (count % 4 == 0)
                    print.set_status(75, "Continuous extrusion: " + std::to_string(count) + "/" +
                                     std::to_string(pending.size()) + " candidates in this pass");
            });
        });
    }
    job.elapsed += std::chrono::duration<double>(Clock::now() - start).count();
    std::vector<ContinuousRegionPlan> plans;
    std::vector<ExPolygons> regions;
    std::vector<double> zs;
    for (size_t i = 0; i < job.planners.size(); ++i) {
        plans.push_back(job.planners[i].best());
        regions.push_back(object.layers()[i]->lslices);
        zs.push_back(object.layers()[i]->print_z);
    }
    job.complete = false;
    job.layers = continuous_join_layers(plans, regions, zs, job.settings, config.ce_ramp_length.value,
                                       config.ce_max_connection.value, throw_on_cancel);
    job.complete = true;
}
} // namespace Slic3r
