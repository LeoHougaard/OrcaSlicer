#include "ContinuousFilamentPlanner.hpp"

#include "../ClipperUtils.hpp"
#include "../Exception.hpp"
#include "../I18N.hpp"

#include <algorithm>
#include <limits>
#include <numeric>

namespace Slic3r {
namespace {

struct ContourNode
{
    size_t  id { 0 };
    size_t  level { 0 };
    size_t  index_at_level { 0 };
    Polygon polygon;
};

struct ContourEdge
{
    size_t a { 0 };
    size_t b { 0 };
    double weight { 0. };
    Point  connection_a;
    Point  connection_b;
};

struct DisjointSet
{
    std::vector<size_t> parent;

    explicit DisjointSet(size_t n) : parent(n)
    {
        std::iota(parent.begin(), parent.end(), size_t(0));
    }

    size_t find(size_t v)
    {
        while (parent[v] != v) {
            parent[v] = parent[parent[v]];
            v = parent[v];
        }
        return v;
    }

    bool unite(size_t a, size_t b)
    {
        a = find(a);
        b = find(b);
        if (a == b)
            return false;
        parent[b] = a;
        return true;
    }
};

static void append_point(Points &out, const Point &point)
{
    if (out.empty() || out.back() != point)
        out.push_back(point);
}

static void remove_neighbor_duplicates(Points &points)
{
    points.erase(std::unique(points.begin(), points.end()), points.end());
}

static double max_segment_length(const Points &points)
{
    double out = 0.;
    for (size_t idx = 1; idx < points.size(); ++idx)
        out = std::max(out, (points[idx] - points[idx - 1]).cast<double>().norm());
    return out;
}

static bool all_segments_inside(const Points &points, const ExPolygon &island)
{
    for (size_t idx = 1; idx < points.size(); ++idx)
        if (!island.contains(Line(points[idx - 1], points[idx])))
            return false;
    return true;
}

static size_t closest_vertex_idx(const Polygon &polygon, const Point &point)
{
    size_t best_idx = 0;
    double best_dist = std::numeric_limits<double>::max();
    for (size_t idx = 0; idx < polygon.points.size(); ++idx) {
        const double dist = (polygon.points[idx] - point).cast<double>().squaredNorm();
        if (dist < best_dist) {
            best_dist = dist;
            best_idx = idx;
        }
    }
    return best_idx;
}

static Point stable_seam_anchor(const Polygon &polygon)
{
    if (polygon.points.empty())
        return {};

    Point best = polygon.points.front();
    for (const Point &point : polygon.points)
        if (point.x() > best.x() || (point.x() == best.x() && point.y() < best.y()))
            best = point;
    return best;
}

static Point closest_point_on_polygon(const Polygon &polygon, const Point &point)
{
    if (polygon.points.empty())
        return point;
    return polygon.point_projection(point);
}

static void append_ring_arc(Points &out, const Polygon &polygon, size_t begin_idx, size_t end_idx, bool forward)
{
    if (polygon.points.empty())
        return;

    const size_t n = polygon.points.size();
    size_t idx = begin_idx % n;
    append_point(out, polygon.points[idx]);
    while (idx != end_idx % n) {
        idx = forward ? (idx + 1) % n : (idx + n - 1) % n;
        append_point(out, polygon.points[idx]);
    }
}

static size_t closest_vertex_on_arc_idx(const Polygon &polygon, size_t begin_idx, size_t end_idx, bool forward, const Point &point)
{
    if (polygon.points.empty())
        return 0;

    const size_t n = polygon.points.size();
    size_t idx = begin_idx % n;
    size_t best_idx = idx;
    double best_dist = (polygon.points[idx] - point).cast<double>().squaredNorm();
    while (idx != end_idx % n) {
        idx = forward ? (idx + 1) % n : (idx + n - 1) % n;
        const double dist = (polygon.points[idx] - point).cast<double>().squaredNorm();
        if (dist < best_dist) {
            best_dist = dist;
            best_idx = idx;
        }
    }
    return best_idx;
}

static void append_nearly_full_ring(Points &out, const Polygon &polygon, const Point &start_point)
{
    if (polygon.points.size() < 2)
        return;

    const size_t n = polygon.points.size();
    const size_t start_idx = closest_vertex_idx(polygon, start_point);
    const size_t end_idx = (start_idx + n - 1) % n;
    append_ring_arc(out, polygon, start_idx, end_idx, true);
}

static size_t advance_idx(const Polygon &polygon, size_t idx, double distance)
{
    if (polygon.points.empty() || distance <= 0.)
        return idx;

    const size_t n = polygon.points.size();
    double walked = 0.;
    size_t current = idx % n;
    while (walked < distance) {
        const size_t next = (current + 1) % n;
        walked += (polygon.points[next] - polygon.points[current]).cast<double>().norm();
        current = next;
        if (current == idx % n)
            break;
    }
    return current;
}

static std::vector<Point> sampled_points(const Polygon &polygon, size_t max_samples)
{
    std::vector<Point> out;
    if (polygon.points.empty())
        return out;

    const size_t step = std::max<size_t>(1, polygon.points.size() / std::max<size_t>(1, max_samples));
    for (size_t idx = 0; idx < polygon.points.size(); idx += step)
        out.push_back(polygon.points[idx]);
    return out;
}

static double sampled_distance_sq(const std::vector<Point> &samples, const Point &point)
{
    double best = std::numeric_limits<double>::max();
    for (const Point &sample : samples)
        best = std::min(best, (sample - point).cast<double>().squaredNorm());
    return best;
}

static std::vector<std::vector<size_t>> build_levels(const ExPolygon &island, coord_t spacing, std::vector<ContourNode> &nodes)
{
    std::vector<std::vector<size_t>> levels;
    if (spacing <= 0)
        return levels;

    ExPolygons current { island };
    for (size_t level = 0; level < 10000; ++level) {
        current = offset_ex(current, level == 0 ? -0.5f * float(spacing) : -float(spacing), jtRound, SCALED_RESOLUTION);
        if (current.empty())
            break;

        std::vector<size_t> level_nodes;
        size_t index_at_level = 0;
        for (const ExPolygon &expoly : current) {
            if (expoly.contour.is_valid()) {
                ContourNode node;
                node.id = nodes.size();
                node.level = level;
                node.index_at_level = index_at_level++;
                node.polygon = expoly.contour;
                node.polygon.make_counter_clockwise();
                node.polygon.densify(float(spacing));
                nodes.push_back(std::move(node));
                level_nodes.push_back(nodes.back().id);
            }
            for (Polygon hole : expoly.holes) {
                if (!hole.is_valid())
                    continue;
                ContourNode node;
                node.id = nodes.size();
                node.level = level;
                node.index_at_level = index_at_level++;
                node.polygon = std::move(hole);
                node.polygon.make_clockwise();
                node.polygon.densify(float(spacing));
                nodes.push_back(std::move(node));
                level_nodes.push_back(nodes.back().id);
            }
        }

        if (level_nodes.empty())
            break;
        levels.push_back(std::move(level_nodes));
    }

    return levels;
}

static bool approximate_connection_segment(const ContourNode &outer,
                                           const ContourNode &inner,
                                           const std::vector<Point> &inner_samples,
                                           ContourEdge &edge)
{
    if (outer.polygon.points.empty() || inner.polygon.points.empty())
        return false;

    Point best_outer = outer.polygon.points.front();
    Point best_inner = closest_point_on_polygon(inner.polygon, best_outer);
    double best_dist = std::numeric_limits<double>::max();

    const size_t step = std::max<size_t>(1, outer.polygon.points.size() / 48);
    for (size_t idx = 0; idx < outer.polygon.points.size(); idx += step) {
        const Point &p = outer.polygon.points[idx];
        const double nearest_dist = sampled_distance_sq(inner_samples, p);
        const Point projected = closest_point_on_polygon(inner.polygon, p);
        const double exact_dist = (projected - p).cast<double>().squaredNorm();
        if (exact_dist < best_dist) {
            best_dist = nearest_dist;
            best_outer = p;
            best_inner = projected;
        }
    }

    edge.a = outer.id;
    edge.b = inner.id;
    edge.weight = std::sqrt(best_dist);
    edge.connection_a = best_outer;
    edge.connection_b = best_inner;
    return true;
}

static std::vector<ContourEdge> build_spiral_contour_mst(const std::vector<std::vector<size_t>> &levels,
                                                         const std::vector<ContourNode> &nodes)
{
    std::vector<ContourEdge> graph_edges;
    for (size_t level = 0; level + 1 < levels.size(); ++level) {
        std::vector<std::vector<Point>> inner_samples;
        inner_samples.reserve(levels[level + 1].size());
        for (size_t inner_id : levels[level + 1])
            inner_samples.push_back(sampled_points(nodes[inner_id].polygon, 64));

        for (size_t outer_id : levels[level]) {
            for (size_t inner_idx = 0; inner_idx < levels[level + 1].size(); ++inner_idx) {
                const size_t inner_id = levels[level + 1][inner_idx];
                ContourEdge edge;
                if (approximate_connection_segment(nodes[outer_id], nodes[inner_id], inner_samples[inner_idx], edge))
                    graph_edges.push_back(edge);
            }
        }
    }

    std::sort(graph_edges.begin(), graph_edges.end(), [](const ContourEdge &a, const ContourEdge &b) {
        return a.weight < b.weight;
    });

    DisjointSet dsu(nodes.size());
    std::vector<ContourEdge> mst;
    for (const ContourEdge &edge : graph_edges)
        if (dsu.unite(edge.a, edge.b))
            mst.push_back(edge);

    return mst;
}

static Points build_connected_fermat_polyline(const std::vector<std::vector<size_t>> &levels,
                                              const std::vector<ContourNode> &nodes,
                                              const std::vector<ContourEdge> &mst,
                                              coord_t spacing)
{
    Points out;
    if (levels.empty())
        return out;

    // Follow one parent/child chain. Each contour is split into two
    // complementary arcs. The inward pass uses the first arc and leaves the
    // second arc unused; after the innermost contour is reached, the outward
    // pass traverses those unused arcs in reverse order. This is the key Fermat
    // idea from the paper: do not consume a whole contour while spiraling in,
    // because the return arm needs its own lane.
    (void)mst;

    struct FermatArc {
        size_t node_id { 0 };
        size_t seam_idx { 0 };
        size_t inward_exit_idx { 0 };
        size_t outward_entry_idx { 0 };
        Point  seam;
        Point  inward_exit;
        Point  outward_entry;
    };

    std::vector<FermatArc> arcs;
    arcs.reserve(levels.size());

    std::vector<size_t> chain;
    chain.reserve(levels.size());
    for (const std::vector<size_t> &level : levels)
        chain.push_back(level.front());

    const Point seam_anchor = stable_seam_anchor(nodes[chain.front()].polygon);
    for (size_t level = 0; level < levels.size(); ++level) {
        const size_t current_id = chain[level];
        const ContourNode &node = nodes[current_id];
        if (node.polygon.points.size() < 3)
            break;

        const size_t seam_idx = closest_vertex_idx(node.polygon, seam_anchor);
        const double loop_len = node.polygon.length();
        const double seam_window = std::min<double>(std::max<double>(spacing * 1.25, SCALED_EPSILON), loop_len * 0.25);
        const size_t outward_entry_idx = advance_idx(node.polygon, seam_idx, seam_window);
        const size_t inward_exit_idx = advance_idx(node.polygon, seam_idx, std::max(seam_window, loop_len - seam_window));

        // Paper notation: the inward reroute starts at B(p), while the outward
        // reroute returns through N(p). Keeping those points about one extrusion
        // spacing apart prevents the two arms from sharing the same printable
        // lane. The reserved interval is intentionally small; the inward arm
        // should consume nearly all of each iso-contour, leaving only the local
        // N(p)->pout window for the outward return.
        append_ring_arc(out, node.polygon, seam_idx, inward_exit_idx, true);

        FermatArc arc;
        arc.node_id = current_id;
        arc.seam_idx = seam_idx;
        arc.inward_exit_idx = inward_exit_idx;
        arc.outward_entry_idx = outward_entry_idx;
        arc.seam = node.polygon.points[seam_idx];
        arc.inward_exit = node.polygon.points[inward_exit_idx];
        arc.outward_entry = node.polygon.points[outward_entry_idx];
        arcs.push_back(arc);

        if (level + 1 >= levels.size())
            break;

        const ContourNode &next_node = nodes[chain[level + 1]];
        append_point(out, next_node.polygon.points[closest_vertex_idx(next_node.polygon, seam_anchor)]);
    }

    if (!arcs.empty())
        append_point(out, arcs.back().outward_entry);

    for (auto it = arcs.rbegin(); it != arcs.rend(); ++it) {
        const ContourNode &node = nodes[it->node_id];
        append_point(out, it->outward_entry);
        append_ring_arc(out, node.polygon, it->outward_entry_idx, it->seam_idx, true);

        if (it + 1 != arcs.rend())
            append_point(out, (it + 1)->outward_entry);
    }

    remove_neighbor_duplicates(out);
    return out;
}

static bool valid_model_above(const Point &point, const ExPolygon &island, const ExPolygon *next_layer_island, coord_t clearance_radius)
{
    if (!island.contains(point))
        return false;
    if (next_layer_island == nullptr)
        return false;
    if (!next_layer_island->contains(point))
        return false;
    if (clearance_radius <= 0)
        return true;

    return next_layer_island->contains(Point(point.x() - clearance_radius, point.y() - clearance_radius)) &&
           next_layer_island->contains(Point(point.x() + clearance_radius, point.y() - clearance_radius)) &&
           next_layer_island->contains(Point(point.x() + clearance_radius, point.y() + clearance_radius)) &&
           next_layer_island->contains(Point(point.x() - clearance_radius, point.y() + clearance_radius));
}

} // namespace

ContinuousFilamentPlan ContinuousFilamentPlanner::plan_island(const ExPolygon &island,
                                                              const ExPolygon *next_layer_island,
                                                              const ContinuousFilamentPlannerParams &params)
{
    ContinuousFilamentPlan plan;
    plan.path = ExtrusionPath(erInternalInfill, params.mm3_per_mm, params.width, params.height);

    std::vector<ContourNode> nodes;
    const std::vector<std::vector<size_t>> levels = build_levels(island, params.spacing, nodes);
    if (levels.empty())
        throw Slic3r::SlicingError(_u8L("Continuous filament mode could not generate Fermat iso-contours for this island."));
    Points points = build_connected_fermat_polyline(levels, nodes, {}, params.spacing);
    if (points.size() < 2)
        throw Slic3r::SlicingError(_u8L("Continuous filament mode could not build a connected Fermat spiral path."));

    plan.used_fermat_spiral = true;
    plan.seam_point = points.front();

    if (params.enable_layer_scarf && params.next_layer_z > params.layer_z &&
        (!params.require_model_above_scarf || valid_model_above(plan.seam_point, island, next_layer_island, params.seam_clearance_radius))) {
        plan.has_layer_scarf = true;
    }

    plan.path.polyline = Polyline3(Polyline(std::move(points)));
    return plan;
}

} // namespace Slic3r
