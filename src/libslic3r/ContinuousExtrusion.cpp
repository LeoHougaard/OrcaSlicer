#include "ContinuousExtrusion.hpp"

#include "Arachne/WallToolPaths.hpp"
#include "ClipperUtils.hpp"
#include "Flow.hpp"

#include <algorithm>
#include <cmath>
#include <optional>
#include <stdexcept>

namespace Slic3r {
namespace {

double area_mm2(const ExPolygons &polygons)
{
    return area(polygons) * SCALING_FACTOR * SCALING_FACTOR;
}

// A swept rectangular strip with butt ends. Adjacent collinear segments do not
// count their shared endpoints as overfill. Width transitions are resolved by
// the variable-width path conversion before this evaluator runs.
Polygon strip(const Point &a, const Point &b, double width)
{
    const Vec2d direction = (b - a).cast<double>();
    const Vec2d normal = Vec2d(-direction.y(), direction.x()).normalized() * scaled<double>(width * 0.5);
    Polygon polygon;
    polygon.points = { (a.cast<double>() + normal).cast<coord_t>(),
                       (a.cast<double>() - normal).cast<coord_t>(),
                       (b.cast<double>() - normal).cast<coord_t>(),
                       (b.cast<double>() + normal).cast<coord_t>() };
    polygon.make_counter_clockwise();
    return polygon;
}

struct BeadPoint {
    Point point;
    double spacing;
    bool external;
    ExtrusionRole role = erSolidInfill;
    bool connector = false;
};
using BeadLoop = std::vector<BeadPoint>;

BeadPoint interpolate(const BeadPoint &a, const BeadPoint &b, double t)
{
    return { (a.point.cast<double>() + t * (b.point - a.point).cast<double>()).cast<coord_t>(),
             a.spacing + t * (b.spacing - a.spacing), a.external, a.role, a.connector };
}

// Open a short interval on a cycle. The returned path runs from just after the
// interval all the way around to just before it. Joining two such paths replaces
// two local intervals with two connectors, instead of depositing a connector
// on top of a completely closed loop.
BeadLoop open_loop(const BeadLoop &loop, size_t edge, double t, double cut)
{
    const auto &a = loop[edge];
    const auto &b = loop[(edge + 1) % loop.size()];
    const double length = unscale<double>(a.point.distance_to(b.point));
    const double half = std::min(cut / (2. * length), 0.45);
    t = std::clamp(t, half, 1. - half);
    BeadLoop result;
    result.reserve(loop.size() + 2);
    result.push_back(interpolate(a, b, t + half));
    for (size_t j = 1; j <= loop.size(); ++j)
        result.push_back(loop[(edge + j) % loop.size()]);
    result.push_back(interpolate(a, b, t - half));
    return result;
}

bool connector_inside(const BeadPoint &a, const BeadPoint &b, const ExPolygons &region)
{
    if (a.point == b.point)
        return true;
    return diff_ex(strip(a.point, b.point, std::max(a.spacing, b.spacing)), region).empty();
}

bool join_loop(BeadLoop &route, const BeadLoop &loop, const ExPolygons &region,
               double cut, double max_distance, const ContinuousExtrusionSettings &settings,
               const std::function<void()> &throw_on_cancel)
{
    struct Join { size_t a, b; double ta, tb, distance, seam_cost; int external_changes, connector_changes; };
    std::vector<Join> candidates;
    // Search edge interiors as well as vertices: long straight walls otherwise
    // force seams into corners. Keep a bounded shortlist before polygon checks.
    for (size_t a = 0; a < route.size(); ++a) {
        if ((a & 63) == 0)
            throw_on_cancel();
        const auto &pa = route[a].point;
        const auto &qa = route[(a + 1) % route.size()].point;
        const Vec2d da = (qa - pa).cast<double>();
        if (da.squaredNorm() < 1.)
            continue;
        for (size_t b = 0; b < loop.size(); ++b) {
            const auto &pb = loop[b].point;
            const auto &qb = loop[(b + 1) % loop.size()].point;
            const Vec2d db = (qb - pb).cast<double>();
            if (db.squaredNorm() < 1.)
                continue;
            Vec2d target = (pb.cast<double>() + qb.cast<double>()) * .5;
            const bool external_a = route[a].external;
            const bool external_b = loop[b].external;
            const bool wall_a = is_perimeter(route[a].role);
            const bool wall_b = is_perimeter(loop[b].role);
            const auto &seams = external_a || external_b ? settings.seam_positions : settings.inner_seam_positions;
            const bool use_seam = !seams.empty() && (wall_a || wall_b);
            if (use_seam) {
                const Vec2d middle = wall_a ? (pa.cast<double>() + qa.cast<double>()) * .5 : target;
                target = std::min_element(seams.begin(), seams.end(), [&](const Point &x, const Point &y) {
                    return (x.cast<double>() - middle).squaredNorm() < (y.cast<double>() - middle).squaredNorm();
                })->cast<double>();
            }
            const double margin_a = use_seam ? std::min(.45, scaled<double>(cut * .5) / da.norm()) : .1;
            const double margin_b = use_seam ? std::min(.45, scaled<double>(cut * .5) / db.norm()) : .1;
            double ta = std::clamp((target - pa.cast<double>()).dot(da) / da.squaredNorm(), margin_a, 1. - margin_a);
            Vec2d pos_a = pa.cast<double>() + ta * da;
            double tb = std::clamp((pos_a - pb.cast<double>()).dot(db) / db.squaredNorm(), margin_b, 1. - margin_b);
            ta = std::clamp((pb.cast<double>() + tb * db - pa.cast<double>()).dot(da) / da.squaredNorm(), margin_a, 1. - margin_a);
            double distance = (pa.cast<double>() + ta * da - pb.cast<double>() - tb * db).squaredNorm();
            double seam_cost = 0.;
            if (use_seam) {
                const Vec2d attachment = wall_a ? pa.cast<double>() + ta * da : pb.cast<double>() + tb * db;
                seam_cost = 1. + (attachment - target).norm();
            }
            if (distance <= std::pow(scaled<double>(max_distance), 2))
                candidates.push_back({ a, b, ta, tb, distance, seam_cost,
                    settings.seam_positions.empty() ? 0 : int(external_a) + int(external_b),
                    use_seam || !settings.inner_seam_positions.empty() ? int(route[a].connector) + int(loop[b].connector) : 0 });
        }
    }
    std::stable_sort(candidates.begin(), candidates.end(), [](const Join &a, const Join &b) {
        if (a.external_changes != b.external_changes)
            return a.external_changes < b.external_changes;
        if (a.connector_changes != b.connector_changes)
            return a.connector_changes < b.connector_changes;
        return a.seam_cost != b.seam_cost ? a.seam_cost < b.seam_cost : a.distance < b.distance;
    });
    if (candidates.size() > 64)
        candidates.resize(64);
    for (const auto &candidate : candidates) {
        throw_on_cancel();
        BeadLoop a = open_loop(route, candidate.a, candidate.ta, cut);
        BeadLoop b = open_loop(loop, candidate.b, candidate.tb, cut);
        double forward = a.back().point.distance_to(b.front().point) + b.back().point.distance_to(a.front().point);
        double reverse = a.back().point.distance_to(b.back().point) + b.front().point.distance_to(a.front().point);
        if (reverse < forward)
            std::reverse(b.begin(), b.end());
        if (!connector_inside(a.back(), b.front(), region) || !connector_inside(b.back(), a.front(), region))
            continue;
        a.back().external = false;
        a.back().role = erSolidInfill;
        a.back().connector = true;
        b.back().external = false;
        b.back().role = erSolidInfill;
        b.back().connector = true;
        a.insert(a.end(), b.begin(), b.end());
        route = std::move(a);
        return true;
    }
    return false;
}

// A single remaining centerline can be an endpoint of the layer route. There
// is no need to print it twice just to force every layer into a closed cycle.
// Start the cycle at the attachment, traverse it once, then follow the stroke.
bool attach_stroke(BeadLoop &route, const BeadLoop &stroke, const ExPolygons &region,
                   double max_distance, const std::function<void()> &throw_on_cancel)
{
    struct Join { size_t edge; double t, distance; bool reverse; };
    std::vector<Join> candidates;
    for (size_t i = 0; i < route.size(); ++i) {
        if ((i & 63) == 0)
            throw_on_cancel();
        const Vec2d a = route[i].point.cast<double>();
        const Vec2d d = (route[(i + 1) % route.size()].point - route[i].point).cast<double>();
        if (d.squaredNorm() < 1.)
            continue;
        for (bool reverse : { false, true }) {
            const Vec2d endpoint = (reverse ? stroke.back() : stroke.front()).point.cast<double>();
            const double t = std::clamp((endpoint - a).dot(d) / d.squaredNorm(), 0., 1.);
            const double distance = (endpoint - a - t * d).squaredNorm();
            if (distance <= std::pow(scaled<double>(max_distance), 2))
                candidates.push_back({ i, t, distance, reverse });
        }
    }
    std::stable_sort(candidates.begin(), candidates.end(), [](const Join &a, const Join &b) { return a.distance < b.distance; });
    for (const auto &candidate : candidates) {
        throw_on_cancel();
        BeadLoop opened = open_loop(route, candidate.edge, candidate.t, 0.);
        BeadLoop tail = stroke;
        if (candidate.reverse)
            std::reverse(tail.begin(), tail.end());
        if (!connector_inside(opened.back(), tail.front(), region))
            continue;
        opened.back().external = false;
        opened.back().role = erSolidInfill;
        opened.back().connector = true;
        opened.insert(opened.end(), tail.begin(), tail.end());
        route = std::move(opened);
        return true;
    }
    return false;
}

ExtrusionPaths to_paths(const BeadLoop &loop, double height, bool closed)
{
    ExtrusionPaths result;
    if (loop.size() < 2)
        return result;
    for (size_t j = 0; j + 1 < loop.size() + size_t(closed); ++j) {
        const auto &a = loop[j];
        const auto &b = loop[(j + 1) % loop.size()];
        if (a.point == b.point)
            continue;
        const double spacing = .5 * (a.spacing + b.spacing);
        if (spacing <= 0.)
            continue;
        const float width = Flow::rounded_rectangle_extrusion_width_from_spacing(float(spacing), float(height));
        const auto role = a.external ? erExternalPerimeter : a.role;
        ExtrusionPath path(role, spacing * height, width, float(height));
        path.polyline.points = { Point3(a.point, 0), Point3(b.point, 0) };
        // Preserve long constant-width passes for preview and downstream checks.
        if (!result.empty() && result.back().last_point() == path.first_point() &&
            result.back().role() == role && std::abs(result.back().width - width) < 1e-6) {
            result.back().polyline.points.emplace_back(b.point, 0);
        } else {
            result.emplace_back(std::move(path));
        }
    }
    return result;
}

} // namespace

ContinuousCoverage continuous_coverage(const ExPolygons &region, const ExtrusionPaths &paths)
{
    ContinuousCoverage result;
    Polygons strips;
    double sum_area = 0.;
    std::optional<coord_t> plane_z;
    for (const auto &path : paths) {
        if (!std::isfinite(path.height) || !std::isfinite(path.mm3_per_mm) || path.height <= 0. || path.mm3_per_mm <= 0.)
            throw std::invalid_argument("Continuous extrusion requires positive bead height and area");
        for (const auto &point : path.polyline.points) {
            if (!plane_z)
                plane_z = point.z();
            if (point.z() != *plane_z)
                throw std::invalid_argument("Region coverage cannot evaluate non-planar extrusion");
        }
        const double width = path.mm3_per_mm / path.height;
        for (size_t j = 1; j < path.polyline.points.size(); ++j) {
            const Point a = path.polyline.points[j - 1].to_point();
            const Point b = path.polyline.points[j].to_point();
            if (a == b)
                continue;
            strips.emplace_back(strip(a, b, width));
            sum_area += std::abs(strips.back().area()) * SCALING_FACTOR * SCALING_FACTOR;
            result.deposited_volume += unscale<double>(a.distance_to(b)) * path.mm3_per_mm;
        }
    }
    ExPolygons covered = union_ex(strips);
    // Subtract one winding from the union. Positive winding now identifies
    // locations covered at least twice, including self-overlap within a path.
    Polygons subtract_once = to_polygons(covered);
    for (auto &polygon : subtract_once)
        polygon.reverse();
    append(strips, std::move(subtract_once));
    result.excess = union_ex(strips, ClipperLib::pftPositive);
    result.target_area = area_mm2(region);
    result.missing = diff_ex(region, covered);
    result.outside = diff_ex(covered, region);
    result.missing_area = area_mm2(result.missing);
    result.outside_area = area_mm2(result.outside);
    result.excess_area = std::max(0., sum_area - area_mm2(covered));
    return result;
}

double continuous_extrusion_speed(const ContinuousExtrusionSettings &settings, double mm3_per_mm)
{
    if (!std::isfinite(mm3_per_mm) || mm3_per_mm <= 0. || !std::isfinite(settings.filament_speed) ||
        settings.filament_speed <= 0. || !std::isfinite(settings.filament_diameter) || settings.filament_diameter <= 0.)
        throw std::invalid_argument("Continuous extrusion requires positive finite bead area, filament diameter, and constant filament speed");
    const double speed = settings.filament_speed * PI * std::pow(settings.filament_diameter * .5, 2) / mm3_per_mm;
    if (!std::isfinite(speed) || speed <= 0.)
        throw std::invalid_argument("Continuous extrusion speed is not representable");
    return speed;
}

ContinuousRegionPlanner::ContinuousRegionPlanner(ExPolygons region, ContinuousExtrusionSettings settings)
    : m_region(union_ex(region)), m_settings(settings)
{
    for (double value : { settings.min_width, settings.max_width, settings.nominal_width, settings.layer_height,
                           settings.nozzle_diameter, settings.resolution, settings.missing_weight, settings.excess_weight })
        if (!std::isfinite(value) || value <= 0.)
            throw std::invalid_argument("Continuous extrusion geometry settings must be positive and finite");
    if (settings.min_width > settings.nominal_width || settings.nominal_width > settings.max_width ||
        settings.min_width < settings.layer_height || !std::isfinite(settings.boundary_tolerance) || settings.boundary_tolerance < 0.)
        throw std::invalid_argument("Continuous extrusion requires layer height <= minimum width <= nominal width <= maximum width");
    if (!std::isfinite(settings.infill_density) || settings.infill_density < 0. || settings.infill_density > 1.)
        throw std::invalid_argument("Continuous infill density must be between zero and one");
    m_widths.push_back(settings.nominal_width);
    // A deterministic, finite initial search. Extending a budget resumes at the
    // next candidate and never replaces a better candidate with a worse one.
    for (int i = 1; i <= 4; ++i) {
        m_widths.push_back(settings.nominal_width + (settings.max_width - settings.nominal_width) * i / 4.);
        m_widths.push_back(settings.nominal_width - (settings.nominal_width - settings.min_width) * i / 4.);
    }
    m_best.coverage = continuous_coverage(m_region, {});
    m_best.reason = "No candidate evaluated yet";
}

ContinuousRegionPlan ContinuousRegionPlanner::generate(double width, const std::function<void()> &throw_on_cancel) const
{
    auto result = generate_region(m_region, width, throw_on_cancel);
    if (m_settings.omit_unreachable && (!result.contained || result.paths.empty())) {
        // Remove necks too narrow for the minimum bead, then plan the remaining
        // material. Keep the original target for coverage/error reporting.
        const float radius = float(scaled<double>(m_settings.min_width * .5 + m_settings.resolution));
        auto printable = intersection_ex(m_region, offset_ex(offset_ex(m_region, -radius), radius));
        auto trimmed = generate_region(printable, width, throw_on_cancel);
        if (trimmed.connected && trimmed.contained && trimmed.widths_valid)
            result = std::move(trimmed);
    }
    return result;
}

ContinuousRegionPlan ContinuousRegionPlanner::generate_region(const ExPolygons &input_region, double width,
                                                              const std::function<void()> &throw_on_cancel) const
{
    ContinuousRegionPlan result;
    ExPolygons region = input_region;
    if (region.size() > 1 && m_settings.omit_unreachable) {
        auto largest = std::max_element(region.begin(), region.end(), [](const auto &a, const auto &b) { return a.area() < b.area(); });
        region = { *largest };
    }
    if (region.size() != 1) {
        result.coverage = continuous_coverage(m_region, {});
        result.reason = "A continuous region requires one connected material region";
        return result;
    }
    const double h = m_settings.layer_height;
    const double spacing = Flow::rounded_rectangle_extrusion_spacing(float(width), float(h));
    Arachne::WallToolPathsParams params {};
    params.min_bead_width = float(m_settings.min_width);
    params.min_feature_size = float(m_settings.resolution);
    params.min_length_factor = .5f;
    params.wall_transition_length = float(m_settings.nozzle_diameter);
    params.wall_transition_angle = 10.f;
    params.wall_transition_filter_deviation = float(m_settings.resolution);
    params.wall_distribution_count = 2;
    params.is_top_or_bottom_layer = true;
    params.prefer_closed_loops = true;
    params.wall_maximum_resolution = scaled<coord_t>(.5);
    params.wall_maximum_deviation = scaled<coord_t>(m_settings.resolution);
    Polygons outline = to_polygons(region);
    const auto box = get_extents(outline);
    const size_t count = size_t(unscale<double>(std::max(box.size().x(), box.size().y())) / spacing) + 2;
    Arachne::WallToolPaths walls(outline, scaled<coord_t>(spacing), scaled<coord_t>(spacing), count, 0, h, params);
    std::vector<BeadLoop> loops, strokes;
    Polygons intentional_void;
    // Ordinary fill surfaces stop at the ordinary wall generator's inner edge.
    // Our candidate wall widths can differ. Extend skins through that wall band
    // so width optimization cannot leave a sparse ring around a solid shell.
    const auto solid_regions = offset_ex(m_settings.solid_regions,
        float(scale_(m_settings.max_width * double(m_settings.wall_loops))));
    const double min_spacing = Flow::rounded_rectangle_extrusion_spacing(float(m_settings.min_width), float(h));
    const double max_spacing = Flow::rounded_rectangle_extrusion_spacing(float(m_settings.max_width), float(h));
    for (const auto &inset : walls.getToolPaths()) {
        throw_on_cancel();
        for (const auto &line : inset) {
            if (std::all_of(line.begin(), line.end(), [](const auto &junction) { return junction.w == 0; }))
                continue;
            BeadLoop loop;
            const bool wall = line.inset_idx < m_settings.wall_loops;
            const bool sparse = m_settings.infill_density < 1. && !wall;
            const auto role = wall ? (line.inset_idx == 0 ? erExternalPerimeter : erPerimeter) :
                              sparse ? erInternalInfill : erSolidInfill;
            for (const auto &j : line.junctions)
                if (loop.empty() || loop.back().point != j.p)
                    // Clamp before evaluating deposition. This may introduce
                    // local excess or missing material; both remain in the
                    // score and preview. Never turn a vanishing marker into
                    // an unextruded travel hidden inside a "continuous" path.
                    loop.push_back({ j.p, std::clamp(unscale<double>(j.w), min_spacing, max_spacing), line.inset_idx == 0, role });
            if (loop.size() < 2)
                continue;
            if (sparse) {
                // Keep complete rings wherever Orca requests solid material.
                // Remaining space receives closed rectilinear passes below.
                const auto paths = to_paths(loop, h, false);
                Polygons footprint;
                for (const auto &path : paths)
                    path.polygons_covered_by_width(footprint, 0.f);
                const bool solid = !intersection_ex(footprint, solid_regions).empty();
                if (!solid) {
                    append(intentional_void, std::move(footprint));
                    continue;
                }
                if (solid)
                    for (auto &point : loop)
                        point.role = erSolidInfill;
            }
            if (line.is_closed) {
                if (loop.front().point == loop.back().point)
                    loop.pop_back();
                if (loop.size() >= 3)
                    loops.emplace_back(std::move(loop));
            } else {
                strokes.emplace_back(std::move(loop));
            }
        }
    }
    if (m_settings.infill_density > 0. && m_settings.infill_density < 1.) {
        Polygons fixed_footprints;
        for (const auto &loop : loops)
            for (const auto &path : to_paths(loop, h, true))
                path.polygons_covered_by_width(fixed_footprints, 0.f);
        // A strip contributes two long passes. Leave the same space between
        // strips as between those passes, giving spacing / density overall.
        // Clip before tracing to retain closed cycles around holes and branches.
        const auto centers = diff_ex(offset_ex(region, -float(scale_(width * .5))),
                                     offset_ex(fixed_footprints, float(scale_(width * .5))));
        const coord_t gap = scale_(spacing / m_settings.infill_density);
        const coord_t pitch = 2 * gap;
        Polygons bands;
        for (coord_t y = coord_t(std::floor(double(box.min.y()) / double(pitch))) * pitch;
             y < box.max.y(); y += pitch) {
            throw_on_cancel();
            Polygon band;
            band.points = {Point(box.min.x(), y), Point(box.max.x(), y),
                           Point(box.max.x(), y + gap), Point(box.min.x(), y + gap)};
            bands.push_back(std::move(band));
        }
        for (const auto &polygon : to_polygons(intersection_ex(centers, bands))) {
            BeadLoop loop;
            for (const auto &point : polygon.points)
                loop.push_back({point, spacing, false, erInternalInfill});
            if (loop.size() >= 3)
                loops.push_back(std::move(loop));
        }
    }
    const ExPolygons allowed = offset_ex(m_region, float(scaled<double>(m_settings.boundary_tolerance)));
    if (!loops.empty()) {
        BeadLoop route = std::move(loops.front());
        loops.erase(loops.begin());
        bool progress = true;
        while (progress && !loops.empty()) {
            progress = false;
            for (size_t i = 0; i < loops.size(); ++i) {
                throw_on_cancel();
                const double reach = spacing * 3. / std::max(.01, m_settings.infill_density);
                if (join_loop(route, loops[i], allowed, spacing, reach, m_settings, throw_on_cancel)) {
                    loops.erase(loops.begin() + i);
                    progress = true;
                    break;
                }
            }
        }
        bool closed = true;
        if (!m_settings.closed_route && loops.empty() && strokes.size() == 1 &&
            attach_stroke(route, strokes.front(), allowed, spacing * 3., throw_on_cancel)) {
            strokes.clear();
            closed = false;
        }
        result.paths = to_paths(route, h, closed);
        for (const auto &loop : loops)
            append(result.unresolved_paths, to_paths(loop, h, true));
    }
    for (const auto &stroke : strokes)
        append(result.unresolved_paths, to_paths(stroke, h, false));
    result.connected = !result.paths.empty() && (result.unresolved_paths.empty() || m_settings.omit_unreachable);
    for (size_t i = 1; i < result.paths.size(); ++i)
        result.connected = result.connected && result.paths[i - 1].last_point() == result.paths[i].first_point();
    result.widths_valid = std::all_of(result.paths.begin(), result.paths.end(), [&](const auto &path) {
        return path.width >= m_settings.min_width - 1e-6 && path.width <= m_settings.max_width + 1e-6;
    });
    Polygons footprints;
    for (const auto &path : result.paths)
        path.polygons_covered_by_width(footprints, 0.f);
    result.contained = diff_ex(footprints, allowed).empty();
    result.coverage = continuous_coverage(m_region, result.paths);
    if (!intentional_void.empty()) {
        // Count only unprinted intentional voids. Connectors deposited across
        // sparse cells still contribute to actual volume and repeated fill.
        auto voids = intersection_ex(result.coverage.missing, intentional_void);
        result.coverage.intentional_void_area = area_mm2(voids);
        result.coverage.missing = diff_ex(result.coverage.missing, voids);
        result.coverage.missing_area = area_mm2(result.coverage.missing);
    }
    result.reason = !result.connected ? "Unresolved open centerlines or disconnected contour routes" :
                    !result.widths_valid ? "Bead width outside configured limits" :
                    !result.contained ? "Bead footprint outside boundary tolerance" :
                    "Connected region candidate; whole-print motion is not yet verified";
    return result;
}

void ContinuousRegionPlanner::advance(std::chrono::steady_clock::time_point deadline,
                                      const std::function<void()> &throw_on_cancel)
{
    while (!finished() && std::chrono::steady_clock::now() < deadline) {
        throw_on_cancel();
        auto candidate = generate(m_widths[m_next_candidate], throw_on_cancel);
        // Keep feasibility separate from geometric quality. An incomplete path
        // cannot beat a complete feasible route merely by depositing less.
        auto feasible = [](const ContinuousRegionPlan &p) { return p.connected && p.widths_valid && p.contained; };
        if (m_next_candidate == 0 || feasible(candidate) > feasible(m_best) ||
            (feasible(candidate) == feasible(m_best) &&
             candidate.coverage.missing_area * m_settings.missing_weight + candidate.coverage.outside_area + candidate.coverage.excess_area * m_settings.excess_weight <
             m_best.coverage.missing_area * m_settings.missing_weight + m_best.coverage.outside_area + m_best.coverage.excess_area * m_settings.excess_weight))
            m_best = std::move(candidate);
        ++m_next_candidate;
    }
}

} // namespace Slic3r
