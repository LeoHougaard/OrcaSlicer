#include "ContinuousFilament.hpp"

namespace Slic3r {

ContinuousFilament::ContinuousFilament(const PrintConfig &config)
    : m_config(config)
{
    m_reader.z() = float(m_config.z_offset);
    m_reader.apply_config(m_config);
}

std::string ContinuousFilament::process_layer(const std::string &gcode)
{
    // Continuous filament planning is performed before G-code emission in GCode::process_layer().
    // Keep this filter as a compatibility no-op so the existing pipeline wiring still works.
    m_reader.parse_buffer(gcode);
    return gcode;
}

} // namespace Slic3r
