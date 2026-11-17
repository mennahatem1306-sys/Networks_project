#pragma once
#include <stdint.h>

struct Job {
  uint32_t id;
  uint32_t batch;
  uint32_t part;
  uint32_t version;
  uint32_t duration;
  uint32_t started;
  uint32_t elapsed;
  uint32_t sentAt;
  int32_t n;
  int32_t k;
  uint8_t attempts;
  bool done;
  bool confirmed;
  bool fail;
};

struct ConfigCommand {
  uint32_t id;
  uint32_t version;
  uint32_t duration;
  int32_t n;
  int32_t k;
};
