#include "ConstrainedBeadPlanner.hpp"

#include "BoundingBox.hpp"
#include "ClipperUtils.hpp"
#include "Exception.hpp"
#include "ExPolygon.hpp"
#include "ExtrusionEntity.hpp"
#include "ExtrusionEntityCollection.hpp"
#include "Layer.hpp"
#include "Line.hpp"
#include "Print.hpp"
#include "Surface.hpp"

#include <boost/log/trivial.hpp>

#include <algorithm>
#include <cmath>
#include <initializer_list>
#include <limits>
#include <sstream>
#include <utility>

namespace Slic3r {
namespace {

constexpr double cbp_double_back_cos  = -0.70710678118654757; // cos(135 deg)

struct SegmentRecord
{
    Line line;
    BoundingBox bbox;
    float width = 0.f;
    size_t chain_id = 0;
    size_t segment_id = 0;
};

struct ChainRecord
{
    Point first;
    Point last;
    size_t segment_count = 0;
    bool closed = false;
};

struct CandidateGeometry
{
    std::vector<SegmentRecord> segments;
    std::vector<ChainRecord> chains;
    BoundingBox bbox;
    Point first;
    Point last;
    bool empty = true;
    size_t next_chain_id = 0;
};

struct CachedEntity
{
    bool bridge_ready = false;
    bool has_bridge = false;
};

struct CandidateRef
{
    size_t index = 0;
    bool reversed = false;
    double distance2 = 0.0;
};

struct CandidatePrepared
{
    ExtrusionPaths paths;
    CandidateGeometry geometry;
};

static BoundingBox line_bbox(const Line &line)
{
    BoundingBox bbox;
    bbox.merge(line.a);
    bbox.merge(line.b);
    return bbox;
}

static double point_distance2(const Point &a, const Point &b)
{
    return (a - b).cast<double>().squaredNorm();
}

static double normalized_dot(const Line &a, const Line &b)
{
    const Vec2d va = (a.b - a.a).cast<double>();
    const Vec2d vb = (b.b - b.a).cast<double>();
    const double len = va.norm() * vb.norm();
    if (len <= EPSILON)
        return 1.0;
    return va.dot(vb) / len;
}

static size_t endpoint_touch_count(const Line &a, const Line &b, const double radius2)
{
    size_t count = 0;
    count += point_distance2(a.a, b.a) <= radius2 ? 1 : 0;
    count += point_distance2(a.a, b.b) <= radius2 ? 1 : 0;
    count += point_distance2(a.b, b.a) <= radius2 ? 1 : 0;
    count += point_distance2(a.b, b.b) <= radius2 ? 1 : 0;
    return count;
}

static bool single_endpoint_touch(const Line &a, const Line &b, const double radius2)
{
    return endpoint_touch_count(a, b, radius2) == 1;
}

static bool adjacent_in_same_chain(const CandidateGeometry &geometry, const SegmentRecord &a, const SegmentRecord &b)
{
    if (a.chain_id != b.chain_id)
        return false;

    const size_t min_id = std::min(a.segment_id, b.segment_id);
    const size_t max_id = std::max(a.segment_id, b.segment_id);
    if (max_id == min_id + 1)
        return true;

    if (a.chain_id >= geometry.chains.size())
        return false;

    const ChainRecord &chain = geometry.chains[a.chain_id];
    return chain.closed && chain.segment_count > 1 && min_id == 0 && max_id + 1 == chain.segment_count;
}

static bool junction_overlap_allowed(const Line &a, const Line &b, const double radius2)
{
    return single_endpoint_touch(a, b, radius2);
}

static bool intersection_is_allowed_junction(const Line &a, const Line &b, const Point &intersection, const double radius2)
{
    const bool near_a = point_distance2(intersection, a.a) <= radius2 || point_distance2(intersection, a.b) <= radius2;
    const bool near_b = point_distance2(intersection, b.a) <= radius2 || point_distance2(intersection, b.b) <= radius2;
    return near_a && near_b && single_endpoint_touch(a, b, radius2);
}

static double segment_distance2(const Line &a, const Line &b)
{
    Point intersection;
    if (a.intersection(b, &intersection))
        return 0.0;

    return std::min({
        a.distance_to_squared(b.a),
        a.distance_to_squared(b.b),
        b.distance_to_squared(a.a),
        b.distance_to_squared(a.b)
    });
}

static double collision_threshold_scaled(const CBPSettings &settings, const SegmentRecord &a, const SegmentRecord &b)
{
    const double width_a = std::max<double>(a.width, 0.01);
    const double width_b = std::max<double>(b.width, 0.01);
    const double allowed_overlap = settings.max_adjacent_overlap * std::min(width_a, width_b);
    const double threshold = 0.5 * (width_a + width_b) + settings.collision_margin + settings.min_clearance - allowed_overlap;
    return threshold > 0.0 ? scale_(threshold) : 0.0;
}

static bool segment_pair_collides(const CBPSettings &settings, const SegmentRecord &a, const SegmentRecord &b)
{
    const double threshold = collision_threshold_scaled(settings, a, b);
    if (threshold <= 0.0)
        return false;

    BoundingBox bbox_a = a.bbox.inflated(threshold);
    if (!bbox_a.overlap(b.bbox))
        return false;

    const double junction_radius2 = scaled<double>(settings.junction_overlap_radius) * scaled<double>(settings.junction_overlap_radius);
    if (settings.allow_junction_overlap && junction_overlap_allowed(a.line, b.line, junction_radius2))
        return false;

    const double distance2 = segment_distance2(a.line, b.line);
    const double threshold2 = std::max(0.0, threshold * threshold - double(SCALED_EPSILON) * double(SCALED_EPSILON));
    return distance2 < threshold2;
}

static ExtrusionPath make_connector_path(const Point &from, const Point &to, const ExtrusionPath &path_template)
{
    ExtrusionPath connector(path_template.role(), path_template.mm3_per_mm, path_template.width, path_template.height);
    connector.smooth_speed    = path_template.smooth_speed;
    connector.overhang_degree = path_template.overhang_degree;
    connector.curve_degree    = 0;
    connector.z_contoured     = false;

    const coord_t z = path_template.polyline.points.empty() ? 0 : path_template.polyline.points.front().z();
    connector.polyline.points = { Point3(from, z), Point3(to, z) };
    return connector;
}

static void append_continuous_path(ExtrusionPaths &out, ExtrusionPath &&path)
{
    if (path.polyline.points.size() < 2 || path.length() <= 0.0)
        return;

    if (!out.empty() && out.back().last_point() != path.first_point()) {
        ExtrusionPath connector = make_connector_path(out.back().last_point(), path.first_point(), path);
        if (connector.first_point() != connector.last_point())
            out.emplace_back(std::move(connector));
    }

    out.emplace_back(std::move(path));
}

static void add_segment(const ExtrusionPath &path, const Point &a, const Point &b, const size_t chain_id, size_t &segment_id, CandidateGeometry &geometry)
{
    if (a == b)
        return;

    if (geometry.chains.size() <= chain_id)
        geometry.chains.resize(chain_id + 1);

    SegmentRecord segment;
    segment.line = Line(a, b);
    segment.bbox = line_bbox(segment.line);
    segment.width = std::max(path.width, 0.01f);
    segment.chain_id = chain_id;
    segment.segment_id = segment_id++;

    ChainRecord &chain = geometry.chains[chain_id];
    if (chain.segment_count == 0)
        chain.first = a;
    chain.last = b;
    ++chain.segment_count;
    chain.closed = chain.first == chain.last;

    if (geometry.empty) {
        geometry.first = a;
        geometry.empty = false;
    }
    geometry.last = b;
    geometry.bbox.merge(segment.bbox);
    geometry.segments.emplace_back(std::move(segment));
}

static void append_path_segments(const ExtrusionPath &path, const bool reversed, const size_t chain_id, size_t &segment_id, CandidateGeometry &geometry)
{
    const Points3 &points = path.polyline.points;
    if (points.size() < 2)
        return;

    if (reversed) {
        for (size_t i = points.size() - 1; i > 0; --i)
            add_segment(path, points[i].to_point(), points[i - 1].to_point(), chain_id, segment_id, geometry);
    } else {
        for (size_t i = 1; i < points.size(); ++i)
            add_segment(path, points[i - 1].to_point(), points[i].to_point(), chain_id, segment_id, geometry);
    }
}

static void append_oriented_entity_paths(const ExtrusionEntity &entity, const bool reversed, const Point *start_near, ExtrusionPaths &out)
{
    if (const auto *path = dynamic_cast<const ExtrusionPath*>(&entity)) {
        ExtrusionPath copy(*path);
        if (reversed)
            copy.reverse();
        append_continuous_path(out, std::move(copy));
    } else if (const auto *multipath = dynamic_cast<const ExtrusionMultiPath*>(&entity)) {
        if (reversed) {
            for (auto it = multipath->paths.rbegin(); it != multipath->paths.rend(); ++it) {
                ExtrusionPath copy(*it);
                copy.reverse();
                append_continuous_path(out, std::move(copy));
            }
        } else {
            for (const ExtrusionPath &path : multipath->paths) {
                ExtrusionPath copy(path);
                append_continuous_path(out, std::move(copy));
            }
        }
    } else if (const auto *loop = dynamic_cast<const ExtrusionLoop*>(&entity)) {
        ExtrusionLoop copy(*loop);
        if (start_near != nullptr)
            copy.split_at(*start_near, false);
        if (reversed)
            copy.reverse();
        for (const ExtrusionPath &path : copy.paths) {
            ExtrusionPath path_copy(path);
            append_continuous_path(out, std::move(path_copy));
        }
    } else if (const auto *collection = dynamic_cast<const ExtrusionEntityCollection*>(&entity)) {
        bool first_child = true;
        if (reversed) {
            for (auto it = collection->entities.rbegin(); it != collection->entities.rend(); ++it) {
                append_oriented_entity_paths(**it, !(*it)->is_loop(), first_child ? start_near : nullptr, out);
                first_child = false;
            }
        } else {
            for (const ExtrusionEntity *child : collection->entities) {
                append_oriented_entity_paths(*child, false, first_child ? start_near : nullptr, out);
                first_child = false;
            }
        }
    }
}

static ExtrusionPaths make_oriented_entity_paths(const ExtrusionEntity &entity, const bool reversed, const Point *start_near)
{
    ExtrusionPaths paths;
    append_oriented_entity_paths(entity, reversed, start_near, paths);
    return paths;
}

static CandidateGeometry make_paths_geometry(const ExtrusionPaths &paths)
{
    CandidateGeometry geometry;
    size_t segment_id = 0;
    for (const ExtrusionPath &path : paths)
        append_path_segments(path, false, 0, segment_id, geometry);
    return geometry;
}

static Polygons paths_covered_by_width(const ExtrusionPaths &paths, const float scaled_epsilon)
{
    Polygons out;
    for (const ExtrusionPath &path : paths)
        path.polygons_covered_by_width(out, scaled_epsilon);
    return out;
}

static bool entity_has_unsupported_bridge_impl(const ExtrusionEntity &entity)
{
    const ExtrusionRole role = entity.role();
    if (role == erBridgeInfill || role == erInternalBridgeInfill)
        return true;

    if (const auto *path = dynamic_cast<const ExtrusionPath*>(&entity))
        return path->role() == erBridgeInfill || path->role() == erInternalBridgeInfill;
    if (const auto *multipath = dynamic_cast<const ExtrusionMultiPath*>(&entity))
        return std::any_of(multipath->paths.begin(), multipath->paths.end(), [](const ExtrusionPath &path) {
            return path.role() == erBridgeInfill || path.role() == erInternalBridgeInfill;
        });
    if (const auto *loop = dynamic_cast<const ExtrusionLoop*>(&entity))
        return std::any_of(loop->paths.begin(), loop->paths.end(), [](const ExtrusionPath &path) {
            return path.role() == erBridgeInfill || path.role() == erInternalBridgeInfill;
        });
    if (const auto *collection = dynamic_cast<const ExtrusionEntityCollection*>(&entity))
        return std::any_of(collection->entities.begin(), collection->entities.end(), [](const ExtrusionEntity *child) {
            return entity_has_unsupported_bridge_impl(*child);
        });

    return false;
}

static bool width_limits_ok_impl(const ExtrusionEntity &entity, const CBPSettings &settings)
{
    auto width_ok = [&settings](const ExtrusionPath &path) {
        if (path.width <= 0.f)
            return true;
        if (settings.min_width > 0.0 && path.width < settings.min_width)
            return false;
        if (settings.max_width > 0.0 && path.width > settings.max_width)
            return false;
        return true;
    };

    if (const auto *path = dynamic_cast<const ExtrusionPath*>(&entity))
        return width_ok(*path);
    if (const auto *multipath = dynamic_cast<const ExtrusionMultiPath*>(&entity))
        return std::all_of(multipath->paths.begin(), multipath->paths.end(), width_ok);
    if (const auto *loop = dynamic_cast<const ExtrusionLoop*>(&entity))
        return std::all_of(loop->paths.begin(), loop->paths.end(), width_ok);
    if (const auto *collection = dynamic_cast<const ExtrusionEntityCollection*>(&entity))
        return std::all_of(collection->entities.begin(), collection->entities.end(), [&settings](const ExtrusionEntity *child) {
            return width_limits_ok_impl(*child, settings);
        });

    return true;
}

static bool width_limits_ok_paths(const ExtrusionPaths &paths, const CBPSettings &settings)
{
    return std::all_of(paths.begin(), paths.end(), [&settings](const ExtrusionPath &path) {
        if (path.width <= 0.f)
            return true;
        if (settings.min_width > 0.0 && path.width < settings.min_width)
            return false;
        if (settings.max_width > 0.0 && path.width > settings.max_width)
            return false;
        return true;
    });
}

static double nearest_entity_point_distance2(const ExtrusionEntity &entity, const Point &point)
{
    Points points;
    entity.collect_points(points);
    if (points.empty())
        return point_distance2(point, entity.first_point());

    double best = std::numeric_limits<double>::max();
    for (const Point &candidate : points)
        best = std::min(best, point_distance2(point, candidate));
    return best;
}

static double candidate_start_distance2(const ExtrusionEntity &entity, const Point &current, const bool reversed)
{
    if (entity.is_loop() || entity.is_collection())
        return nearest_entity_point_distance2(entity, current);
    return point_distance2(current, reversed ? entity.last_point() : entity.first_point());
}

static bool bridge_mode_rejects_collection(const CBPSettings &settings, ExtrusionEntityCollection &collection, std::vector<CachedEntity> &cache)
{
    if (settings.bridge_mode_handling != ConstrainedBeadPlannerBridgeModeHandling::FailUnsupported)
        return false;

    for (size_t i = 0; i < collection.entities.size(); ++i) {
        CachedEntity &entry = cache[i];
        if (!entry.bridge_ready) {
            entry.has_bridge = entity_has_unsupported_bridge_impl(*collection.entities[i]);
            entry.bridge_ready = true;
        }
        if (entry.has_bridge)
            return true;
    }
    return false;
}

static bool candidate_ref_less(const CandidateRef &lhs, const CandidateRef &rhs)
{
    if (lhs.distance2 != rhs.distance2)
        return lhs.distance2 < rhs.distance2;
    if (lhs.index != rhs.index)
        return lhs.index < rhs.index;
    return !lhs.reversed && rhs.reversed;
}

static bool internal_centerline_ok(const CBPSettings &settings, const CandidateGeometry &geometry, std::string &reason)
{
    const double junction_radius2 = scaled<double>(settings.junction_overlap_radius) * scaled<double>(settings.junction_overlap_radius);

    if (settings.check_turns && settings.min_segment_length > 0.0) {
        const double min_length = scaled<double>(settings.min_segment_length);
        for (const SegmentRecord &segment : geometry.segments) {
            if (segment.line.length() < min_length) {
                reason = "segment shorter than continuous extrusion minimum segment length";
                return false;
            }
        }
    }

    if (settings.check_double_back || settings.check_turns) {
        for (size_t i = 1; i < geometry.segments.size(); ++i) {
            const SegmentRecord &prev = geometry.segments[i - 1];
            const SegmentRecord &next = geometry.segments[i];
            if (prev.chain_id != next.chain_id || prev.segment_id + 1 != next.segment_id)
                continue;

            const double dot = normalized_dot(prev.line, next.line);
            if (settings.check_double_back && dot < cbp_double_back_cos) {
                reason = "candidate doubles back";
                return false;
            }

            if (settings.check_turns && settings.max_width_change_per_mm > 0.0) {
                const double length_mm = std::max(0.001, unscale<double>(next.line.length()));
                const double width_change = std::abs(double(next.width) - double(prev.width)) / length_mm;
                if (width_change > settings.max_width_change_per_mm) {
                    reason = "candidate width changes too quickly";
                    return false;
                }
            }
        }
    }

    if (settings.check_centerline_crossing || settings.check_same_layer_collision) {
        for (size_t i = 0; i < geometry.segments.size(); ++i) {
            for (size_t j = i + 1; j < geometry.segments.size(); ++j) {
                const SegmentRecord &a = geometry.segments[i];
                const SegmentRecord &b = geometry.segments[j];
                const bool adjacent = adjacent_in_same_chain(geometry, a, b);
                if (adjacent && settings.allow_junction_overlap)
                    continue;

                if (settings.check_centerline_crossing) {
                    Point intersection;
                    if (a.line.intersection(b.line, &intersection) &&
                        !(settings.allow_junction_overlap && intersection_is_allowed_junction(a.line, b.line, intersection, junction_radius2))) {
                        reason = "candidate centerline self-crosses";
                        return false;
                    }
                }

                if (settings.bead_model && settings.check_same_layer_collision && segment_pair_collides(settings, a, b)) {
                    reason = "candidate bead footprint self-collides";
                    return false;
                }
            }
        }
    }

    return true;
}

class Planner
{
public:
    Planner(const CBPSettings &settings, const ExPolygons &printable_region, ExtrusionEntityCollection &collection, const bool wrap_commit) :
        m_settings(settings),
        m_printable_region(printable_region),
        m_collection(collection),
        m_cache(collection.entities.size()),
        m_wrap_commit(wrap_commit)
    {
        if (!m_printable_region.empty())
            m_printable_bbox = get_extents(m_printable_region);
    }

