#pragma once

#include "esphome/core/component.h"
#include "esphome/components/i2c/i2c.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/binary_sensor/binary_sensor.h"

namespace esphome {
namespace qmi8658 {

// QMI8658 accel-only poller. Motion algorithm ported from Argus argus_input.c:
// a fast low-pass tracks gravity; deviation from it is "motion"; a sustained
// departure from 1g flags pickup. Scale is irrelevant (we normalize) — only
// relative change and direction matter.
class QMI8658Component : public PollingComponent, public i2c::I2CDevice {
 public:
  void setup() override;
  void update() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

  void set_moving_sensor(binary_sensor::BinarySensor *s) { this->moving_ = s; }
  void set_face_down_sensor(binary_sensor::BinarySensor *s) { this->face_down_ = s; }
  void set_accel_mag_sensor(sensor::Sensor *s) { this->accel_mag_ = s; }
  void set_debug(bool d) { this->debug_ = d; }

 protected:
  binary_sensor::BinarySensor *moving_{nullptr};
  binary_sensor::BinarySensor *face_down_{nullptr};
  sensor::Sensor *accel_mag_{nullptr};
  bool debug_{false};
  bool ok_{false};

  bool lp_init_{false};
  float lp_x_{0}, lp_y_{0}, lp_z_{0};  // fast low-pass gravity estimate (g)
  float pickup_t_{0};                  // seconds spent "moving"
  uint32_t last_us_{0};
  float dbg_t_{0};
};

}  // namespace qmi8658
}  // namespace esphome
