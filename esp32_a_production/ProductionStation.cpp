#include "ProductionStation.h"

#include <stdio.h>
#include <string.h>

#include <initializer_list>

namespace mct431 {
namespace {
bool elapsed(uint32_t now, uint32_t since, uint32_t duration) {
  return uint32_t(now - since) >= duration;
}
bool equal(const char* a, const char* b) {
  return a && b && !strcmp(a, b);
}
}

bool ProductionStation::Request::same(const Request& r) const {
  return type == r.type && cmd == r.cmd && batch == r.batch && part == r.part &&
         version == r.version && cycle == r.cycle && target == r.target &&
         hasParent == r.hasParent && (!hasParent || parent == r.parent);
}
ProductionStation::Type ProductionStation::typeFrom(const char* text) {
  if (equal(text, "SYNC_SESSION"))
    return Type::Sync;
  if (equal(text, "SET_PRODUCTION_CONFIG"))
    return Type::Config;
  if (equal(text, "START_PRODUCTION"))
    return Type::Start;
  if (equal(text, "PRODUCTION_PERMIT"))
    return Type::Permit;
  if (equal(text, "PAUSE_PRODUCTION"))
    return Type::Pause;
  if (equal(text, "RESUME_PRODUCTION"))
    return Type::Resume;
  if (equal(text, "STOP_PRODUCTION"))
    return Type::Stop;
  return Type::Unknown;
}
const char* ProductionStation::name(Type type) {
  switch (type) {
    case Type::Sync:
      return "SYNC_SESSION";
    case Type::Config:
      return "SET_PRODUCTION_CONFIG";
    case Type::Start:
      return "START_PRODUCTION";
    case Type::Permit:
      return "PRODUCTION_PERMIT";
    case Type::Pause:
      return "PAUSE_PRODUCTION";
    case Type::Resume:
      return "RESUME_PRODUCTION";
    case Type::Stop:
      return "STOP_PRODUCTION";
    default:
      return "UNKNOWN";
  }
}
const char* ProductionStation::errorName(Error error) {
  switch (error) {
    case Error::InvalidState:
      return "INVALID_STATE";
    case Error::InvalidValue:
      return "INVALID_VALUE";
    case Error::VersionConflict:
      return "CONFIG_VERSION_CONFLICT";
    case Error::DuplicateConflict:
      return "DUPLICATE_ID_CONFLICT";
    case Error::CacheFull:
      return "CACHE_FULL";
    case Error::Stale:
      return "STALE_MESSAGE";
    case Error::MissingField:
      return "MISSING_FIELD";
    case Error::InvalidType:
      return "INVALID_TYPE";
    default:
      return "INVALID_VALUE";
  }
}

void ProductionStation::begin(uint32_t bootId, bool bootCounterValid, uint32_t now) {
  boot_ = bootId;
  bootValid_ = bootCounterValid;
  now_ = now;
  fault_ = !bootCounterValid;
  helloPending_ = true;
  lastError_ = bootCounterValid ? "" : "BOOT_COUNTER_ERROR";
  output_.diagnostic(now_, bootCounterValid ? "BOOT" : "BOOT_COUNTER_ERROR", 0, 0, 0);
}
void ProductionStation::receive(uint8_t byte, uint32_t now) {
  now_ = now;
  decoder_.feed(byte);
}
const char* ProductionStation::state() const {
  if (fault_)
    return "FAULT";
  if (active_ >= 0)
    return "BUSY";
  if (batchActive_)
    return "ARMED";
  return "SAFE_IDLE";
}
bool ProductionStation::emit(const char* fields) {
  if (txSeq_ > UINT32_MAX) {
    latchFault("SEQUENCE_EXHAUSTED");
    return false;
  }
  char payload[kJsonMax + 1];
  int size = synced_
                 ? snprintf(payload, sizeof(payload),
                            "{\"schema_version\":1,\"device_id\":%u,\"session_id\":%lu,%s}",
                            unsigned(kDeviceId), (unsigned long)session_, fields)
                 : snprintf(payload, sizeof(payload), "{\"schema_version\":1,\"device_id\":%u,%s}",
                            unsigned(kDeviceId), fields);
  if (size <= 0 || size > int(kJsonMax)) {
    latchFault("FRAME_ERROR");
    return false;
  }
  uint8_t encoded[kEncodedMax];
  size_t encodedSize =
      encodeFrame(payload, size_t(size), uint32_t(txSeq_++), encoded, sizeof(encoded));
  if (!encodedSize || !output_.transmit(encoded, encodedSize)) {
    latchFault("TX_QUEUE_FULL");
    return false;
  }
  return true;
}
void ProductionStation::hello() {
  char fields[160];
  snprintf(fields, sizeof(fields), "\"msg_type\":\"HELLO\",\"boot_id\":%lu,\"device_state\":\"%s\"",
           (unsigned long)boot_, state());
  emit(fields);
  helloSent_ = now_;
  helloPending_ = false;
}
void ProductionStation::heartbeat() {
  char fields[180];
  snprintf(
      fields, sizeof(fields),
      "\"msg_type\":\"HEARTBEAT\",\"heartbeat_seq\":%lu,\"boot_id\":%lu,\"device_state\":\"%s\"",
      (unsigned long)heartbeatSeq_, (unsigned long)boot_, state());
  if (!emit(fields))
    return;
  heartbeatSent_ = now_;
  if (heartbeatSeq_ == UINT32_MAX)
    fault("SEQUENCE_EXHAUSTED", "Heartbeat counter exhausted");
  else
    ++heartbeatSeq_;
}
void ProductionStation::error(const char* code, const char* reason) {
  lastError_ = code;
  output_.diagnostic(now_, code, 0, 0, 0);
  char fields[240];
  // All callers use fixed ASCII literals, never unescaped remote text.
  snprintf(fields, sizeof(fields), "\"msg_type\":\"ERROR\",\"error_code\":\"%s\",\"reason\":\"%s\"",
           code, reason);
  emit(fields);
}
void ProductionStation::fault(const char* code, const char* reason) {
  error(code, reason);
  latchFault(code);
}
void ProductionStation::latchFault(const char* code) {
  if (active_ >= 0)
    commands_[active_].phase = Phase::Cancelled;
  if (pausePending_ >= 0)
    commands_[pausePending_].phase = Phase::Cancelled;
  if (stopPending_ >= 0)
    commands_[stopPending_].phase = Phase::Cancelled;
  active_ = pausePending_ = stopPending_ = -1;
  batchActive_ = false;
  paused_ = false;
  fault_ = true;
  reports_[0].pending = reports_[1].pending = false;
  synced_ = false;
  configured_ = false;
  helloPending_ = true;
  lastError_ = code;
  output_.diagnostic(now_, code, 0, 0, 0);
}
bool ProductionStation::response(const Request& r, const char* phase, const char* result) {
  char fields[280];
  if (result)
    snprintf(fields, sizeof(fields),
             "\"msg_type\":\"%s\",\"cmd_id\":%lu,\"ref_msg_type\":\"%s\",\"result\":\"%s\"", phase,
             (unsigned long)r.cmd, name(r.type), result);
  else
    snprintf(fields, sizeof(fields), "\"msg_type\":\"%s\",\"cmd_id\":%lu,\"ref_msg_type\":\"%s\"",
             phase, (unsigned long)r.cmd, name(r.type));
  if (!emit(fields))
    return false;
  output_.diagnostic(now_, phase, r.cmd, r.part, 0);
  return true;
}
void ProductionStation::reject(const Request& r, Error code) {
  char fields[320];
  snprintf(fields, sizeof(fields),
           "\"msg_type\":\"REJECTED\",\"cmd_id\":%lu,\"ref_msg_type\":\"%s\",\"error_code\":\"%s\","
           "\"reason\":\"%s\"",
           (unsigned long)r.cmd, name(r.type), errorName(code), errorName(code));
  emit(fields);
  lastError_ = errorName(code);
  output_.diagnostic(now_, errorName(code), r.cmd, r.part, 0);
}
bool ProductionStation::retired(uint32_t session) const {
  for (size_t i = 0; i < retiredCount_; ++i)
    if (retired_[i] == session)
      return true;
  return false;
}
void ProductionStation::synchronize(uint32_t seq) {
  uint32_t sid, cmd, target, boot, parent = 0;
  const char* const keys[] = {"schema_version", "msg_type",     "device_id",
                              "session_id",     "cmd_id",       "target_device_id",
                              "target_boot_id", "parent_cmd_id"};
  if (!json_.only(keys, sizeof(keys) / sizeof(keys[0])) || !json_.uint32("session_id", sid) ||
      !json_.uint32("cmd_id", cmd) || !json_.uint32("target_device_id", target) ||
      !json_.uint32("target_boot_id", boot) ||
      (json_.find("parent_cmd_id") && !json_.uint32("parent_cmd_id", parent))) {
    error("MISSING_FIELD", "Invalid synchronization fields");
    return;
  }
  if (!bootValid_) {
    error("BOOT_COUNTER_ERROR", "NVS boot counter must be repaired before synchronization");
    return;
  }
  if (target != kDeviceId || boot != boot_) {
    error("INVALID_VALUE", "Synchronization target mismatch");
    return;
  }
  if (retired(sid) || (!synced_ && haveSession_ && lastSession_ == sid)) {
    error("STALE_MESSAGE", "Retired synchronization session");
    return;
  }
  bool hasParent = json_.find("parent_cmd_id") != nullptr;
  Request request;
  request.type = Type::Sync;
  request.cmd = cmd;
  if (synced_ && sid == session_) {
    if (cmd != syncCmd_ || hasParent != syncHasParent_ || (hasParent && parent != syncParent_)) {
      reject(request, Error::DuplicateConflict);
      return;
    }
    if (haveRxSeq_ && seq <= rxSeq_) {
      error("STALE_MESSAGE", "Repeated transport sequence");
      return;
    }
    rxSeq_ = seq;
    if (!response(request, "DELIVERY_ACK") || !response(request, "ACCEPTED"))
      return;
    response(request, "COMPLETED", "READY");
    return;  // A handshake retry must not reset a running timer or caches.
  }
  if (haveSession_) {
    if (retiredCount_ == kRetiredSessionMax) {
      error("CACHE_FULL", "Retired session cache full; reboot safely");
      return;
    }
    retired_[retiredCount_++] = lastSession_;
  }
  // A genuinely new session abandons uncertain old work; nothing is resumed.
  if (active_ >= 0 || batchActive_)
    output_.diagnostic(now_, "SESSION_ABORT", 0, 0, 0);
  active_ = start_ = pausePending_ = stopPending_ = -1;
  batchActive_ = paused_ = configured_ = stopped_ = false;
  fault_ = false;
  reports_[0] = Report{};
  reports_[1] = Report{};
  commandCount_ = 0;
  produced_ = target_ = batch_ = 0;
  session_ = lastSession_ = sid;
  syncCmd_ = cmd;
  syncParent_ = parent;
  syncHasParent_ = hasParent;
  synced_ = true;
  haveSession_ = true;
  txSeq_ = 0;
  rxSeq_ = seq;
  haveRxSeq_ = true;
  lastHeartbeat_ = heartbeatSent_ = now_;
  heartbeatSeq_ = 0;
  haveGatewayHeartbeat_ = false;
  lastError_ = "";
  if (!response(request, "DELIVERY_ACK") || !response(request, "ACCEPTED"))
    return;
  response(request, "COMPLETED", "READY");
}

bool ProductionStation::allowedFields(Type type) const {
  const char* keys[13] = {"schema_version", "msg_type", "device_id",
                          "session_id",     "cmd_id",   "parent_cmd_id"};
  size_t count = 6;
  switch (type) {
    case Type::Config:
      keys[count++] = "config_version";
      keys[count++] = "cycle_time_ms";
      break;
    case Type::Start:
      keys[count++] = "batch_id";
      keys[count++] = "target_parts";
      break;
    case Type::Permit:
      keys[count++] = "batch_id";
      keys[count++] = "part_id";
      keys[count++] = "config_version";
      break;
    case Type::Pause:
    case Type::Resume:
    case Type::Stop:
      keys[count++] = "batch_id";
      break;
    default:
      break;
  }
  return json_.only(keys, count);
}
bool ProductionStation::readRequest(Type type, Request& r) {
  r.type = type;
  if (!json_.uint32("cmd_id", r.cmd)) {
    error("MISSING_FIELD", "A uint32 cmd_id is required");
    return false;
  }
  if (!allowedFields(type)) {
    reject(r, Error::InvalidValue);
    return false;
  }
  const char* fields[3]{};
  uint32_t* values[3]{};
  size_t count = 0;
  if (type == Type::Config) {
    fields[count] = "config_version";
    values[count++] = &r.version;
    fields[count] = "cycle_time_ms";
    values[count++] = &r.cycle;
  } else if (type == Type::Start || type == Type::Permit || type == Type::Pause ||
             type == Type::Resume || type == Type::Stop) {
    fields[count] = "batch_id";
    values[count++] = &r.batch;
    if (type == Type::Start) {
      fields[count] = "target_parts";
      values[count++] = &r.target;
    }
    if (type == Type::Permit) {
      fields[count] = "part_id";
      values[count++] = &r.part;
      fields[count] = "config_version";
      values[count++] = &r.version;
    }
  }
  for (size_t i = 0; i < count; ++i)
    if (!json_.uint32(fields[i], *values[i])) {
      reject(r, json_.find(fields[i]) ? Error::InvalidType : Error::MissingField);
      return false;
    }
  r.hasParent = json_.find("parent_cmd_id") != nullptr;
  if (r.hasParent && !json_.uint32("parent_cmd_id", r.parent)) {
    reject(r, Error::InvalidType);
    return false;
  }
  return true;
}

void ProductionStation::onRecord(uint32_t seq, const char* data, size_t size) {
  if (!json_.parse(data, size)) {
    error("INVALID_JSON", "Expected bounded flat UTF-8 JSON with unique keys");
    return;
  }
  uint32_t schema, device;
  const char* text = json_.string("msg_type");
  if (!json_.uint32("schema_version", schema) || schema != 1 ||
      !json_.uint32("device_id", device) || device != kGatewayId || !text) {
    error("INVALID_VALUE", "Invalid common envelope");
    return;
  }
  Type type = typeFrom(text);
  if (type == Type::Sync) {
    synchronize(seq);
    return;
  }
  uint32_t sid;
  if (!synced_ || !json_.uint32("session_id", sid) || sid != session_) {
    error("INVALID_SESSION", "Synchronize before sending operational messages");
    return;
  }
  if (haveRxSeq_ && seq <= rxSeq_) {
    error("STALE_MESSAGE", "Repeated transport sequence");
    return;
  }
  rxSeq_ = seq;
  haveRxSeq_ = true;
  if (equal(text, "HEARTBEAT")) {
    const char* const keys[] = {"schema_version", "msg_type",      "device_id",
                                "session_id",     "heartbeat_seq", "device_state"};
    uint32_t heartbeatSeq;
    const char* deviceState = json_.string("device_state");
    bool validState = equal(deviceState, "SAFE_IDLE") || equal(deviceState, "ARMED") ||
                      equal(deviceState, "BUSY") || equal(deviceState, "FAULT");
    if (!json_.only(keys, 6) || !json_.uint32("heartbeat_seq", heartbeatSeq) || !validState) {
      error("INVALID_VALUE", "Invalid heartbeat");
      return;
    }
    if (haveGatewayHeartbeat_ && heartbeatSeq <= gatewayHeartbeatSeq_) {
      error("STALE_MESSAGE", "Repeated heartbeat counter");
      return;
    }
    gatewayHeartbeatSeq_ = heartbeatSeq;
    haveGatewayHeartbeat_ = true;
    lastHeartbeat_ = now_;
    if (equal(deviceState, "FAULT"))
      fault("DEVICE_NOT_READY", "Gateway reports a fault");
    return;
  }
  if (equal(text, "DELIVERY_ACK") || equal(text, "ACCEPTED") || equal(text, "REJECTED")) {
    receiveReportAck(text);
    return;  // Never acknowledge an acknowledgement.
  }
  if (equal(text, "ERROR")) {
    output_.diagnostic(now_, "GATEWAY_ERROR", 0, 0, 0);
    return;
  }
  if (type == Type::Unknown) {
    error("UNKNOWN_MESSAGE_TYPE", "Unsupported production-station message");
    return;
  }
  Request request;
  if (readRequest(type, request))
    handle(request);
}
void ProductionStation::onFrameError(const char* code) {
  error(code, "Invalid serial record discarded");
}

void ProductionStation::handle(Request request) {
  if (!response(request, "DELIVERY_ACK"))
    return;
  if (request.cmd == syncCmd_) {
    reject(request, Error::DuplicateConflict);
    return;
  }
  for (size_t i = 0; i < commandCount_; ++i)
    if (commands_[i].request.cmd == request.cmd) {
      if (!commands_[i].request.same(request))
        reject(request, Error::DuplicateConflict);
      else
        replay(int(i));
      return;
    }
  if (commandCount_ == kCommandCacheSize) {
    reject(request, Error::CacheFull);
    return;
  }
  int index = int(commandCount_++);
  Command& command = commands_[index];
  command = Command{};
  command.request = request;
  Error invalid = Error::None;
  if (fault_)
    invalid = Error::InvalidState;
  else
    switch (request.type) {
      case Type::Config:
        if (!request.cycle || request.cycle > kCycleMaxMs)
          invalid = Error::InvalidValue;
        else if (configured_ && (request.version < configVersion_ ||
                                 (request.version == configVersion_ && request.cycle != cycleMs_)))
          invalid = Error::VersionConflict;
        break;
      case Type::Start:
        if (!request.target || request.target > kTargetMax)
          invalid = Error::InvalidValue;
        else if (!configured_ || batchActive_ || active_ >= 0 || reports_[0].pending ||
                 reports_[1].pending)
          invalid = Error::InvalidState;
        else
          for (size_t i = 0; i + 1 < commandCount_; ++i)
            if (commands_[i].request.type == Type::Start && commands_[i].phase != Phase::Rejected &&
                commands_[i].request.batch == request.batch)
              invalid = Error::DuplicateConflict;
        break;
      case Type::Permit:
        if (!batchActive_ || paused_ || active_ >= 0 || stopPending_ >= 0 || reports_[0].pending ||
            request.batch != batch_ || produced_ >= target_)
          invalid = Error::InvalidState;
        else if (!configured_ || request.version != configVersion_)
          invalid = Error::VersionConflict;
        for (size_t i = 0; i + 1 < commandCount_; ++i)
          if (commands_[i].request.type == Type::Permit && commands_[i].phase != Phase::Rejected &&
              commands_[i].request.part == request.part)
            invalid = Error::DuplicateConflict;
        break;
      case Type::Pause:
        if (start_ < 0 || stopped_ || request.batch != batch_ || paused_ || stopPending_ >= 0)
          invalid = Error::InvalidState;
        break;
      case Type::Resume:
        if (start_ < 0 || stopped_ || request.batch != batch_ || !paused_ || active_ >= 0 ||
            pausePending_ >= 0 || stopPending_ >= 0)
          invalid = Error::InvalidState;
        break;
      case Type::Stop:
        if (start_ < 0 || stopped_ || request.batch != batch_ || stopPending_ >= 0)
          invalid = Error::InvalidState;
        break;
      default:
        invalid = Error::InvalidValue;
        break;
    }
  if (invalid != Error::None) {
    command.phase = Phase::Rejected;
    command.error = invalid;
    reject(request, invalid);
    return;
  }
  command.phase = Phase::Accepted;
  if (!response(request, "ACCEPTED")) {
    command.phase = Phase::Cancelled;
    return;
  }
  switch (request.type) {
    case Type::Config:
      configVersion_ = request.version;
      cycleMs_ = request.cycle;
      configured_ = true;
      finishCommand(index);
      break;
    case Type::Start:
      batch_ = request.batch;
      target_ = request.target;
      produced_ = 0;
      batchActive_ = true;
      paused_ = stopped_ = false;
      start_ = index;
      finishCommand(index);
      break;
    case Type::Permit:
      command.cycleSnapshotMs = cycleMs_;
      active_ = index;
      activeStarted_ = now_;
      activeCycleMs_ = command.cycleSnapshotMs;
      break;
    case Type::Pause:
      paused_ = true;
      pausePending_ = index;
      settleControls();
      break;
    case Type::Resume:
      paused_ = false;
      finishCommand(index);
      break;
    case Type::Stop:
      paused_ = true;
      stopPending_ = index;
      settleControls();
      break;
    default:
      break;
  }
}
void ProductionStation::replay(int index) {
  Command& command = commands_[index];
  output_.diagnostic(now_, "DUPLICATE_REPLAY", command.request.cmd, command.request.part, 0);
  if (command.phase == Phase::Rejected) {
    reject(command.request, command.error);
    return;
  }
  if (command.phase == Phase::Cancelled) {
    reject(command.request, Error::Stale);
    return;
  }
  if (!response(command.request, "ACCEPTED"))
    return;
  if (command.phase != Phase::Done)
    return;
  if (command.request.type == Type::Permit) {
    Report saved = reports_[0];
    reports_[0].command = index;
    sendReport(0, false);
    if (synced_)
      reports_[0] = saved;
  } else
    finishCommand(index);
}
void ProductionStation::finishCommand(int index) {
  if (index < 0)
    return;
  Command& c = commands_[index];
  c.phase = Phase::Done;
  const char* result = "STOPPED";
  switch (c.request.type) {
    case Type::Config:
      result = "CONFIG_APPLIED";
      break;
    case Type::Start:
      result = "ARMED";
      break;
    case Type::Pause:
      result = "PAUSED";
      break;
    case Type::Resume:
      result = "RESUMED";
      break;
    default:
      break;
  }
  response(c.request, "COMPLETED", result);
}
void ProductionStation::finishProduction() {
  int index = active_;
  active_ = -1;
  Command& command = commands_[index];
  command.phase = Phase::Done;
  ++produced_;
  reports_[0] = Report{};
  reports_[0].pending = true;
  reports_[0].command = index;
  if (!sendReport(0, false))
    return;
  if (produced_ == target_) {
    batchActive_ = false;
    reports_[1] = Report{};
    reports_[1].pending = true;
    reports_[1].command = start_;
    if (!sendReport(1, false))
      return;
  }
  settleControls();
}
void ProductionStation::settleControls() {
  if (!synced_ || active_ >= 0)
    return;
  if (pausePending_ >= 0) {
    int index = pausePending_;
    pausePending_ = -1;
    finishCommand(index);
  }
  if (!synced_)
    return;
  if (stopPending_ >= 0) {
    int index = stopPending_;
    stopPending_ = -1;
    batchActive_ = false;
    paused_ = false;
    stopped_ = true;
    finishCommand(index);
  }
}
bool ProductionStation::sendReport(unsigned index, bool retry) {
  Report& report = reports_[index];
  if (!synced_ || report.command < 0)
    return false;
  const Request& r = commands_[report.command].request;
  char fields[320];
  if (index == 0)
    snprintf(fields, sizeof(fields),
             "\"msg_type\":\"PART_PRODUCED\",\"cmd_id\":%lu,\"batch_id\":%lu,\"part_id\":%lu,"
             "\"config_version\":%lu,\"result\":\"PRODUCED\"",
             (unsigned long)r.cmd, (unsigned long)r.batch, (unsigned long)r.part,
             (unsigned long)r.version);
  else
    snprintf(fields, sizeof(fields),
             "\"msg_type\":\"PRODUCTION_FINISHED\",\"cmd_id\":%lu,\"batch_id\":%lu,\"produced_"
             "total\":%lu",
             (unsigned long)r.cmd, (unsigned long)r.batch, (unsigned long)produced_);
  if (!emit(fields))
    return false;
  report.sentAt = now_;
  ++report.attempts;
  output_.diagnostic(now_, index ? "PRODUCTION_FINISHED" : "PART_PRODUCED", r.cmd, r.part,
                     retry ? report.attempts - 1 : 0);
  return true;
}
void ProductionStation::receiveReportAck(const char* type) {
  uint32_t cmd;
  const char* ref = json_.string("ref_msg_type");
  const char* const keys[] = {"schema_version", "msg_type",   "device_id", "session_id", "cmd_id",
                              "ref_msg_type",   "error_code", "reason",    "result"};
  if (!json_.only(keys, 9)) {
    error("INVALID_VALUE", "Unexpected result acknowledgement field");
    return;
  }
  if (!json_.uint32("cmd_id", cmd) || !ref) {
    error("MISSING_FIELD", "Result acknowledgement requires cmd_id and ref_msg_type");
    return;
  }
  for (const char* field : {"error_code", "reason", "result"}) {
    if (json_.find(field) && !json_.string(field)) {
      error("INVALID_TYPE", "Acknowledgement text fields must be strings");
      return;
    }
  }
  if (equal(type, "REJECTED") && (!json_.string("error_code") || !json_.string("reason"))) {
    error("MISSING_FIELD", "Rejection requires error_code and reason");
    return;
  }
  for (unsigned i = 0; i < 2; ++i) {
    Report& report = reports_[i];
    if (!report.pending || report.command < 0 || commands_[report.command].request.cmd != cmd ||
        !equal(ref, i ? "PRODUCTION_FINISHED" : "PART_PRODUCED"))
      continue;
    if (equal(type, "REJECTED")) {
      fault("RESULT_REJECTED", "Gateway rejected a production report");
      return;
    }
    // The gateway contract requires durable recording before a positive report ACK.
    report.pending = false;
    output_.diagnostic(now_, "REPORT_CONFIRMED", cmd, commands_[report.command].request.part, 0);
  }
}
void ProductionStation::tick(uint32_t now) {
  now_ = now;
  if (!synced_) {
    if (helloPending_ || elapsed(now_, helloSent_, kHeartbeatMs))
      hello();
    return;
  }
  if (elapsed(now_, lastHeartbeat_, kHeartbeatLossMs)) {
    fault("TIMEOUT", "Gateway heartbeat lost; old batch abandoned");
    return;
  }
  if (elapsed(now_, heartbeatSent_, kHeartbeatMs))
    heartbeat();
  if (!synced_)
    return;
  if (active_ >= 0 && elapsed(now_, activeStarted_, activeCycleMs_))
    finishProduction();
  for (unsigned i = 0; i < 2 && synced_; ++i) {
    Report& report = reports_[i];
    if (!report.pending || !elapsed(now_, report.sentAt, kDeliveryTimeoutMs))
      continue;
    if (report.attempts >= 1 + kMaxRetries) {
      fault("RETRY_EXHAUSTED", "Production report receipt was not confirmed");
      return;
    }
    sendReport(i, true);
  }
}
}