    CBPPlanResult run()
    {
        CBPPlanResult result;
        if (!m_settings.enabled || m_collection.entities.empty())
            return result;

        if (bridge_mode_rejects_collection(m_settings, m_collection, m_cache)) {
            return this->fail("unsupported bridge extrusion in continuous extrusion scope");
        }

        if (m_collection.can_sort()) {
            for (const ExtrusionEntity *entity : m_collection.entities) {
                if (!entity->can_sort())
                    return this->fail("collection contains a no-sort child");
            }
        }

        m_remaining.assign(m_collection.entities.size(), true);
        const bool allow_reorder = m_collection.can_sort();
        Point current = m_collection.entities.front()->first_point();
        bool current_valid = false;

        for (size_t step = 0; step < m_collection.entities.size(); ++step) {
            std::vector<CandidateRef> candidates;
            if (allow_reorder)
                candidates = this->candidate_refs(current);
            else
                candidates.push_back({ step, false, 0.0 });

            if (candidates.empty())
                return this->fail("no remaining candidates");

            bool accepted = false;
            CandidateRef chosen;
            CandidatePrepared chosen_prepared;
            std::string rejection_reason;

            for (const CandidateRef &candidate : candidates) {
                if (!this->consume()) {
                    return this->fail("operation budget exceeded");
                }

                CandidatePrepared prepared = this->prepare_candidate(candidate, current_valid ? &current : nullptr);
                std::string reason;
                if (!this->candidate_ok(candidate.index, prepared, reason)) {
                    rejection_reason = reason;
                    continue;
                }

                if (m_settings.check_dead_ends && m_settings.max_lookahead_depth > 0 && !this->lookahead_ok(candidate.index, prepared)) {
                    rejection_reason = "lookahead detected a dead end";
                    continue;
                }

                chosen = candidate;
                chosen_prepared = std::move(prepared);
                accepted = true;
                break;
            }

            if (!accepted)
                return this->fail(rejection_reason.empty() ? "no admissible candidate in bounded set" : rejection_reason);

            m_plan.emplace_back(chosen.index, chosen.reversed);
            m_remaining[chosen.index] = false;
            current = chosen_prepared.geometry.empty ? current : chosen_prepared.geometry.last;
            current_valid = !chosen_prepared.geometry.empty;
            m_accepted.insert(m_accepted.end(), chosen_prepared.geometry.segments.begin(), chosen_prepared.geometry.segments.end());
            std::move(chosen_prepared.paths.begin(), chosen_prepared.paths.end(), std::back_inserter(m_committed_paths));
        }

        this->commit_plan();
        result.applied = true;
        result.accepted_entities = m_plan.size();
        result.operation_count = m_operations;
        return result;
    }

private:
    bool consume(const size_t amount = 1)
    {
        if (m_settings.layer_operation_budget > 0 &&
            m_operations + amount > size_t(m_settings.layer_operation_budget))
            return false;
        m_operations += amount;
        return true;
    }

