idf_component_register(
    SRCS "inspection_station.cpp" "platform.cpp" "../tests/logic_compile_checks.cpp"
    INCLUDE_DIRS "."
    REQUIRES esp_wifi esp_event esp_netif esp_timer nvs_flash lwip
)
target_compile_features(${COMPONENT_LIB} PUBLIC cxx_std_17)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror)
