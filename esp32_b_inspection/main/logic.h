#pragma once
#include <stdint.h>
constexpr bool inspectionFails(uint32_t part, int32_t n, int32_t k) {
  return n > 0 && (int64_t(part) + int64_t(k)) % int64_t(n) == 0;
}
constexpr bool elapsedAtLeast(uint32_t now, uint32_t start, uint32_t duration) {
  return uint32_t(now - start) >= duration;
}
