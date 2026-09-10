#pragma once

#include "ExPolygon.hpp"
#include "ExtrusionEntity.hpp"

#include <chrono>
#include <functional>
#include <string>

namespace Slic3r {

// Geometry and motion are separate: a nominal G-code E/time is NOT evidence
// that firmware maintains constant filament feed during acceleration.
struct ContinuousExtrusionSettings
{
    double nominal_width = 0.42;
    double min_width = 0.30;
    double max_width = 0.80;
    double layer_height = 0.20;
    double nozzle_diameter = 0.40;
    double filament_diameter = 1.75;
    double filament_speed = 0.50; // Nominal mm of input filament / second.
    double boundary_tolerance = 0.05;
    double resolution = 0.025;
    bool omit_unreachable = false;
    bool closed_route = false;
    double missing_weight = 1.;
    double excess_weight = 1.;
    double infill_density = 1.; // Interior line spacing is bead spacing / density.
    size_t wall_loops = 2;
    ExPolygons solid_regions; // Orca's top, bottom, bridge and solid shell surfaces.
    Points seam_positions; // Preferred wall attachments from Orca's SeamPlacer.
    Points inner_seam_positions;
};

struct ContinuousCoverage
{
    ExPolygons missing;
    ExPolygons outside;
    ExPolygons excess;
    double target_area = 0.;
    double missing_area = 0.;
    double outside_area = 0.;
    double excess_area = 0.;
    double deposited_volume = 0.;
    double intentional_void_area = 0.;

    double error_area() const { return missing_area + outside_area + excess_area; }
};

struct ContinuousRegionPlan
{
    ExtrusionPaths paths;
    ExtrusionPaths unresolved_paths;
    ContinuousCoverage coverage;
    bool connected = false;
    bool widths_valid = false;
    bool contained = false;
    std::string reason;
};

// Effective rectangular footprints use area / height, so their area integral
// equals deposited volume / height. This is a geometric surrogate, not a TPU
// pressure/flow simulation. It counts repeated deposition, not just a union.
ContinuousCoverage continuous_coverage(const ExPolygons &region, const ExtrusionPaths &paths);

// Requested cruise speed only. Actual feed follows firmware acceleration.
double continuous_extrusion_speed(const ContinuousExtrusionSettings &settings, double mm3_per_mm);

class ContinuousRegionPlanner
{
public:
    ContinuousRegionPlanner(ExPolygons region, ContinuousExtrusionSettings settings);

    // A deadline is a checkpoint, not an exception. Candidate state and the best
    // result survive repeated calls. Cancellation still uses the caller's hook.
    void advance(std::chrono::steady_clock::time_point deadline,
                 const std::function<void()> &throw_on_cancel = []{});
    bool finished() const { return m_next_candidate == m_widths.size(); }
    size_t attempts() const { return m_next_candidate; }
    const ContinuousRegionPlan &best() const { return m_best; }

private:
    ContinuousRegionPlan generate(double width, const std::function<void()> &throw_on_cancel) const;
    ContinuousRegionPlan generate_region(const ExPolygons &region, double width,
                                        const std::function<void()> &throw_on_cancel) const;
    ExPolygons m_region;
    ContinuousExtrusionSettings m_settings;
    std::vector<double> m_widths;
    size_t m_next_candidate = 0;
    ContinuousRegionPlan m_best;
};

} // namespace Slic3r
