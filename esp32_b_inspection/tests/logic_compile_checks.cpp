#include "../main/logic.h"

static_assert(inspectionFails(2, 3, 1));
static_assert(!inspectionFails(3, 3, 1));
static_assert(inspectionFails(1, 3, -1));
static_assert(inspectionFails(UINT32_MAX, 1, INT32_MIN));
static_assert(!inspectionFails(UINT32_MAX, INT32_MAX, INT32_MAX));
static_assert(!inspectionFails(0, INT32_MAX, INT32_MIN));
static_assert(inspectionFails(0, 3, -3));
static_assert(!inspectionFails(2, 0, 1));
static_assert(elapsedAtLeast(20, UINT32_MAX - 15, 36));
static_assert(!elapsedAtLeast(20, UINT32_MAX - 15, 37));
static_assert(elapsedAtLeast(6000, 1000, 5000));
static_assert(!elapsedAtLeast(5999, 1000, 5000));
