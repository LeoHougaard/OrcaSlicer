#pragma once

#include "PrintConfig.hpp"

#include <cstddef>
#include <string>
#include <vector>

namespace Slic3r {

class ExPolygon;
class ExtrusionEntityCollection;
class PrintObject;
using ExPolygons = std::vector<ExPolygon>;

struct CBPSettings
{
    bool enabled = false;

    ConstrainedBeadPlannerScope scope = ConstrainedBeadPlannerScope::PerimetersAndInfill;
    ConstrainedBeadPlannerBridgeModeHandling bridge_mode_handling = ConstrainedBeadPlannerBridgeModeHandling::FailUnsupported;

    bool debug = false;
    int max_candidates = 8;
    int max_lookahead_depth = 0;
    int max_backtracks = 0;
    int layer_operation_budget = 20000;

    bool bead_model = true;
    bool use_variable_width = false;
    bool check_containment = false;
    bool check_same_layer_collision = true;
    bool check_centerline_crossing = true;
    bool check_double_back = true;
    bool allow_junction_overlap = true;
    bool check_turns = false;
    bool penalize_sharp_turns = true;
    bool check_dead_ends = false;
    bool check_euler_feasibility = false;
    bool prevent_unreachable_regions = false;
    bool check_support = false;

    double collision_margin = 0.0;
    double coverage_margin = 0.02;
    double min_width = 0.0;
    double max_width = 0.0;
    double min_clearance = 0.0;
    double min_adjacent_overlap = 0.0;
    double max_adjacent_overlap = 0.35;
    double junction_overlap_radius = 0.12;
    double min_segment_length = 0.2;
    double min_turn_radius = 0.0;
    double max_width_change_per_mm = 0.0;

    int beam_width = 1;
    int topology_check_interval = 32;
};

struct CBPPlanResult
{
    bool applied = false;
    std::string reason;
    size_t accepted_entities = 0;
    size_t operation_count = 0;
};

bool constrained_bead_planner_enabled(const PrintObjectConfig &config);
CBPSettings constrained_bead_planner_settings(const PrintObjectConfig &config);

CBPPlanResult constrained_bead_planner_plan_collection(
    const CBPSettings &settings,
    const ExPolygons &printable_region,
    ExtrusionEntityCollection &collection);

void apply_constrained_bead_planner(PrintObject &object);

} // namespace Slic3r