    std::vector<CandidateRef> candidate_refs(const Point &current) const
    {
        std::vector<CandidateRef> candidates;
        candidates.reserve(m_remaining.size() * 2);

        for (size_t i = 0; i < m_remaining.size(); ++i) {
            if (!m_remaining[i])
                continue;

            const ExtrusionEntity *entity = m_collection.entities[i];
            candidates.push_back({ i, false, candidate_start_distance2(*entity, current, false) });
            if (entity->can_reverse() && entity->first_point() != entity->last_point())
                candidates.push_back({ i, true, candidate_start_distance2(*entity, current, true) });
        }

        const size_t limit = std::min<size_t>(candidates.size(), std::max(1, m_settings.max_candidates));
        if (limit < candidates.size()) {
            std::partial_sort(candidates.begin(), candidates.begin() + limit, candidates.end(), candidate_ref_less);
            candidates.resize(limit);
        } else {
            std::sort(candidates.begin(), candidates.end(), candidate_ref_less);
        }

        return candidates;
    }

    CandidatePrepared prepare_candidate(const CandidateRef &candidate, const Point *current) const
    {
        CandidatePrepared prepared;
        prepared.paths = make_oriented_entity_paths(*m_collection.entities[candidate.index], candidate.reversed, current);

        if (current != nullptr && !prepared.paths.empty() && *current != prepared.paths.front().first_point()) {
            ExtrusionPath connector = make_connector_path(*current, prepared.paths.front().first_point(), prepared.paths.front());
            if (connector.first_point() != connector.last_point())
                prepared.paths.insert(prepared.paths.begin(), std::move(connector));
        }

        prepared.geometry = make_paths_geometry(prepared.paths);
        return prepared;
    }

