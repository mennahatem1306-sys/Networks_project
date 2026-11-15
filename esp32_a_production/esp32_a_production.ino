#include <Arduino.h>
#include <Preferences.h>

#include "ProductionStation.h"

using namespace mct431;

class UartOutput final : public StationOutput {
 public:
  bool transmit(const uint8_t* frame, size_t size) override {
    if (size > kTxQueueBytes - queuedBytes_)
      return false;
    for (size_t i = 0; i < size; ++i) {
      queue_[writeIndex_] = frame[i];
      writeIndex_ = (writeIndex_ + 1) % kTxQueueBytes;
    }
    queuedBytes_ += size;
    return true;
  }
  void diagnostic(uint32_t now, const char* event, uint32_t cmd, uint32_t part,
                  uint32_t retry) override {
    // Plaintext logging would corrupt the binary protocol on this UART.
    lastEventMs = now;
    lastEvent = event;
    lastCmd = cmd;
    lastPart = part;
    lastRetry = retry;
    ++eventCount;
  }
  void pump() {
    int available = Serial.availableForWrite();
    if (available <= 0 || !queuedBytes_)
      return;
    size_t writeSize = queuedBytes_ < size_t(available) ? queuedBytes_ : size_t(available);
    size_t contiguous = kTxQueueBytes - readIndex_;
    if (writeSize > contiguous)
      writeSize = contiguous;
    size_t written = Serial.write(queue_ + readIndex_, writeSize);
    readIndex_ = (readIndex_ + written) % kTxQueueBytes;
    queuedBytes_ -= written;
  }
  uint32_t lastEventMs = 0, lastCmd = 0, lastPart = 0, lastRetry = 0, eventCount = 0;
  const char* lastEvent = "BOOT";

 private:
  uint8_t queue_[kTxQueueBytes]{};
  size_t readIndex_ = 0, writeIndex_ = 0, queuedBytes_ = 0;
};

UartOutput uartOutput;
ProductionStation station(uartOutput);

bool incrementBootCounter(uint32_t& bootId) {
  Preferences preferences;
  if (!preferences.begin("mct431-a", false))
    return false;
  bool valid = true;
  uint32_t previous = 0;
  if (preferences.isKey("boot_id")) {
    if (preferences.getType("boot_id") != PT_U32)
      valid = false;
    // A failed read must not fall back to zero and accidentally reuse boot ID 1.
    else
      previous = preferences.getUInt("boot_id", UINT32_MAX);
  }
  if (previous == UINT32_MAX)
    valid = false;
  if (valid) {
    bootId = previous + 1;
    valid = preferences.putUInt("boot_id", bootId) == sizeof(uint32_t) &&
            preferences.getUInt("boot_id", 0) == bootId;
  }
  preferences.end();
  return valid;
}

void setup() {
  Serial.setRxBufferSize(2048);
  Serial.begin(kBaud, SERIAL_8N1);
  Serial.setDebugOutput(false);
  uint32_t bootId = 0;
  bool bootValid = incrementBootCounter(bootId);
  station.begin(bootId, bootValid, millis());
}

void loop() {
  uartOutput.pump();
  // Bound input work so continuous traffic cannot starve timers/heartbeats.
  for (unsigned budget = 0; budget < 256 && Serial.available() > 0; ++budget) {
    int byte = Serial.read();
    if (byte >= 0)
      station.receive(uint8_t(byte), millis());
  }
  station.tick(millis());
  uartOutput.pump();
  yield();
}
