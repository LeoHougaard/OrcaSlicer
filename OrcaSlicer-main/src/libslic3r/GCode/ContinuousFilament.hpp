#ifndef slic3r_ContinuousFilament_hpp_
#define slic3r_ContinuousFilament_hpp_

#include "../GCodeReader.hpp"
#include "../libslic3r.h"

namespace Slic3r {

class ContinuousFilament
{
public:
    explicit ContinuousFilament(const PrintConfig &config);

    std::string process_layer(const std::string &gcode);

private:
    const PrintConfig &m_config;
    GCodeReader       m_reader;
    bool              m_started { false };
};

} // namespace Slic3r

#endif // slic3r_ContinuousFilament_hpp_