    bool footprint_within_printable(const ExtrusionPaths &paths, std::string &reason)
    {
        if (!m_settings.bead_model || !m_settings.check_containment || m_printable_region.empty())
            return true;

        Polygons footprint = paths_covered_by_width(paths, float(scale_(m_settings.collision_margin)));
        if (footprint.empty())
            return true;

        const BoundingBox footprint_bbox = get_extents(footprint);
        if (m_printable_bbox.defined && footprint_bbox.defined && !m_printable_bbox.contains(footprint_bbox)) {
            reason = "candidate footprint leaves printable bbox";
            return false;
        }

        if (!this->consume()) {
            reason = "operation budget exceeded";
            return false;
        }

        if (!diff_ex(footprint, m_printable_region).empty()) {
            reason = "candidate footprint leaves printable region";
            return false;
        }

        return true;
    }

    bool segment_pair_ok(const SegmentRecord &segment, const SegmentRecord &accepted, std::string &reason) const
    {
        const double junction_radius2 = scaled<double>(m_settings.junction_overlap_radius) * scaled<double>(m_settings.junction_overlap_radius);

        if (m_settings.check_double_back &&
            single_endpoint_touch(segment.line, accepted.line, junction_radius2) &&
            normalized_dot(segment.line, accepted.line) < cbp_double_back_cos) {
            reason = "candidate doubles back over accepted extrusion";
            return false;
        }

        if (m_settings.check_centerline_crossing) {
            Point intersection;
            if (segment.line.intersection(accepted.line, &intersection) &&
                !(m_settings.allow_junction_overlap && intersection_is_allowed_junction(segment.line, accepted.line, intersection, junction_radius2))) {
                reason = "candidate centerline crosses accepted extrusion";
                return false;
            }
        }

        if (m_settings.bead_model && m_settings.check_same_layer_collision && segment_pair_collides(m_settings, segment, accepted)) {
            reason = "candidate bead footprint collides with accepted extrusion";
            return false;
        }

        return true;
    }

