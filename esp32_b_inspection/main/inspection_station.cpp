#include <algorithm>
#include <cstring>
#include <ArduinoJson.h>
#include "config.h"
#include "logic.h"
#include "station_types.h"
#include "protocol.h"
#include "platform.h"
#include "esp_log.h"

namespace {

bool sendResponse(const char *type, JsonDocument &request, const char *result = nullptr,
                  const char *error = nullptr);

TcpConnection client;
uint32_t bootId, sessionId, txSeq, heartbeatSeq, lastHeartbeat, lastSentHeartbeat;
uint32_t gatewayHeartbeatSeq;
bool gatewayHeartbeatSeen = false;
uint32_t reconnectAt, reconnectDelay = 1000, frameStarted;
bool synced = false, configured = false, fatalBoot = false;
uint32_t configVersion, inspectionMs;
int32_t ruleN, ruleK;

Job jobs[Protocol::MaxJobs];
size_t jobCount = 0;
int active = -1;
ConfigCommand commands[Protocol::MaxConfigCommands];
size_t commandCount = 0;
uint32_t syncCommand;
uint8_t rx[Protocol::PrefixBytes + Protocol::MaxJsonBytes];
size_t rxUsed = 0, rxNeed = Protocol::PrefixBytes;

const char *localState() { return active >= 0 ? "BUSY" : "SAFE_IDLE"; }
void disconnectSafe(const char *reason) {
  ESP_LOGW("station", "SAFE_IDLE: %s; active work abandoned; new session required", reason);
  client.stop();
  synced = configured = false;
  active = -1;
  rxUsed = 0;
  rxNeed = 4;
  reconnectAt = monotonicMs() + reconnectDelay;
  reconnectDelay = std::min(reconnectDelay * 2, Protocol::MaxReconnectDelayMs);
}
void buildEnvelope(JsonDocument &d, const char *type) {
  d["schema_version"] = Protocol::SchemaVersion;
  d["msg_type"] = type;
  d["device_id"] = Protocol::InspectionDeviceId;
  if (synced)
    d["session_id"] = sessionId;
}
bool sendMessage(JsonDocument &d) {
  if (!client.connected())
    return false;
  if (d.overflowed()) {
    disconnectSafe("JSON allocation failed");
    return false;
  }
  if (txSeq == UINT32_MAX) {
    disconnectSafe("sequence exhausted");
    return false;
  }
  d["seq"] = txSeq++;
  size_t n = measureJson(d);
  if (n > Protocol::MaxJsonBytes) {
    disconnectSafe("outgoing frame too large");
    return false;
  }
  uint8_t wire[Protocol::PrefixBytes + Protocol::MaxJsonBytes + 1];
  wire[0] = n >> 24;
  wire[1] = n >> 16;
  wire[2] = n >> 8;
  wire[3] = n;
  serializeJson(d, reinterpret_cast<char *>(wire + 4), n + 1);

  if (client.write(wire, n + 4) != n + 4) {
    disconnectSafe("short TCP write");
    return false;
  }
  ESP_LOGI("station", "TX %.*s", int(n), reinterpret_cast<const char *>(wire + 4));
  return true;
}
bool sendResponse(const char *type, JsonDocument &request, const char *result, const char *error) {
  JsonDocument d;
  buildEnvelope(d, type);
  d["ref_msg_type"] = request["msg_type"];
  if (request["cmd_id"].is<uint32_t>())
    d["cmd_id"] = request["cmd_id"];
  if (request["job_id"].is<uint32_t>())
    d["job_id"] = request["job_id"];
  if (result)
    d["result"] = result;
  if (error) {
    d["error_code"] = error;
    d["reason"] = error;
  }
  return sendMessage(d);
}
void rejectRequest(JsonDocument &r, const char *error) {
  sendResponse("REJECTED", r, nullptr, error);
}
void sendError(const char *error) {
  JsonDocument d;
  buildEnvelope(d, "ERROR");
  d["error_code"] = error;
  d["reason"] = error;
  sendMessage(d);
}
bool isUint32(JsonDocument &d, const char *name) {
  return d[name].is<uint32_t>() && !d[name].is<bool>();
}
void sendInspectionResult(Job &j) {
  JsonDocument d;
  buildEnvelope(d, "INSPECTION_RESULT");
  d["batch_id"] = j.batch;
  d["job_id"] = j.id;
  d["part_id"] = j.part;
  d["config_version"] = j.version;
  d["inspection_elapsed_ms"] = j.elapsed;
  d["result"] = j.fail ? "FAIL" : "PASS";
  if (sendMessage(d)) {
    j.sentAt = monotonicMs();
    ++j.attempts;
  }
}
void handleMessage(JsonDocument &d) {
  if (!d.is<JsonObject>() || !d["msg_type"].is<const char *>()) {
    sendError("INVALID_JSON");
    return;
  }
  const char *type = d["msg_type"];
  for (JsonPair field : d.as<JsonObject>()) {
    if (field.value().is<JsonObject>() || field.value().is<JsonArray>()) {
      rejectRequest(d, "INVALID_TYPE");
      return;
    }
  }
  if (!isUint32(d, "schema_version") ||
      d["schema_version"].as<uint32_t>() != Protocol::SchemaVersion || !isUint32(d, "device_id") ||
      d["device_id"].as<uint32_t>() != Protocol::GatewayDeviceId || !isUint32(d, "seq")) {
    rejectRequest(d, "INVALID_VALUE");
    return;
  }
  if (!strcmp(type, "SYNC_SESSION")) {
    if (!isUint32(d, "cmd_id") || !isUint32(d, "session_id") || !isUint32(d, "target_boot_id") ||
        !isUint32(d, "target_device_id") ||
        d["target_device_id"].as<uint32_t>() != Protocol::InspectionDeviceId ||
        d["target_boot_id"].as<uint32_t>() != bootId) {
      rejectRequest(d, "INVALID_VALUE");
      return;
    }
    if (synced && (d["session_id"].as<uint32_t>() != sessionId ||
                   d["cmd_id"].as<uint32_t>() != syncCommand)) {
      rejectRequest(d, "INVALID_SESSION");
      return;
    }
    if (!synced) {
      sessionId = d["session_id"];
      syncCommand = d["cmd_id"];
      synced = true;
      configured = false;
      jobCount = commandCount = 0;
      active = -1;
      lastHeartbeat = lastSentHeartbeat = monotonicMs();
      gatewayHeartbeatSeen = false;
      reconnectDelay = 1000;
    }
    if (!sendResponse("DELIVERY_ACK", d))
      return;
    if (!sendResponse("ACCEPTED", d))
      return;
    sendResponse("COMPLETED", d, "READY");
    return;
  }
  if (!synced || !isUint32(d, "session_id") || d["session_id"].as<uint32_t>() != sessionId) {
    rejectRequest(d, "INVALID_SESSION");
    return;
  }
  if (!strcmp(type, "HEARTBEAT")) {
    if (isUint32(d, "heartbeat_seq") && d["device_state"].is<const char *>() &&
        (!gatewayHeartbeatSeen || d["heartbeat_seq"].as<uint32_t>() > gatewayHeartbeatSeq)) {
      gatewayHeartbeatSeq = d["heartbeat_seq"];
      gatewayHeartbeatSeen = true;
      lastHeartbeat = monotonicMs();
    }
    return;
  }

  if (!strcmp(type, "ACCEPTED") || !strcmp(type, "DELIVERY_ACK")) {
    if (!strcmp(d["ref_msg_type"] | "", "INSPECTION_RESULT") && isUint32(d, "job_id")) {
      for (size_t i = 0; i < jobCount; ++i)
        if (jobs[i].id == d["job_id"].as<uint32_t>() && jobs[i].done)
          jobs[i].confirmed = true;
    }
    return;
  }
  if (!strcmp(type, "ERROR") || !strcmp(type, "REJECTED")) {
    disconnectSafe("gateway rejected report / reported error");
    return;
  }
  if (!strcmp(type, "SET_INSPECTION_CONFIG")) {
    if (!isUint32(d, "cmd_id")) {
      rejectRequest(d, "MISSING_FIELD");
      return;
    }
    if (!sendResponse("DELIVERY_ACK", d))
      return;
    if (!isUint32(d, "config_version") || !isUint32(d, "inspection_time_ms") ||
        !d["rule_n"].is<int32_t>() || d["rule_n"].is<bool>() || !d["rule_k"].is<int32_t>() ||
        d["rule_k"].is<bool>()) {
      rejectRequest(d, "INVALID_TYPE");
      return;
    }
    uint32_t id = d["cmd_id"], v = d["config_version"], ms = d["inspection_time_ms"];
    int32_t n = d["rule_n"], k = d["rule_k"];
    if (n <= 0) {
      rejectRequest(d, "INVALID_RULE_N");
      return;
    }
    if (ms < 1 || ms > Protocol::MaxInspectionMs) {
      rejectRequest(d, "INVALID_VALUE");
      return;
    }
    if (id == syncCommand) {
      rejectRequest(d, "DUPLICATE_ID_CONFLICT");
      return;
    }
    for (size_t i = 0; i < commandCount; ++i)
      if (commands[i].id == id) {
        auto &c = commands[i];
        if (c.version != v || c.duration != ms || c.n != n || c.k != k)
          rejectRequest(d, "DUPLICATE_ID_CONFLICT");
        else {
          sendResponse("ACCEPTED", d);
          sendResponse("COMPLETED", d, "CONFIG_APPLIED");
        }
        return;
      }
    if (configured && v <= configVersion) {
      rejectRequest(d, "CONFIG_VERSION_CONFLICT");
      return;
    }
    if (commandCount == Protocol::MaxConfigCommands) {
      rejectRequest(d, "INVALID_STATE");
      return;
    }
    if (!sendResponse("ACCEPTED", d))
      return;
    commands[commandCount++] = {id, v, ms, n, k};
    configVersion = v;
    inspectionMs = ms;
    ruleN = n;
    ruleK = k;
    configured = true;
    sendResponse("COMPLETED", d, "CONFIG_APPLIED");
    return;
  }
  if (!strcmp(type, "INSPECT_PART")) {
    if (!isUint32(d, "job_id")) {
      rejectRequest(d, "MISSING_FIELD");
      return;
    }
    if (!sendResponse("DELIVERY_ACK", d))
      return;
    if (!isUint32(d, "batch_id") || !isUint32(d, "part_id") || !isUint32(d, "config_version")) {
      rejectRequest(d, "INVALID_TYPE");
      return;
    }
    uint32_t id = d["job_id"], batch = d["batch_id"], part = d["part_id"],
             version = d["config_version"];
    for (size_t i = 0; i < jobCount; ++i)
      if (jobs[i].id == id) {
        Job &j = jobs[i];
        if (j.batch != batch || j.part != part || j.version != version)
          rejectRequest(d, "DUPLICATE_ID_CONFLICT");
        else {
          sendResponse("ACCEPTED", d);
          if (j.done) {
            j.attempts = 0;
            sendInspectionResult(j);
          }
        }
        return;
      }
    if (!configured) {
      rejectRequest(d, "DEVICE_NOT_READY");
      return;
    }
    if (version != configVersion) {
      rejectRequest(d, "CONFIG_VERSION_CONFLICT");
      return;
    }
    if (active >= 0 || jobCount == Protocol::MaxJobs) {
      rejectRequest(d, "INVALID_STATE");
      return;
    }
    Job j = {};
    j.id = id;
    j.batch = batch;
    j.part = part;
    j.version = version;
    j.duration = inspectionMs;
    j.n = ruleN;
    j.k = ruleK;
    if (!sendResponse("ACCEPTED", d))
      return;
    if (!synced)
      return;
    j.started = monotonicMs();
    jobs[jobCount] = j;
    active = jobCount++;
    return;
  }
  rejectRequest(d, "UNKNOWN_MESSAGE_TYPE");
}
void receiveFrames() {

  unsigned budget = Protocol::ReceiveBudgetBytes;
  while (client.connected() && client.available() && budget--) {
    if (!rxUsed)
      frameStarted = monotonicMs();
    int incoming = client.read();
    if (incoming < 0)
      break;
    rx[rxUsed++] = uint8_t(incoming);
    if (rxUsed == 4 && rxNeed == 4) {
      uint32_t n =
          (uint32_t(rx[0]) << 24) | (uint32_t(rx[1]) << 16) | (uint32_t(rx[2]) << 8) | rx[3];
      if (n == 0 || n > Protocol::MaxJsonBytes) {
        disconnectSafe("FRAME_ERROR");
        return;
      }
      rxNeed = n + 4;
    }
    if (rxUsed == rxNeed) {
      JsonDocument d;
      auto err = deserializeJson(d, rx + 4, rxNeed - 4, DeserializationOption::NestingLimit(2));
      rxUsed = 0;
      rxNeed = 4;
      if (err)
        sendError("INVALID_JSON");
      else
        handleMessage(d);
    }
  }
  if (rxUsed && uint32_t(monotonicMs() - frameStarted) > Protocol::FrameTimeoutMs)
    disconnectSafe("partial frame timeout");
}
void stationSetup() {
  if (!initializeBootCounter(bootId) || !startNetwork()) {
    fatalBoot = true;
    ESP_LOGE("station", "Initialization failed; network work disabled");
  }
}
void stationLoop() {
  if (fatalBoot) {
    yieldStation();
    return;
  }
  uint32_t now = monotonicMs();
  if (!client.connected()) {
    if (synced)
      disconnectSafe("connection lost");
    if (networkReady() && int32_t(now - reconnectAt) >= 0) {

      if (client.connect(GATEWAY_HOST, GATEWAY_PORT, Protocol::ConnectTimeoutMs)) {
        txSeq = heartbeatSeq = 0;
        rxUsed = 0;
        rxNeed = 4;
        JsonDocument d;
        buildEnvelope(d, "HELLO");
        d["boot_id"] = bootId;
        d["device_state"] = "SAFE_IDLE";
        sendMessage(d);
        lastHeartbeat = monotonicMs();
      } else {
        reconnectAt = monotonicMs() + reconnectDelay;
        reconnectDelay = std::min(reconnectDelay * 2, Protocol::MaxReconnectDelayMs);
      }
    }
    yieldStation();
    return;
  }
  receiveFrames();
  now = monotonicMs();
  if (!client.connected())
    return;
  if (uint32_t(now - lastHeartbeat) >= Protocol::HeartbeatTimeoutMs) {
    disconnectSafe("heartbeat / handshake timeout");
    return;
  }
  if (synced && uint32_t(now - lastSentHeartbeat) >= Protocol::HeartbeatIntervalMs) {
    JsonDocument d;
    buildEnvelope(d, "HEARTBEAT");
    d["heartbeat_seq"] = heartbeatSeq++;
    d["boot_id"] = bootId;
    d["device_state"] = localState();
    sendMessage(d);
    lastSentHeartbeat = now;
  }
  if (active >= 0 && elapsedAtLeast(now, jobs[active].started, jobs[active].duration)) {
    Job &j = jobs[active];
    j.elapsed = now - j.started;
    j.fail = inspectionFails(j.part, j.n, j.k);
    j.done = true;
    active = -1;
    sendInspectionResult(j);
  }
  for (size_t i = 0; i < jobCount && synced; ++i) {
    Job &j = jobs[i];
    if (j.done && !j.confirmed &&
        elapsedAtLeast(monotonicMs(), j.sentAt, Protocol::ResultRetryIntervalMs)) {
      if (j.attempts >= Protocol::MaxResultAttempts) {
        sendError("RETRY_EXHAUSTED");
        disconnectSafe("result retries exhausted");
        break;
      }
      sendInspectionResult(j);
    }
  }
  yieldStation();
}

}

extern "C" void app_main() {
  stationSetup();
  if (fatalBoot)
    return;
  ESP_LOGI("station", "Inspection station ready; boot_id=%lu", static_cast<unsigned long>(bootId));
  while (true)
    stationLoop();
}
