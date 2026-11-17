#pragma once
#include <stdint.h>
#include "sdkconfig.h"

inline constexpr char WIFI_SSID[] = CONFIG_STATION_WIFI_SSID;
inline constexpr char WIFI_PASSWORD[] = CONFIG_STATION_WIFI_PASSWORD;
inline constexpr char GATEWAY_HOST[] = CONFIG_STATION_GATEWAY_IPV4;
inline constexpr uint16_t GATEWAY_PORT = CONFIG_STATION_GATEWAY_PORT;
