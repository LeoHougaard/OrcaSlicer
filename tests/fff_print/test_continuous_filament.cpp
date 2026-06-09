#include <catch2/catch_all.hpp>

#include "libslic3r/GCode/ContinuousFilament.hpp"
#include "libslic3r/PrintConfig.hpp"

using namespace Slic3r;

static PrintConfig continuous_filament_config(bool relative_e)
{
    PrintConfig config;
    config.use_relative_e_distances.value = relative_e;
    config.continuous_filament_connector_flow_ratio.value = 0.25;
    return config;
}

TEST_CASE("Continuous filament connectors preserve absolute E coordinates", "[ContinuousFilament]")
{
    ContinuousFilament continuous(continuous_filament_config(false));
    const std::string gcode =
        "G1 Z0.2\n"
        "G1 X10 E1\n"
        "G0 X20 F9000\n"
        "G1 X30 E2\n";

    const std::string processed = continuous.process_layer(gcode);

    REQUIRE_THAT(processed, Catch::Matchers::ContainsSubstring("G1 X20.000 Y0.000 Z0.133 E1.25000 F9000"));
    REQUIRE_THAT(processed, !Catch::Matchers::ContainsSubstring("G1 X20.000 Y0.000 Z0.133 E0.25000 F9000"));
}

TEST_CASE("Continuous filament connectors use delta E in relative mode", "[ContinuousFilament]")
{
    ContinuousFilament continuous(continuous_filament_config(true));
    const std::string gcode =
        "G1 Z0.2\n"
        "G1 X10 E1\n"
        "G0 X20 F9000\n"
        "G1 X30 E1\n";

    const std::string processed = continuous.process_layer(gcode);

    REQUIRE_THAT(processed, Catch::Matchers::ContainsSubstring("G1 X20.000 Y0.000 Z0.133 E0.25000 F9000"));
}
