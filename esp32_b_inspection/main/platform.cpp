#include "platform.h"
#include "config.h"
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstring>
#include <unistd.h>
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "lwip/tcp.h"
#include "nvs.h"
#include "nvs_flash.h"

namespace {
std::atomic<bool> hasAddress{false};

void networkEvent(void *, esp_event_base_t base, int32_t event, void *) {
  if (base == WIFI_EVENT && event == WIFI_EVENT_STA_START) {
    esp_wifi_connect();
  } else if (base == WIFI_EVENT && event == WIFI_EVENT_STA_DISCONNECTED) {
    hasAddress.store(false);
    esp_wifi_connect();
  } else if (base == IP_EVENT && event == IP_EVENT_STA_GOT_IP) {
    hasAddress.store(true);
    ESP_LOGI("network", "Wi-Fi IPv4 address ready");
  } else if (base == IP_EVENT && event == IP_EVENT_STA_LOST_IP) {
    hasAddress.store(false);
  }
}

bool waitWritable(int descriptor, uint32_t timeoutMs) {
  fd_set writable;
  FD_ZERO(&writable);
  FD_SET(descriptor, &writable);
  timeval timeout{};
  timeout.tv_sec = timeoutMs / 1000;
  timeout.tv_usec = (timeoutMs % 1000) * 1000;
  return select(descriptor + 1, nullptr, &writable, nullptr, &timeout) > 0;
}
}

uint32_t monotonicMs() { return uint32_t(esp_timer_get_time() / 1000); }
bool networkReady() { return hasAddress.load(); }
void yieldStation() { vTaskDelay(pdMS_TO_TICKS(1)); }

bool initializeBootCounter(uint32_t &bootId) {
  if (nvs_flash_init() != ESP_OK)
    return false;
  nvs_handle_t handle;
  if (nvs_open("mct431-b", NVS_READWRITE, &handle) != ESP_OK)
    return false;
  uint32_t previous = 0;
  esp_err_t error = nvs_get_u32(handle, "boot", &previous);
  bool valid = (error == ESP_OK || error == ESP_ERR_NVS_NOT_FOUND) && previous != UINT32_MAX;
  uint32_t stored = 0;
  if (valid) {
    bootId = previous + 1;
    valid = nvs_set_u32(handle, "boot", bootId) == ESP_OK && nvs_commit(handle) == ESP_OK &&
            nvs_get_u32(handle, "boot", &stored) == ESP_OK && stored == bootId;
  }
  nvs_close(handle);
  return valid;
}

bool startNetwork() {
  if (std::strlen(WIFI_SSID) > 32 || std::strlen(WIFI_PASSWORD) > 63)
    return false;
  if (esp_netif_init() != ESP_OK || esp_event_loop_create_default() != ESP_OK)
    return false;
  if (!esp_netif_create_default_wifi_sta())
    return false;
  wifi_init_config_t initialization = WIFI_INIT_CONFIG_DEFAULT();
  if (esp_wifi_init(&initialization) != ESP_OK ||
      esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, networkEvent, nullptr) != ESP_OK ||
      esp_event_handler_register(IP_EVENT, ESP_EVENT_ANY_ID, networkEvent, nullptr) != ESP_OK)
    return false;
  wifi_config_t configuration{};
  std::memcpy(configuration.sta.ssid, WIFI_SSID, std::strlen(WIFI_SSID));
  std::memcpy(configuration.sta.password, WIFI_PASSWORD, std::strlen(WIFI_PASSWORD));
  configuration.sta.threshold.authmode = WIFI_AUTH_OPEN;
  return esp_wifi_set_storage(WIFI_STORAGE_RAM) == ESP_OK &&
         esp_wifi_set_mode(WIFI_MODE_STA) == ESP_OK &&
         esp_wifi_set_config(WIFI_IF_STA, &configuration) == ESP_OK && esp_wifi_start() == ESP_OK;
}

bool TcpConnection::connect(const char *host, uint16_t port, uint32_t timeoutMs) {
  stop();
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_port = htons(port);
  if (inet_pton(AF_INET, host, &address.sin_addr) != 1)
    return false;
  descriptor_ = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (descriptor_ < 0)
    return false;
  int enabled = 1;
  if (ioctl(descriptor_, FIONBIO, &enabled) < 0 ||
      setsockopt(descriptor_, IPPROTO_TCP, TCP_NODELAY, &enabled, sizeof(enabled)) < 0) {
    stop();
    return false;
  }
  int result = ::connect(descriptor_, reinterpret_cast<sockaddr *>(&address), sizeof(address));
  if (result < 0 && (errno != EINPROGRESS || !waitWritable(descriptor_, timeoutMs))) {
    stop();
    return false;
  }
  int error = 0;
  socklen_t length = sizeof(error);
  if (getsockopt(descriptor_, SOL_SOCKET, SO_ERROR, &error, &length) < 0 || error != 0) {
    stop();
    return false;
  }
  return true;
}

bool TcpConnection::connected() {
  if (descriptor_ < 0)
    return false;
  if (!networkReady()) {
    stop();
    return false;
  }
  uint8_t byte;
  int count = recv(descriptor_, &byte, 1, MSG_PEEK | MSG_DONTWAIT);
  if (count == 0 || (count < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) {
    stop();
    return false;
  }
  return true;
}

size_t TcpConnection::available() {
  if (!connected())
    return 0;
  int count = 0;
  if (ioctl(descriptor_, FIONREAD, &count) < 0) {
    stop();
    return 0;
  }
  return count > 0 ? size_t(count) : 0;
}

int TcpConnection::read() {
  uint8_t byte;
  return recv(descriptor_, &byte, 1, MSG_DONTWAIT) == 1 ? byte : -1;
}

size_t TcpConnection::write(const uint8_t *data, size_t size) {
  size_t sent = 0;
  uint32_t started = monotonicMs();
  while (descriptor_ >= 0 && sent < size) {
    int count = send(descriptor_, data + sent, size - sent, MSG_DONTWAIT);
    if (count > 0) {
      sent += size_t(count);
    } else if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
      uint32_t elapsed = monotonicMs() - started;
      if (elapsed >= 100 || !waitWritable(descriptor_, 100 - elapsed))
        break;
    } else {
      break;
    }
  }
  return sent;
}

void TcpConnection::stop() {
  if (descriptor_ >= 0) {
    shutdown(descriptor_, SHUT_RDWR);
    close(descriptor_);
    descriptor_ = -1;
  }
}
