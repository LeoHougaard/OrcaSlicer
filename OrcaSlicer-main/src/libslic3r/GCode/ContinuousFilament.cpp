#include "ContinuousFilament.hpp"

#include <algorithm>
#include <cmath>
#include <sstream>

namespace Slic3r {

ContinuousFilament::ContinuousFilament(const PrintConfig &config)
    : m_config(config)
{
    m_reader.z() = float(m_config.z_offset);
    m_reader.apply_config(m_config);
}

static std::string format_g1_move(float x, float y, float z, float e, float f)
{
    std::ostringstream out;
    out.setf(std::ios::fixed, std::ios::floatfield);
    out.precision(3);
    out << "G1 X" << x << " Y" << y << " Z" << z;
    out.precision(5);
    out << " E" << e;
    if (f > 0.f) {
        out.precision(0);
        out << " F" << f;
    }
    return out.str();
}

std::string ContinuousFilament::process_layer(const std::string &gcode)
{
    float layer_height = 0.f;
    float z            = m_reader.z();
    bool  set_z        = false;
    bool  layer_started = m_started;
    float extrusion_xy_len = 0.f;
    float extrusion_e      = 0.f;
    float total_xy_len     = 0.f;

    {
        GCodeReader r = m_reader;
        r.parse_buffer(gcode, [&](GCodeReader &reader, const GCodeReader::GCodeLine &line) {
            if (!line.cmd_is("G0") && !line.cmd_is("G1"))
                return;

            const bool has_xy = line.has_x() || line.has_y();
            const float dist_xy = line.dist_XY(reader);
            const bool is_extruding_xy = line.extruding(reader) && dist_xy > EPSILON;

            if (line.has_z() && !has_xy) {
                layer_height += line.dist_Z(reader);
                if (!set_z) {
                    z = line.new_Z(reader);
                    set_z = true;
                }
                return;
            }

            if (is_extruding_xy) {
                layer_started = true;
                total_xy_len += dist_xy;
                extrusion_xy_len += dist_xy;
                extrusion_e += m_config.use_relative_e_distances.value ? line.e() : line.dist_E(reader);
            } else if (layer_started && has_xy && dist_xy > EPSILON && !line.has_e()) {
                total_xy_len += dist_xy;
            }
        });
    }

    z -= layer_height;
    if (total_xy_len <= EPSILON) {
        m_reader.parse_buffer(gcode);
        return gcode;
    }

    const float nominal_e_per_mm = extrusion_xy_len > EPSILON && extrusion_e > 0.f ?
        extrusion_e / extrusion_xy_len :
        0.033f;
    const float connector_ratio = float(std::clamp(m_config.continuous_filament_connector_flow_ratio.value, 0.0, 1.0));
    const float connector_e_per_mm = nominal_e_per_mm * connector_ratio;

    std::string out;
    float len = 0.f;
    m_reader.parse_buffer(gcode, [&](GCodeReader &reader, GCodeReader::GCodeLine line) {
        if (!line.cmd_is("G0") && !line.cmd_is("G1")) {
            out += line.raw() + '\n';
            return;
        }

        const bool has_xy = line.has_x() || line.has_y();
        const float dist_xy = line.dist_XY(reader);

        if (line.retracting(reader) || (line.extruding(reader) && dist_xy <= EPSILON)) {
            // The model body must never stop flowing for retract / unretract-only moves.
            return;
        }

        if (line.has_z() && !has_xy) {
            line.set(Z, z);
            out += line.raw() + '\n';
            return;
        }

        if (dist_xy > EPSILON && line.extruding(reader)) {
            m_started = true;
            len += dist_xy;
            line.set(Z, z + len / total_xy_len * layer_height);
            out += line.raw() + '\n';
            return;
        }

        if (m_started && has_xy && dist_xy > EPSILON && !line.has_e()) {
            len += dist_xy;
            const float new_z = z + len / total_xy_len * layer_height;
            const float e = connector_e_per_mm * dist_xy;
            if (line.cmd_is("G0")) {
                out += format_g1_move(line.new_X(reader), line.new_Y(reader), new_z, e, line.new_F(reader)) + '\n';
            } else {
                line.set(Z, new_z);
                line.set(E, e, 5);
                out += line.raw() + '\n';
            }
            return;
        }

        out += line.raw() + '\n';
    });

    return out;
}

} // namespace Slic3r
