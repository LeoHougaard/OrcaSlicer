#include "ContinuousPrint.hpp"
#include "ClipperUtils.hpp"
#include "Layer.hpp"
#include "Print.hpp"
#include "GCode/SeamPlacer.hpp"
#include "I18N.hpp"
#include "Geometry.hpp"

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
        return _u8L("Continuous extrusion requires exactly one object and one instance on the plate.");
    const auto &object = *print.get_object(size_t(0));
    const auto &config = object.config();
    if (print.config().gcode_flavor.value != gcfKlipper)
        return _u8L("Continuous extrusion currently supports standard Klipper G-code.");
    const auto &model = print.model();
    const auto commands = model.plates_custom_gcodes.find(model.curr_plate_index);
    if (commands != model.plates_custom_gcodes.end() && !commands->second.gcodes.empty())
        return _u8L("Remove layer pauses, color changes, and custom layer commands from this plate, or disable continuous extrusion.");
    if (print.extruders().size() != 1 || object.num_printing_regions() != 1)
        return _u8L("Continuous extrusion currently requires one material and one print region.");
    if (object.printing_region(0).config().wall_loops.value < 1)
        return _u8L("Continuous extrusion requires at least one wall loop to connect the interior.");
    if (object.has_support_material() || config.raft_layers.value > 0 || print.config().spiral_mode.value)
        return _u8L("Disable supports, raft, and spiral vase for continuous extrusion.");
    if (print.has_wipe_tower())
        return _u8L("Disable the prime tower for continuous extrusion.");
    if (print.config().skirt_loops.value > 0 &&
        (print.config().skirt_height.value > 1 || print.config().draft_shield.value == dsEnabled))
        return _u8L("Continuous extrusion supports a first-layer skirt. Disable the draft shield and set skirt height to one layer.");
    if (config.ce_min_width.value < std::max(config.layer_height.value, print.config().initial_layer_print_height.value) ||
        config.ce_min_width.value > config.ce_nominal_width.value || config.ce_nominal_width.value > config.ce_max_width.value)
        return _u8L("Continuous extrusion requires layer height <= minimum bead width <= preferred bead width <= maximum bead width.");
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
    // Prefer a deposited column shared by all layers. Using only the model
    // outlines can choose an empty sparse cell; starting on the outer wall
    // can strand the route when a later layer steps inward.
    ExPolygons common = regions.empty() ? ExPolygons{} : regions.front();
    for (size_t i = 0; i < plans.size() && !common.empty(); ++i) {
        throw_on_cancel();
        if (settings.infill_density < 1.) {
            Polygons deposited;
            for (const auto &path : plans[i].paths)
                path.polygons_covered_by_width(deposited, 0.f);
            common = intersection_ex(common, union_ex(deposited));
        } else {
            // Solid layers have no intentional voids. Avoid repeatedly
            // unioning thousands of adjacent beads just to find a reference.
            common = intersection_ex(common, regions[i]);
        }
    }
    const auto reference = interior_reference(common);
    for (size_t layer = 0; layer < plans.size(); ++layer) {
        throw_on_cancel();
        if (!std::isfinite(print_zs[layer]) || print_zs[layer] <= 0.)
            throw SlicingError(_u8L("Continuous extrusion requires positive finite layer heights"));
        const auto &plan = plans[layer];
        if (!(plan.connected && plan.contained && plan.widths_valid) || plan.paths.empty())
            throw SlicingError(format(_u8L("Continuous extrusion layer %1% has no valid continuous route. Increase the planning budget or adjust bead limits."), layer + 1));
        auto paths = segments(plan.paths);
        if (paths.empty() || paths.front().polyline.points.front() != paths.back().polyline.points.back())
            throw SlicingError(_u8L("Continuous extrusion requires closed layer routes for layer connections."));
        for (size_t p = 1; p < paths.size(); ++p)
            if (paths[p - 1].polyline.points.back() != paths[p].polyline.points.front())
                throw SlicingError(_u8L("Disconnected continuous extrusion route"));
        ExtrusionPaths connection;
        const auto next_material = layer + 1 < regions.size() ? intersection_ex(regions[layer], regions[layer + 1]) : regions[layer];
        ExPolygons next_centers;
        if (layer + 1 < plans.size()) {
            // A sloping wall moves between layers. Its departure need only be
            // within reach of next-layer deposition, not inside an inset of
            // both sections at once. The incoming ramp follows that movement.
            Polygons reachable;
            for (auto path : plans[layer + 1].paths) {
                path.width = float(2. * max_connection);
                path.polygons_covered_by_width(reachable, 0.f);
            }
            next_centers = union_ex(reachable);
        }
        const auto can_leave = [&](const Point &point) {
            return layer + 1 == regions.size() || std::any_of(next_centers.begin(), next_centers.end(), [&](const auto &r) { return r.contains(point); });
        };
        auto target_reference = reference ? reference : interior_reference(next_material);
        if (settings.infill_density < 1. && !reference) {
            // Sparse layers cannot ramp through an empty interior. Start at a
            // wall attachment selected by the seam planner instead.
            for (size_t i = 0; i < paths.size(); ++i)
                if (paths[i].role() == erExternalPerimeter &&
                    paths[(i + 1) % paths.size()].role() != erExternalPerimeter) {
                    target_reference = paths[i].last_point();
                    break;
                }
        }
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
                throw SlicingError(_u8L("Continuous extrusion has no first-layer attachment that reaches the next layer."));
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
                throw SlicingError(_u8L("Continuous extrusion requires increasing layer heights"));
            const auto &previous = result.back().paths.back();
            const Point a = previous.polyline.points.back().to_point();
            const Point target = target_reference.value_or(a);
            struct Candidate { size_t edge; Point point; double distance, preference; bool endpoint; };
            std::vector<Candidate> candidates;
            for (size_t i = 0; i < paths.size(); ++i) {
                const Vec2d b = paths[i].polyline.points.front().to_point().cast<double>();
                const Vec2d d = (paths[i].polyline.points.back().to_point() - paths[i].polyline.points.front().to_point()).cast<double>();
                const Points projections{target, a, paths[i].first_point(), paths[i].last_point()};
                for (size_t projection_index = 0; projection_index < projections.size(); ++projection_index) {
                    const auto &projection = projections[projection_index];
                    const double t = std::clamp((projection.cast<double>() - b).dot(d) / d.squaredNorm(), 0., 1.);
                    const Point point = (b + t * d).cast<coord_t>();
                    const double distance = unscale<double>(a.distance_to(point));
                    if (distance <= max_connection && can_leave(point))
                        candidates.push_back({i, point, distance, unscale<double>(target.distance_to(point)), projection_index >= 2});
                }
            }
            std::stable_sort(candidates.begin(), candidates.end(), [](const auto &a, const auto &b) {
                if (a.endpoint != b.endpoint)
                    return !a.endpoint;
                return a.distance != b.distance ? a.distance < b.distance : a.preference < b.preference;
            });
            ExPolygons envelope = regions[layer - 1];
            append(envelope, regions[layer]);
            const auto allowed = offset_ex(union_ex(envelope), float(scale_(settings.boundary_tolerance)));
            bool joined = false;
            for (const auto &candidate : candidates) {
                throw_on_cancel();
                auto connector = paths[candidate.edge];
                connector.width = float(settings.min_width);
                connector.mm3_per_mm = Flow(connector.width, connector.height, float(settings.nozzle_diameter)).mm3_per_mm();
                connector.polyline.points = {Point3(a, 0), Point3(candidate.point, 0)};
                if (a != candidate.point && !footprint_inside(connector, allowed))
                    continue;
                // Enter the next route at its first visible edge. A connector
                // across another next-layer edge would be printed twice there.
                bool crosses_route = false;
                bool reuse_entry = false;
                for (size_t i = 0; a != candidate.point && i < paths.size(); ++i) {
                    const Point b = paths[i].first_point(), c = paths[i].last_point();
                    const Vec2d incoming = (candidate.point - a).cast<double>();
                    const double length = incoming.norm();
                    // Include the entry edge itself. Running along it before
                    // traversing the cycle is a retrace, even if both segments
                    // share an endpoint. Allow only sub-quantization overlap.
                    const double tolerance = scale_(.001);
                    if (std::abs(cross2(incoming, (b - a).cast<double>())) <= tolerance * length &&
                        std::abs(cross2(incoming, (c - a).cast<double>())) <= tolerance * length) {
                        const double u = incoming.dot((b - a).cast<double>()) / length;
                        const double v = incoming.dot((c - a).cast<double>()) / length;
                        if (std::min(length, std::max(u, v)) - std::max(0., std::min(u, v)) > tolerance) {
                            if (i == candidate.edge) {
                                reuse_entry = true;
                                continue;
                            }
                            crosses_route = true;
                            break;
                        }
                    }
                    if (i == candidate.edge)
                        continue;
                    if (b == a || c == a || b == candidate.point || c == candidate.point)
                        continue;
                    if (Geometry::segments_intersect(a, candidate.point, b, c)) {
                        crosses_route = true;
                        break;
                    }
                }
                if (crosses_route)
                    continue;
                if (reuse_entry) {
                    // The incoming ramp can replace a portion of its entry
                    // edge. Traverse the rest of the cycle away from that
                    // interval and finish at its other end, instead of
                    // depositing the same interval again on the way back.
                    auto opened = paths;
                    size_t edge = candidate.edge;
                    const Line entry(paths[edge].first_point(), paths[edge].last_point());
                    Point finish;
                    entry.distance_to_squared(a, &finish);
                    if (!can_leave(finish))
                        continue;
                    if ((candidate.point - a).cast<double>().dot((entry.b - entry.a).cast<double>()) < 0.) {
                        std::reverse(opened.begin(), opened.end());
                        for (auto &path : opened)
                            path.polyline.reverse();
                        edge = opened.size() - 1 - edge;
                    }
                    rotate_cycle(opened, edge, Point3(candidate.point, 0));
                    if (opened.back().first_point() == finish)
                        opened.pop_back();
                    else
                        opened.back().polyline.points.back() = Point3(finish, 0);
                    connector.width = paths[candidate.edge].width;
                    connector.mm3_per_mm = paths[candidate.edge].mm3_per_mm;
                    if (!footprint_inside(connector, allowed))
                        continue;
                    paths = std::move(opened);
                } else {
                    rotate_cycle(paths, candidate.edge, Point3(candidate.point, 0));
                }
                if (a != candidate.point)
                    connection.push_back(std::move(connector));
                joined = true;
                break;
            }
            if (!joined)
                throw SlicingError(format(_u8L("Continuous extrusion cannot connect layer %1% inside the model. No travel was inserted. Increase wall loops, adjust the layer connection limit, or change the geometry."), layer + 1));
        }
        ContinuousLayerRoute route;
        route.coverage = plan.coverage;
        route.print_z = print_zs[layer];
        // Rise during the incoming connection too. Printing it flat at the
        // previous Z scratched across already completed walls and top skins.
        paths.insert(paths.begin(), connection.begin(), connection.end());
        double length = 0.;
        for (const auto &path : paths)
            length += unscale<double>(path.length());
        double ramp = std::min(ramp_length, length);
        if (!connection.empty())
            ramp = std::min(ramp, unscale<double>(connection.front().length()));
        const double base_z = layer ? print_zs[layer - 1] : print_zs[layer];
        if (layer && !paths.empty())
            // The first endpoint must still rise after XYZ is rounded to
            // 0.001 mm in G-code. Otherwise a short first edge becomes a flat
            // retrace of the preceding layer despite a rising planner path.
            ramp = std::min(ramp, unscale<double>(paths.front().length()) *
                                  (print_zs[layer] - base_z) / .001);
        if (layer) {
            // Reach the new layer height before crossing an old-layer stroke.
            // Collinear stacking is the normal ramp; transverse crossings need
            // full clearance, especially when a sparse layer meets a solid skin.
            double along = 0.;
            for (const auto &path : paths) {
                if (along >= ramp)
                    break;
                const Line next(path.first_point(), path.last_point());
                for (const auto &old_path : result.back().paths)
                    for (size_t j = 1; j < old_path.polyline.points.size(); ++j) {
                        const Line old(old_path.polyline.points[j - 1].to_point(), old_path.polyline.points[j].to_point());
                        if (std::abs(cross2((next.b - next.a).cast<double>(), (old.b - old.a).cast<double>())) < 1.)
                            continue;
                        Point hit;
                        if (next.intersection(old, &hit)) {
                            if ((hit == next.a || hit == next.b) && (hit == old.a || hit == old.b))
                                continue;
                            const double distance = along + unscale<double>(next.a.distance_to(hit));
                            if (distance > .001)
                                ramp = std::min(ramp, distance * .5);
                        }
                    }
                along += unscale<double>(next.length());
            }
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
            throw SlicingError(_u8L("Continuous extrusion layer endpoint mismatch"));
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
    const auto &region_config = object.printing_region(0).config();
    settings.infill_density = region_config.sparse_infill_density.value / 100.;
    settings.wall_loops = size_t(region_config.wall_loops.value);
    std::string signature;
    for (const auto &key : config.keys())
        if (key.rfind("ce_", 0) == 0 && key != "ce_search_time" && key != "ce_flow_control" &&
            key != "ce_volumetric_flow" && key != "ce_filament_speed")
            signature += key + "=" + config.opt_serialize(key) + ";";
    signature += print.config().nozzle_diameter.serialize();
    signature += config.seam_position.serialize() + config.staggered_inner_seams.serialize();
    for (const auto *key : {"sparse_infill_density", "wall_loops", "top_shell_layers", "bottom_shell_layers",
                            "top_shell_thickness", "bottom_shell_thickness"})
        signature += std::string(key) + "=" + region_config.opt_serialize(key) + ";";
    if (!object.continuous_job || object.continuous_job->signature != signature) {
        // Publish only after every layer is initialized. Cancellation during
        // seam placement must not leave a partial cache for the next slice.
        auto pending_job = std::make_shared<ContinuousPrintJob>();
        auto &job = *pending_job;
        job.signature = signature;
        job.settings = settings;
        print.set_status(75, _u8L("Continuous extrusion: placing wall attachments"));
        SeamPlacer seams;
        seams.init(print, throw_on_cancel);
        Point previous_seam = object.bounding_box().min;
        for (const auto *layer : object.layers()) {
            throw_on_cancel();
            settings.layer_height = layer->height;
            settings.solid_regions.clear();
            settings.seam_positions.clear();
            settings.inner_seam_positions.clear();
            for (const auto *region : layer->regions()) {
                for (const auto &surface : region->fill_surfaces.surfaces)
                    if (surface.surface_type != stInternal && surface.surface_type != stInternalVoid)
                        settings.solid_regions.push_back(surface.expolygon);
                auto perimeters = region->perimeters.flatten();
                for (const auto *entity : perimeters.entities)
                    if (const auto *loop = dynamic_cast<const ExtrusionLoop *>(entity);
                        loop && is_perimeter(loop->role()) && !loop->paths.empty()) {
                        auto copy = *loop;
                        float overhang = 0.f;
                        seams.place_seam(layer, copy, previous_seam, overhang);
                        if (loop->role() == erExternalPerimeter) {
                            previous_seam = copy.first_point();
                            settings.seam_positions.push_back(previous_seam);
                        } else {
                            settings.inner_seam_positions.push_back(copy.first_point());
                        }
                    }
            }
            job.planners.emplace_back(layer->lslices, settings);
        }
        object.continuous_job = std::move(pending_job);
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
        print.set_status(75, format(_u8L("Continuous extrusion: %1%/%2% layers evaluated, %3% s"), evaluated,
            job.planners.size(), int(job.elapsed + std::chrono::duration<double>(Clock::now() - start).count())));
        std::atomic<size_t> completed {0};
        workers.execute([&] {
            tbb::parallel_for(size_t(0), pending.size(), [&](size_t i) {
                job.planners[pending[i]].advance(std::min(deadline, Clock::now() + std::chrono::milliseconds(1)), throw_on_cancel);
                const auto count = ++completed;
                if (count % 4 == 0)
                    print.set_status(75, format(_u8L("Continuous extrusion: %1%/%2% candidates in this pass"), count, pending.size()));
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