    bool candidate_ok(const size_t index, const CandidatePrepared &prepared, std::string &reason)
    {
        const CandidateGeometry &geometry = prepared.geometry;
        if (geometry.empty) {
            reason = "candidate has no extrusion segments";
            return false;
        }

        if (!width_limits_ok_impl(*m_collection.entities[index], m_settings) ||
            !width_limits_ok_paths(prepared.paths, m_settings)) {
            reason = "candidate bead width outside continuous extrusion limits";
            return false;
        }

        if (!internal_centerline_ok(m_settings, geometry, reason))
            return false;

        if (!this->footprint_within_printable(prepared.paths, reason))
            return false;

        for (const SegmentRecord &segment : geometry.segments) {
            for (const SegmentRecord &accepted : m_accepted) {
                if (!this->consume()) {
                    reason = "operation budget exceeded";
                    return false;
                }

                if (!this->segment_pair_ok(segment, accepted, reason))
                    return false;
            }
        }

        return true;
    }

    bool lookahead_ok(const size_t chosen_index, const CandidatePrepared &chosen_prepared)
    {
        if (m_plan.size() + 1 == m_collection.entities.size())
            return true;

        std::vector<SegmentRecord> saved_accepted = m_accepted;
        saved_accepted.insert(saved_accepted.end(), chosen_prepared.geometry.segments.begin(), chosen_prepared.geometry.segments.end());

        const Point current = chosen_prepared.geometry.empty ? m_collection.entities[chosen_index]->last_point() : chosen_prepared.geometry.last;
        std::vector<CandidateRef> candidates;
        candidates.reserve(m_remaining.size() * 2);
        for (size_t i = 0; i < m_remaining.size(); ++i) {
            if (!m_remaining[i] || i == chosen_index)
                continue;
            const ExtrusionEntity *entity = m_collection.entities[i];
            candidates.push_back({ i, false, candidate_start_distance2(*entity, current, false) });
            if (entity->can_reverse() && entity->first_point() != entity->last_point())
                candidates.push_back({ i, true, candidate_start_distance2(*entity, current, true) });
        }
        const size_t limit = std::min<size_t>(candidates.size(), std::max(1, m_settings.max_candidates));
        if (limit < candidates.size()) {
            std::partial_sort(candidates.begin(), candidates.begin() + limit, candidates.end(), candidate_ref_less);
            candidates.resize(limit);
        } else {
            std::sort(candidates.begin(), candidates.end(), candidate_ref_less);
        }

        for (size_t i = 0; i < limit; ++i) {
            if (!this->consume())
                return false;
            CandidatePrepared prepared = this->prepare_candidate(candidates[i], &current);
            std::string reason;
            std::vector<SegmentRecord> original_accepted;
            original_accepted.swap(m_accepted);
            m_accepted = saved_accepted;
            const bool ok = this->candidate_ok(candidates[i].index, prepared, reason);
            m_accepted.swap(original_accepted);
            if (ok)
                return true;
        }

        return false;
    }

