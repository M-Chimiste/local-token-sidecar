#include "qmi8658.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include <cmath>

namespace esphome {
namespace qmi8658 {

static const char *const TAG = "qmi8658";

// Registers we use (QMI8658, see Argus argus_input.c).
static const uint8_t REG_WHO_AM_I = 0x00;  // -> 0x05
static const uint8_t REG_CTRL1 = 0x02;
static const uint8_t REG_CTRL2 = 0x03;
static const uint8_t REG_CTRL5 = 0x06;
static const uint8_t REG_CTRL7 = 0x08;
static const uint8_t REG_AX_L = 0x35;  // AX_L..AZ_H, little-endian int16

// Motion thresholds (Argus-tuned).
static const float PICKUP_DEV_G = 0.18f;
static const float PICKUP_SUSTAIN_S = 0.12f;
static const float FACE_DOWN_FACE = 0.80f;
static const float ACCEL_LSB_G = 4.0f / 32768.0f;  // ±4g full scale

void QMI8658Component::setup() {
  uint8_t who = 0;
  this->read_register(REG_WHO_AM_I, &who, 1);
  ESP_LOGI(TAG, "QMI8658 WHO_AM_I=0x%02X (expect 0x05)", who);
  this->write_byte(REG_CTRL1, 0x40);  // address auto-increment for burst read
  this->write_byte(REG_CTRL7, 0x00);  // disable while configuring
  this->write_byte(REG_CTRL2, 0x15);  // accel ±4g, 250Hz ODR
  this->write_byte(REG_CTRL5, 0x01);  // accel low-pass filter on
  this->write_byte(REG_CTRL7, 0x01);  // enable accel
  this->ok_ = (who == 0x05);
  if (!this->ok_) {
    ESP_LOGW(TAG, "QMI8658 not detected at 0x%02X; IMU features disabled", this->address_);
    this->mark_failed();
    return;
  }
  this->last_us_ = micros();
}

void QMI8658Component::update() {
  if (!this->ok_)
    return;

  uint8_t b[6];
  if (this->read_register(REG_AX_L, b, 6) != i2c::ERROR_OK)
    return;

  int16_t rx = (int16_t) ((b[1] << 8) | b[0]);
  int16_t ry = (int16_t) ((b[3] << 8) | b[2]);
  int16_t rz = (int16_t) ((b[5] << 8) | b[4]);
  float ax = rx * ACCEL_LSB_G, ay = ry * ACCEL_LSB_G, az = rz * ACCEL_LSB_G;
  float mag = sqrtf(ax * ax + ay * ay + az * az);
  if (mag < 1e-3f)
    return;

  uint32_t now = micros();
  float dt = (now - this->last_us_) / 1e6f;
  this->last_us_ = now;
  if (dt <= 0.0f || dt > 1.0f)
    dt = 0.05f;

  if (!this->lp_init_) {
    this->lp_x_ = ax;
    this->lp_y_ = ay;
    this->lp_z_ = az;
    this->lp_init_ = true;
  }
  const float alpha = 0.25f;
  this->lp_x_ += (ax - this->lp_x_) * alpha;
  this->lp_y_ += (ay - this->lp_y_) * alpha;
  this->lp_z_ += (az - this->lp_z_) * alpha;
  float dx = ax - this->lp_x_, dy = ay - this->lp_y_, dz = az - this->lp_z_;
  float dev = sqrtf(dx * dx + dy * dy + dz * dz);

  bool moving = (fabsf(mag - 1.0f) > PICKUP_DEV_G) || (dev > PICKUP_DEV_G);
  this->pickup_t_ = moving ? (this->pickup_t_ + dt) : 0.0f;
  bool picked_up = this->pickup_t_ > PICKUP_SUSTAIN_S;

  // screen-normal axis; on this board az ~ +1g when face-down (Argus note).
  float face = az / mag;
  bool face_down = face > FACE_DOWN_FACE;

  if (this->moving_ != nullptr)
    this->moving_->publish_state(picked_up);
  if (this->face_down_ != nullptr)
    this->face_down_->publish_state(face_down);
  if (this->accel_mag_ != nullptr)
    this->accel_mag_->publish_state(mag);

  if (this->debug_) {
    this->dbg_t_ += dt;
    if (this->dbg_t_ >= 1.0f) {
      this->dbg_t_ = 0.0f;
      ESP_LOGI(TAG,
               "ax=%+.2f ay=%+.2f az=%+.2f | mag=%.2f dev=%.2f gx=%+.2f gy=%+.2f face=%+.2f down=%d",
               ax, ay, az, mag, dev, ax / mag, ay / mag, face, (int) face_down);
    }
  }
}

void QMI8658Component::dump_config() {
  ESP_LOGCONFIG(TAG, "QMI8658 IMU:");
  LOG_I2C_DEVICE(this);
  ESP_LOGCONFIG(TAG, "  Detected: %s", this->ok_ ? "yes" : "NO");
  LOG_UPDATE_INTERVAL(this);
}

}  // namespace qmi8658
}  // namespace esphome
