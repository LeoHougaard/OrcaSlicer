#pragma once

#include "../ExPolygon.hpp"
#include "../ExtrusionEntity.hpp"

namespace Slic3r {

struct ContinuousFilamentPlan
{
    ExtrusionPath path;
    bool          used_fermat_spiral { false };
    bool          has_layer_scarf { false };
    Point         seam_point;
};

struct ContinuousFilamentPlannerParams
{
    coord_t       spacing { 0 };
    double        mm3_per_mm { 0. };
    float         width { 0.f };
    float         height { 0.f };
    coordf_t      layer_z { 0. };
    coordf_t      next_layer_z { 0. };
    bool          enable_layer_scarf { true };
    bool          require_model_above_scarf { true };
    double        layer_scarf_length { 0. };
    coord_t       seam_clearance_radius { 0 };
};

class ContinuousFilamentPlanner
{
public:
    static ContinuousFilamentPlan plan_island(const ExPolygon &island,
                                              const ExPolygon *next_layer_island,
                                              const ContinuousFilamentPlannerParams &params);
};

} // namespace Slic3r
