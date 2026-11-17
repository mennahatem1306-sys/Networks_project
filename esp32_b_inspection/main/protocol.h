#pragma once
#include <stddef.h>
#include <stdint.h>

namespace Protocol {
constexpr uint32_t SchemaVersion = 1;
constexpr uint32_t GatewayDeviceId = 0;
constexpr uint32_t InspectionDeviceId = 2;
constexpr size_t MaxJsonBytes = 1024;
constexpr size_t PrefixBytes = 4;
constexpr size_t MaxJobs = 1000;
constexpr size_t MaxConfigCommands = 256;
constexpr uint32_t HeartbeatIntervalMs = 1000;
constexpr uint32_t HeartbeatTimeoutMs = 3000;
constexpr uint32_t ResultRetryIntervalMs = 1000;
constexpr uint8_t MaxResultAttempts = 4;
constexpr uint32_t FrameTimeoutMs = 2000;
constexpr uint32_t MaxInspectionMs = 3600000;
constexpr uint32_t MaxReconnectDelayMs = 10000;
constexpr int32_t ConnectTimeoutMs = 250;
constexpr unsigned ReceiveBudgetBytes = 2048;
}
