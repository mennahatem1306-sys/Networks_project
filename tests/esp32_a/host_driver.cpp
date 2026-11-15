#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>

#include "ProductionStation.h"

class SerialCapture final : public mct431::StationOutput {
 public:
  int failAfter = -1;
  bool transmit(const uint8_t* bytes, size_t size) override {
    if (failAfter == 0) {
      failAfter = -1;
      return false;
    }
    if (failAfter > 0)
      --failAfter;
    std::cout << "TX ";
    for (size_t i = 0; i < size; ++i)
      std::cout << std::hex << std::setw(2) << std::setfill('0') << unsigned(bytes[i]);
    std::cout << std::dec << '\n';
    return true;
  }
  void diagnostic(uint32_t now, const char* event, uint32_t cmd, uint32_t part,
                  uint32_t retry) override {
    std::cout << "LOG " << now << ' ' << event << ' ' << cmd << ' ' << part << ' ' << retry << '\n';
  }
};

int main(int argc, char**) {
  static SerialCapture output;
  static mct431::ProductionStation station(output);
  station.begin(7, argc == 1, 0);
  std::cout << "DONE\n" << std::flush;
  for (std::string line; std::getline(std::cin, line);) {
    std::istringstream in(line);
    std::string operation;
    in >> operation;
    if (operation == "RX") {
      uint32_t now;
      std::string hex;
      in >> now >> hex;
      for (size_t i = 0; i + 1 < hex.size(); i += 2)
        station.receive(uint8_t(std::stoul(hex.substr(i, 2), nullptr, 16)), now);
    } else if (operation == "TICK") {
      uint32_t now;
      in >> now;
      station.tick(now);
    } else if (operation == "FAIL_AFTER") {
      in >> output.failAfter;
    } else if (operation == "STATE") {
      std::cout << "STATE " << station.state() << ' ' << station.produced() << ' '
                << station.session() << ' ' << station.synchronized() << ' ' << station.lastError()
                << '\n';
    } else if (operation == "CRC") {
      std::cout << "CRC " << mct431::crc16(reinterpret_cast<const uint8_t*>("123456789"), 9)
                << '\n';
    } else
      return 2;
    std::cout << "DONE\n" << std::flush;
  }
}
