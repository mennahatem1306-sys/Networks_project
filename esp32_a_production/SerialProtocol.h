#pragma once
#include "StationConfig.h"

namespace mct431 {
uint16_t crc16(const uint8_t* bytes, size_t size);
size_t encodeFrame(const char* json, size_t size, uint32_t seq, uint8_t* output, size_t capacity);

class FrameReceiver {
 public:
  virtual ~FrameReceiver() = default;
  virtual void onRecord(uint32_t seq, const char* json, size_t size) = 0;
  virtual void onFrameError(const char* code) = 0;
};

class SlipDecoder {
 public:
  explicit SlipDecoder(FrameReceiver& receiver) : receiver_(receiver) {}
  void feed(uint8_t byte);
  void reset();

 private:
  FrameReceiver& receiver_;
  uint8_t buffer_[kRecordMax]{};
  size_t size_ = 0;
  bool started_ = false, escaped_ = false, dropping_ = false;
  void finish();
};

class FlatJson {
 public:
  enum class Kind : uint8_t { String, Integer, Other };
  struct Field {
    char key[32]{};
    char text[96]{};
    int64_t integer = 0;
    Kind kind = Kind::Other;
  };
  bool parse(const char* data, size_t size);
  const Field* find(const char* key) const;
  bool uint32(const char* key, uint32_t& value) const;
  const char* string(const char* key) const;
  bool only(const char* const* keys, size_t size) const;
  const char* error() const {
    return error_;
  }

 private:
  static constexpr size_t kMaxFields = 20;
  Field fields_[kMaxFields]{};
  size_t count_ = 0, pos_ = 0, size_ = 0;
  const char* data_ = nullptr;
  const char* error_ = "INVALID_JSON";
  void whitespace();
  bool quoted(char* output, size_t capacity);
  bool number(Field& field);
  bool hex4(uint32_t& value);
};
}
