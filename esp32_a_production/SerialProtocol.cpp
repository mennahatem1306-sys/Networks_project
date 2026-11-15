#include "SerialProtocol.h"

#include <limits.h>
#include <string.h>

#include <initializer_list>

namespace mct431 {
namespace {
constexpr uint8_t END = 0xc0, ESC = 0xdb, ESC_END = 0xdc, ESC_ESC = 0xdd;
uint16_t read16(const uint8_t* p) {
  return (uint16_t(p[0]) << 8) | p[1];
}
uint32_t read32(const uint8_t* p) {
  return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | p[3];
}
bool digit(char c) {
  return c >= '0' && c <= '9';
}
bool validUtf8(const char* data, size_t size) {
  for (size_t i = 0; i < size;) {
    uint8_t c = uint8_t(data[i++]);
    if (c == 0)
      return false;
    if (c < 0x80)
      continue;
    uint32_t cp = 0, minimum = 0;
    unsigned more = 0;
    if (c >= 0xc2 && c <= 0xdf) {
      cp = c & 31;
      more = 1;
      minimum = 0x80;
    } else if (c >= 0xe0 && c <= 0xef) {
      cp = c & 15;
      more = 2;
      minimum = 0x800;
    } else if (c >= 0xf0 && c <= 0xf4) {
      cp = c & 7;
      more = 3;
      minimum = 0x10000;
    } else
      return false;
    if (i + more > size)
      return false;
    while (more--) {
      c = uint8_t(data[i++]);
      if ((c & 0xc0) != 0x80)
        return false;
      cp = (cp << 6) | (c & 63);
    }
    if (cp < minimum || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff))
      return false;
  }
  return true;
}
}

uint16_t crc16(const uint8_t* bytes, size_t size) {
  uint16_t crc = 0xffff;
  while (size--) {
    crc ^= uint16_t(*bytes++) << 8;
    for (unsigned bit = 0; bit < 8; ++bit)
      crc = (crc & 0x8000) ? uint16_t((crc << 1) ^ 0x1021) : uint16_t(crc << 1);
  }
  return crc;
}

size_t encodeFrame(const char* json, size_t size, uint32_t seq, uint8_t* output, size_t capacity) {
  if (!size || size > kJsonMax || capacity < 2 * (size + 10) + 2)
    return 0;
  uint8_t record[kRecordMax];
  record[0] = uint8_t(kProductionTestId >> 8);
  record[1] = uint8_t(kProductionTestId);
  record[2] = uint8_t(seq >> 24);
  record[3] = uint8_t(seq >> 16);
  record[4] = uint8_t(seq >> 8);
  record[5] = uint8_t(seq);
  record[6] = uint8_t(size >> 8);
  record[7] = uint8_t(size);
  memcpy(record + 8, json, size);
  uint16_t crc = crc16(record, size + 8);
  record[size + 8] = uint8_t(crc >> 8);
  record[size + 9] = uint8_t(crc);
  size_t out = 0;
  output[out++] = END;
  for (size_t i = 0; i < size + 10; ++i) {
    if (record[i] == END) {
      output[out++] = ESC;
      output[out++] = ESC_END;
    } else if (record[i] == ESC) {
      output[out++] = ESC;
      output[out++] = ESC_ESC;
    } else
      output[out++] = record[i];
  }
  output[out++] = END;
  return out;
}

void SlipDecoder::reset() {
  size_ = 0;
  started_ = escaped_ = dropping_ = false;
}
void SlipDecoder::feed(uint8_t byte) {
  if (byte == END) {
    if (started_ && escaped_ && !dropping_)
      receiver_.onFrameError("FRAME_ERROR");
    else if (started_ && size_ && !dropping_)
      finish();
    started_ = true;
    size_ = 0;
    escaped_ = dropping_ = false;
    return;
  }
  // Ignore ROM boot text and all input until the first SLIP delimiter.
  if (!started_ || dropping_)
    return;
  if (escaped_) {
    escaped_ = false;
    if (byte == ESC_END)
      byte = END;
    else if (byte == ESC_ESC)
      byte = ESC;
    else {
      dropping_ = true;
      receiver_.onFrameError("FRAME_ERROR");
      return;
    }
  } else if (byte == ESC) {
    escaped_ = true;
    return;
  }
  if (size_ == sizeof(buffer_)) {
    dropping_ = true;
    receiver_.onFrameError("FRAME_ERROR");
    return;
  }
  buffer_[size_++] = byte;
}
void SlipDecoder::finish() {
  if (size_ < 10 || read16(buffer_) != kProductionTestId || read16(buffer_ + 6) != size_ - 10 ||
      size_ - 10 > kJsonMax) {
    receiver_.onFrameError("FRAME_ERROR");
    return;
  }
  if (read16(buffer_ + size_ - 2) != crc16(buffer_, size_ - 2)) {
    receiver_.onFrameError("CRC_ERROR");
    return;
  }
  receiver_.onRecord(read32(buffer_ + 2), reinterpret_cast<const char*>(buffer_ + 8), size_ - 10);
}

