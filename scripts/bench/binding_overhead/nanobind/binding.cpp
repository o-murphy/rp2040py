#include <nanobind/nanobind.h>
#include <cstdint>
#include <cstring>
extern "C" {
int32_t rp2040_boot(); void rp2040_erase_flash(); uint8_t* rp2040_sram();
int64_t rp2040_run_n(uint32_t); uint32_t rp2040_register(uint32_t); void rp2040_set_register(uint32_t, uint32_t);
}
namespace nb = nanobind;
NB_MODULE(nbcore, m) {
  m.def("boot", &rp2040_boot);
  m.def("erase_flash", &rp2040_erase_flash);
  m.def("run_n", &rp2040_run_n);
  m.def("register", &rp2040_register);
  m.def("set_register", &rp2040_set_register);
  m.def("write_sram", [](uint32_t off, nb::bytes b){ std::memcpy(rp2040_sram()+off, b.c_str(), b.size()); });
}
