#pragma once
#include <stddef.h>
#include <stdint.h>

namespace mct431 {
constexpr uint8_t kDeviceId = 1;
constexpr uint8_t kGatewayId = 0;
constexpr uint32_t kBaud = 115200;
constexpr uint16_t kProductionTestId = 0;
constexpr size_t kJsonMax = 512;
constexpr size_t kRecordMax = kJsonMax + 10;
constexpr size_t kEncodedMax = 2 * kRecordMax + 2;
constexpr uint32_t kHeartbeatMs = 1000;
constexpr uint32_t kHeartbeatLossMs = 3000;
constexpr uint32_t kDeliveryTimeoutMs = 1000;
constexpr uint8_t kMaxRetries = 3;
constexpr uint32_t kTargetMax = 1000;
constexpr uint32_t kCycleMaxMs = 3600000;
// Never evict accepted IDs within a session. Includes permits and controls.
constexpr size_t kCommandCacheSize = 1536;
constexpr size_t kRetiredSessionMax = 64;
constexpr size_t kTxQueueBytes = 8192;
}