    CBPPlanResult fail(const std::string &reason) const
    {
        const std::string message = "Continuous extrusion failed: " + reason;
        BOOST_LOG_TRIVIAL(error) << message;
        throw SlicingError(message);
    }

    void commit_plan()
    {
        if (m_committed_paths.empty())
            return;

        auto *multipath = new ExtrusionMultiPath();
        multipath->paths = std::move(m_committed_paths);

        m_collection.clear();
        if (m_wrap_commit) {
            // LayerRegion perimeter/fill containers store top-level island collections.
            // Keep that shape so downstream G-code grouping can safely inspect islands,
            // while the island itself contains the single continuous extrusion path.
            auto *island = new ExtrusionEntityCollection();
            island->entities.emplace_back(multipath);
            island->no_sort = false;
            m_collection.entities.emplace_back(island);
        } else {
            m_collection.entities.emplace_back(multipath);
        }
        m_collection.no_sort = false;
    }

    const CBPSettings &m_settings;
    const ExPolygons &m_printable_region;
    ExtrusionEntityCollection &m_collection;
    std::vector<CachedEntity> m_cache;
    BoundingBox m_printable_bbox;
    std::vector<bool> m_remaining;
    std::vector<std::pair<size_t, bool>> m_plan;
    ExtrusionPaths m_committed_paths;
    std::vector<SegmentRecord> m_accepted;
    size_t m_operations = 0;
    bool m_wrap_commit = true;
};

static bool process_perimeters(const CBPSettings &settings)
{
    return settings.scope == ConstrainedBeadPlannerScope::PerimetersOnly ||
           settings.scope == ConstrainedBeadPlannerScope::PerimetersAndInfill ||
           settings.scope == ConstrainedBeadPlannerScope::AllSupportedRoles;
}

static bool process_infill(const CBPSettings &settings)
{
    return settings.scope == ConstrainedBeadPlannerScope::InfillOnly ||
           settings.scope == ConstrainedBeadPlannerScope::PerimetersAndInfill ||
           settings.scope == ConstrainedBeadPlannerScope::AllSupportedRoles;
}

static ExPolygons printable_region_for(const LayerRegion &layerm, const bool infill, const CBPSettings &settings)
{
    ExPolygons region;
    if (infill && !layerm.fill_expolygons.empty())
        region = layerm.fill_expolygons;
    else
        region = to_expolygons(layerm.slices.surfaces);

    if (!region.empty() && settings.coverage_margin > 0.0)
        region = offset_ex(region, float(scale_(settings.coverage_margin)));
    return region;
}

static bool collection_contains_only_islands(const ExtrusionEntityCollection &collection)
{
    return !collection.entities.empty() &&
           std::all_of(collection.entities.begin(), collection.entities.end(), [](const ExtrusionEntity *entity) {
               return dynamic_cast<const ExtrusionEntityCollection*>(entity) != nullptr;
           });
}

static CBPPlanResult plan_collection_impl(
    const CBPSettings &settings,
    const ExPolygons &printable_region,
    ExtrusionEntityCollection &collection,
    const bool wrap_commit)
{
    Planner planner(settings, printable_region, collection, wrap_commit);
    return planner.run();
}

static CBPPlanResult plan_flattened_islands_impl(
    const CBPSettings &settings,
    const ExPolygons &printable_region,
    ExtrusionEntityCollection &collection)
{
    ExtrusionEntityCollection flat;

    for (const ExtrusionEntity *entity : collection.entities) {
        const auto *island = dynamic_cast<const ExtrusionEntityCollection*>(entity);
        if (island == nullptr)
            return plan_collection_impl(settings, printable_region, collection, true);

        for (const ExtrusionEntity *child : island->entities)
            flat.entities.emplace_back(child->clone());
    }

    CBPPlanResult result = plan_collection_impl(settings, printable_region, flat, true);
    if (!result.applied)
        return result;

    collection.clear();
    collection.entities = std::move(flat.entities);
    collection.no_sort = false;
    return result;
}

} // namespace

bool constrained_bead_planner_enabled(const PrintObjectConfig &config)
{
    return config.continuous_extrusion.value ||
           config.slicing_mode.value == SlicingMode::ConstrainedBeadPlanner;
}

CBPSettings constrained_bead_planner_settings(const PrintObjectConfig &config)
{
    CBPSettings settings;
    settings.enabled = constrained_bead_planner_enabled(config);
    settings.scope = config.cbp_scope.value;
    settings.bridge_mode_handling = config.cbp_bridge_mode_handling.value;
    settings.debug = config.cbp_debug.value;
    settings.max_candidates = std::max(1, config.cbp_max_candidates.value);
    settings.max_lookahead_depth = std::max(0, config.cbp_max_lookahead_depth.value);
    settings.max_backtracks = std::max(0, config.cbp_max_backtracks.value);
    settings.layer_operation_budget = std::max(1, config.cbp_layer_operation_budget.value);
    settings.bead_model = config.cbp_bead_model.value;
    settings.collision_margin = std::max(0.0, config.cbp_collision_margin.value);
    settings.coverage_margin = std::max(0.0, config.cbp_coverage_margin.value);
    settings.use_variable_width = config.cbp_use_variable_width.value;
    settings.min_width = std::max(0.0, config.cbp_min_width.value);
    settings.max_width = std::max(0.0, config.cbp_max_width.value);
    settings.check_containment = config.cbp_check_containment.value;
    settings.check_same_layer_collision = config.cbp_check_same_layer_collision.value;
    settings.check_centerline_crossing = config.cbp_check_centerline_crossing.value;
    settings.check_double_back = config.cbp_check_double_back.value;
    settings.min_clearance = std::max(0.0, config.cbp_min_clearance.value);
    settings.min_adjacent_overlap = std::max(0.0, config.cbp_min_adjacent_overlap.value / 100.0);
    settings.max_adjacent_overlap = std::max(0.0, config.cbp_max_adjacent_overlap.value / 100.0);
    settings.allow_junction_overlap = config.cbp_allow_junction_overlap.value;
    settings.junction_overlap_radius = std::max(0.0, config.cbp_junction_overlap_radius.value);
    settings.check_turns = config.cbp_check_turns.value;
    settings.min_segment_length = std::max(0.0, config.cbp_min_segment_length.value);
    settings.min_turn_radius = std::max(0.0, config.cbp_min_turn_radius.value);
    settings.max_width_change_per_mm = std::max(0.0, config.cbp_max_width_change_per_mm.value);
    settings.penalize_sharp_turns = config.cbp_penalize_sharp_turns.value;
    settings.check_dead_ends = config.cbp_check_dead_ends.value;
    settings.check_euler_feasibility = config.cbp_check_euler_feasibility.value;
    settings.prevent_unreachable_regions = config.cbp_prevent_unreachable_regions.value;
    settings.beam_width = std::max(1, config.cbp_beam_width.value);
    settings.topology_check_interval = std::max(1, config.cbp_topology_check_interval.value);
    settings.check_support = config.cbp_check_support.value;
    return settings;
}

CBPPlanResult constrained_bead_planner_plan_collection(
    const CBPSettings &settings,
    const ExPolygons &printable_region,
    ExtrusionEntityCollection &collection)
{
    if (collection_contains_only_islands(collection))
        return plan_flattened_islands_impl(settings, printable_region, collection);
    return plan_collection_impl(settings, printable_region, collection, true);
}

void apply_constrained_bead_planner(PrintObject &object)
{
    CBPSettings settings = constrained_bead_planner_settings(object.config());
    if (!settings.enabled)
        return;

    for (Layer *layer : object.layers()) {
        for (LayerRegion *layerm : layer->regions()) {
            if (process_perimeters(settings)) {
                ExPolygons printable = printable_region_for(*layerm, false, settings);
                CBPPlanResult result = constrained_bead_planner_plan_collection(settings, printable, layerm->perimeters);
                if (result.applied)
                    BOOST_LOG_TRIVIAL(info) << "Continuous extrusion perimeters layer " << layer->id() << ": accepted "
                                            << result.accepted_entities << ", operations "
                                            << result.operation_count << ", reason: " << result.reason;
            }

            if (process_infill(settings)) {
                ExPolygons printable = printable_region_for(*layerm, true, settings);
                CBPPlanResult result = constrained_bead_planner_plan_collection(settings, printable, layerm->fills);
                if (result.applied)
                    BOOST_LOG_TRIVIAL(info) << "Continuous extrusion infill layer " << layer->id() << ": accepted "
                                            << result.accepted_entities << ", operations "
                                            << result.operation_count << ", reason: " << result.reason;
            }
        }
    }
}

} // namespace Slic3r
