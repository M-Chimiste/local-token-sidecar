"""QMI8658 6-axis IMU (accelerometer) for the Token Oracle.

Minimal, motion-focused: polls the accel over I2C and exposes a `moving`
binary_sensor (pickup detection), an optional `face_down` binary_sensor, and an
optional `accel_magnitude` debug sensor. Register config + motion algorithm are
ported from the verified Argus firmware (argus_input.c) for this exact board.
The QMI8658 is pure I2C at 0x6B (no INT/reset pins on the 2.8C) — poll only.
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor, i2c, sensor
from esphome.const import CONF_ID, DEVICE_CLASS_MOVING, STATE_CLASS_MEASUREMENT

CODEOWNERS = ["@athena"]
DEPENDENCIES = ["i2c"]
AUTO_LOAD = ["sensor", "binary_sensor"]

qmi8658_ns = cg.esphome_ns.namespace("qmi8658")
QMI8658Component = qmi8658_ns.class_(
    "QMI8658Component", cg.PollingComponent, i2c.I2CDevice
)

CONF_MOVING = "moving"
CONF_FACE_DOWN = "face_down"
CONF_ACCEL_MAGNITUDE = "accel_magnitude"
CONF_DEBUG = "debug"

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(QMI8658Component),
            cv.Optional(CONF_MOVING): binary_sensor.binary_sensor_schema(
                device_class=DEVICE_CLASS_MOVING
            ),
            cv.Optional(CONF_FACE_DOWN): binary_sensor.binary_sensor_schema(),
            cv.Optional(CONF_ACCEL_MAGNITUDE): sensor.sensor_schema(
                unit_of_measurement="g",
                accuracy_decimals=3,
                state_class=STATE_CLASS_MEASUREMENT,
            ),
            cv.Optional(CONF_DEBUG, default=False): cv.boolean,
        }
    )
    .extend(cv.polling_component_schema("50ms"))
    .extend(i2c.i2c_device_schema(0x6B))
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await i2c.register_i2c_device(var, config)
    cg.add(var.set_debug(config[CONF_DEBUG]))
    if CONF_MOVING in config:
        cg.add(var.set_moving_sensor(await binary_sensor.new_binary_sensor(config[CONF_MOVING])))
    if CONF_FACE_DOWN in config:
        cg.add(var.set_face_down_sensor(await binary_sensor.new_binary_sensor(config[CONF_FACE_DOWN])))
    if CONF_ACCEL_MAGNITUDE in config:
        cg.add(var.set_accel_mag_sensor(await sensor.new_sensor(config[CONF_ACCEL_MAGNITUDE])))