void FlatJson::whitespace() {
  while (pos_ < size_ &&
         (data_[pos_] == ' ' || data_[pos_] == '\r' || data_[pos_] == '\n' || data_[pos_] == '\t'))
    ++pos_;
}
bool FlatJson::hex4(uint32_t& value) {
  value = 0;
  for (unsigned i = 0; i < 4; ++i) {
    if (pos_ == size_)
      return false;
    char c = data_[pos_++];
    unsigned n;
    if (digit(c))
      n = unsigned(c - '0');
    else if (c >= 'a' && c <= 'f')
      n = unsigned(c - 'a' + 10);
    else if (c >= 'A' && c <= 'F')
      n = unsigned(c - 'A' + 10);
    else
      return false;
    value = (value << 4) | n;
  }
  return true;
}
bool FlatJson::quoted(char* output, size_t capacity) {
  if (pos_ == size_ || data_[pos_++] != '"')
    return false;
  size_t used = 0;
  auto append = [&](uint8_t c) {
    if (!c || used + 1 >= capacity)
      return false;
    output[used++] = char(c);
    return true;
  };
  while (pos_ < size_) {
    uint8_t c = uint8_t(data_[pos_++]);
    if (c == '"') {
      output[used] = 0;
      return true;
    }
    if (c < 0x20)
      return false;
    if (c != '\\') {
      if (!append(c))
        return false;
      continue;
    }
    if (pos_ == size_)
      return false;
    c = uint8_t(data_[pos_++]);
    switch (c) {
      case '"':
      case '\\':
      case '/':
        if (!append(c))
          return false;
        break;
      case 'b':
        if (!append('\b'))
          return false;
        break;
      case 'f':
        if (!append('\f'))
          return false;
        break;
      case 'n':
        if (!append('\n'))
          return false;
        break;
      case 'r':
        if (!append('\r'))
          return false;
        break;
      case 't':
        if (!append('\t'))
          return false;
        break;
      case 'u': {
        uint32_t cp;
        if (!hex4(cp))
          return false;
        if (cp >= 0xd800 && cp <= 0xdbff) {
          if (pos_ + 2 > size_ || data_[pos_++] != '\\' || data_[pos_++] != 'u')
            return false;
          uint32_t low;
          if (!hex4(low) || low < 0xdc00 || low > 0xdfff)
            return false;
          cp = 0x10000 + ((cp - 0xd800) << 10) + low - 0xdc00;
        } else if (cp >= 0xdc00 && cp <= 0xdfff)
          return false;
        if (cp < 0x80) {
          if (!append(uint8_t(cp)))
            return false;
        } else if (cp < 0x800) {
          if (!append(uint8_t(0xc0 | (cp >> 6))) || !append(uint8_t(0x80 | (cp & 63))))
            return false;
        } else if (cp < 0x10000) {
          if (!append(uint8_t(0xe0 | (cp >> 12))) || !append(uint8_t(0x80 | ((cp >> 6) & 63))) ||
              !append(uint8_t(0x80 | (cp & 63))))
            return false;
        } else {
          if (!append(uint8_t(0xf0 | (cp >> 18))) || !append(uint8_t(0x80 | ((cp >> 12) & 63))) ||
              !append(uint8_t(0x80 | ((cp >> 6) & 63))) || !append(uint8_t(0x80 | (cp & 63))))
            return false;
        }
        break;
      }
      default:
        return false;
    }
  }
  return false;
}
bool FlatJson::number(Field& field) {
  bool negative = false, integral = true, overflow = false;
  if (pos_ < size_ && data_[pos_] == '-') {
    negative = true;
    ++pos_;
  }
  if (pos_ == size_ || !digit(data_[pos_]))
    return false;
  uint64_t value = 0;
  if (data_[pos_] == '0')
    ++pos_;
  else
    while (pos_ < size_ && digit(data_[pos_])) {
      unsigned d = unsigned(data_[pos_++] - '0');
      if (value > (uint64_t(INT64_MAX) - d) / 10)
        overflow = true;
      if (!overflow)
        value = value * 10 + d;
    }
  if (pos_ < size_ && data_[pos_] == '.') {
    integral = false;
    ++pos_;
    if (pos_ == size_ || !digit(data_[pos_]))
      return false;
    while (pos_ < size_ && digit(data_[pos_]))
      ++pos_;
  }
  if (pos_ < size_ && (data_[pos_] == 'e' || data_[pos_] == 'E')) {
    integral = false;
    ++pos_;
    if (pos_ < size_ && (data_[pos_] == '+' || data_[pos_] == '-'))
      ++pos_;
    if (pos_ == size_ || !digit(data_[pos_]))
      return false;
    while (pos_ < size_ && digit(data_[pos_]))
      ++pos_;
  }
  field.kind = integral && !overflow ? Kind::Integer : Kind::Other;
  field.integer = negative ? -int64_t(value) : int64_t(value);
  return true;
}
bool FlatJson::parse(const char* data, size_t size) {
  count_ = pos_ = 0;
  data_ = data;
  size_ = size;
  error_ = "INVALID_JSON";
  if (!size || size > kJsonMax || !validUtf8(data, size))
    return false;
  whitespace();
  if (pos_ == size_ || data_[pos_++] != '{')
    return false;
  whitespace();
  if (pos_ < size_ && data_[pos_] == '}') {
    ++pos_;
    whitespace();
    return pos_ == size_;
  }
  while (pos_ < size_ && count_ < kMaxFields) {
    Field& field = fields_[count_];
    field = Field{};
    if (!quoted(field.key, sizeof(field.key)))
      return false;
    for (size_t i = 0; i < count_; ++i)
      if (!strcmp(fields_[i].key, field.key))
        return false;
    whitespace();
    if (pos_ == size_ || data_[pos_++] != ':')
      return false;
    whitespace();
    if (pos_ == size_)
      return false;
    if (data_[pos_] == '"') {
      field.kind = Kind::String;
      if (!quoted(field.text, sizeof(field.text)))
        return false;
    } else if (digit(data_[pos_]) || data_[pos_] == '-') {
      if (!number(field))
        return false;
    } else {
      bool found = false;
      for (const char* literal : {"true", "false", "null"}) {
        size_t n = strlen(literal);
        if (pos_ + n <= size_ && !memcmp(data_ + pos_, literal, n)) {
          pos_ += n;
          field.kind = Kind::Other;
          found = true;
          break;
        }
      }
      if (!found)
        return false;  // Nested objects/arrays are outside the flat contract.
    }
    ++count_;
    whitespace();
    if (pos_ == size_)
      return false;
    if (data_[pos_] == '}') {
      ++pos_;
      whitespace();
      return pos_ == size_;
    }
    if (data_[pos_++] != ',')
      return false;
    whitespace();
  }
  return false;
}
const FlatJson::Field* FlatJson::find(const char* key) const {
  for (size_t i = 0; i < count_; ++i)
    if (!strcmp(fields_[i].key, key))
      return &fields_[i];
  return nullptr;
}
bool FlatJson::uint32(const char* key, uint32_t& value) const {
  const Field* f = find(key);
  if (!f || f->kind != Kind::Integer || f->integer < 0 || uint64_t(f->integer) > UINT32_MAX)
    return false;
  value = uint32_t(f->integer);
  return true;
}
const char* FlatJson::string(const char* key) const {
  const Field* f = find(key);
  return f && f->kind == Kind::String ? f->text : nullptr;
}
bool FlatJson::only(const char* const* keys, size_t size) const {
  for (size_t i = 0; i < count_; ++i) {
    bool known = false;
    for (size_t j = 0; j < size; ++j)
      if (!strcmp(fields_[i].key, keys[j])) {
        known = true;
        break;
      }
    if (!known)
      return false;
  }
  return true;
}
}
