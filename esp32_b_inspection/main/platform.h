#pragma once
#include <stddef.h>
#include <stdint.h>

uint32_t monotonicMs();
bool initializeBootCounter(uint32_t &bootId);
bool startNetwork();
bool networkReady();
void yieldStation();

class TcpConnection {
public:
  bool connect(const char *host, uint16_t port, uint32_t timeoutMs);
  bool connected();
  size_t available();
  int read();
  size_t write(const uint8_t *data, size_t size);
  void stop();

private:
  int descriptor_ = -1;
};
