#pragma once
#include "SerialProtocol.h"

namespace mct431 {
class StationOutput {
 public:
  virtual ~StationOutput() = default;
  // Must enqueue atomically, without waiting for the physical UART transmission.
  virtual bool transmit(const uint8_t* frame, size_t size) = 0;
  virtual void diagnostic(uint32_t now, const char* event, uint32_t cmdId, uint32_t partId,
                          uint32_t retry) = 0;
};

class ProductionStation : public FrameReceiver {
 public:
  explicit ProductionStation(StationOutput& output) : output_(output), decoder_(*this) {}
  void begin(uint32_t bootId, bool bootCounterValid, uint32_t now);
  void receive(uint8_t byte, uint32_t now);
  void tick(uint32_t now);
  void onRecord(uint32_t seq, const char* json, size_t size) override;
  void onFrameError(const char* code) override;
  const char* state() const;
  uint32_t produced() const {
    return produced_;
  }
  uint32_t session() const {
    return session_;
  }
  bool synchronized() const {
    return synced_;
  }
  const char* lastError() const {
    return lastError_;
  }

 private:
  enum class Type : uint8_t { Sync, Config, Start, Permit, Pause, Resume, Stop, Unknown };
  enum class Phase : uint8_t { Accepted, Done, Rejected, Cancelled };
  enum class Error : uint8_t {
    None,
    InvalidState,
    InvalidValue,
    VersionConflict,
    DuplicateConflict,
    CacheFull,
    Stale,
    MissingField,
    InvalidType
  };
  struct Request {
    Type type = Type::Unknown;
    uint32_t cmd = 0, batch = 0, part = 0, version = 0, cycle = 0, target = 0, parent = 0;
    bool hasParent = false;
    bool same(const Request& other) const;
  };
  struct Command {
    Request request;
    uint32_t cycleSnapshotMs = 0;
    Phase phase = Phase::Accepted;
    Error error = Error::None;
  };
  struct Report {
    bool pending = false;
    uint8_t attempts = 0;
    uint32_t sentAt = 0;
    int command = -1;
  };

  StationOutput& output_;
  SlipDecoder decoder_;
  FlatJson json_;
  Command commands_[kCommandCacheSize]{};
  size_t commandCount_ = 0;
  uint32_t retired_[kRetiredSessionMax]{};
  size_t retiredCount_ = 0;
  Report reports_[2]{};  // PART_PRODUCED and PRODUCTION_FINISHED.
  uint32_t now_ = 0, boot_ = 0, session_ = 0, lastSession_ = 0;
  uint32_t lastHeartbeat_ = 0, heartbeatSent_ = 0, heartbeatSeq_ = 0, gatewayHeartbeatSeq_ = 0;
  uint32_t helloSent_ = 0, rxSeq_ = 0;
  uint64_t txSeq_ = 0;
  bool bootValid_ = false, synced_ = false, fault_ = false;
  bool haveRxSeq_ = false, haveGatewayHeartbeat_ = false;
  bool configured_ = false, batchActive_ = false, paused_ = false, stopped_ = false;
  bool haveSession_ = false;
  bool helloPending_ = true;
  uint32_t configVersion_ = 0, cycleMs_ = 0, batch_ = 0, target_ = 0, produced_ = 0;
  uint32_t activeStarted_ = 0, activeCycleMs_ = 0;
  uint32_t syncCmd_ = 0, syncParent_ = 0;
  bool syncHasParent_ = false;
  int active_ = -1, start_ = -1, pausePending_ = -1, stopPending_ = -1;
  const char* lastError_ = "";

  static Type typeFrom(const char* name);
  static const char* name(Type type);
  static const char* errorName(Error error);
  bool emit(const char* fields);
  void hello();
  void heartbeat();
  void error(const char* code, const char* reason);
  void reject(const Request& request, Error code);
  bool response(const Request& request, const char* phase, const char* result = nullptr);
  void fault(const char* code, const char* reason);
  void latchFault(const char* code);
  bool retired(uint32_t session) const;
  void synchronize(uint32_t seq);
  bool readRequest(Type type, Request& request);
  bool allowedFields(Type type) const;
  void handle(Request request);
  void replay(int index);
  void finishCommand(int index);
  void finishProduction();
  void settleControls();
  bool sendReport(unsigned index, bool retry);
  void receiveReportAck(const char* type);
};
}
