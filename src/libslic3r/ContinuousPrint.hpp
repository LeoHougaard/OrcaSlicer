#pragma once

#include "ContinuousExtrusion.hpp"
#include <memory>

namespace Slic3r {
class Print;
class PrintObject;
class PrintObjectConfig;

struct ContinuousLayerRoute {
    ExtrusionPaths paths; // Absolute print Z, object-local XY.
    ContinuousCoverage coverage; // Planar estimate before the layer ramp.
    double print_z = 0.;
    double transition_volume = 0.;
};

struct ContinuousPrintJob {
    std::string signature;
    std::vector<ContinuousRegionPlanner> planners;
    std::vector<ContinuousLayerRoute> layers;
    ContinuousExtrusionSettings settings;
    double elapsed = 0.;
    bool complete = false;
};

bool continuous_print_enabled(const PrintObjectConfig &config);
std::string continuous_print_validation(const Print &print);
void apply_continuous_print(Print &print, const std::function<void()> &throw_on_cancel);

// Rotate closed layer cycles and replace layer travel with a contained,
// extruding connection and gradual Z rise. Throws instead of hiding travel.
std::vector<ContinuousLayerRoute> continuous_join_layers(
    const std::vector<ContinuousRegionPlan> &plans, const std::vector<ExPolygons> &regions,
    const std::vector<double> &print_zs, const ContinuousExtrusionSettings &settings,
    double ramp_length, double max_connection, const std::function<void()> &throw_on_cancel = []{});
} // namespace Slic3r
