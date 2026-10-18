# AIoT Predictive Safety System - Wiring Guide

## Hardware Connections

### ESP32 Pin Mapping

| Component | ESP32 Pin | Notes |
|---|---|---|
| MPU6050 VCC | 3.3V (or 5V on GY-521) | GY-521 module has an onboard 3.3V regulator; bare chip is 3.3V-only |
| MPU6050 GND | GND | Common ground |
| MPU6050 SCL | GPIO 22 | I2C Clock (Green wire) |
| MPU6050 SDA | GPIO 21 | I2C Data (Yellow wire) |
| DS18B20 VCC | 5V | From 5V supply |
| DS18B20 GND | GND | Common ground |
| DS18B20 DATA | GPIO 4 | 1-Wire Data (Light Blue wire) |
| DS18B20 PULL-UP | Between DATA & 3.3V | 4.7kΩ resistor (CRITICAL) |
| ACS712 VCC | 5V | From 5V supply |
| ACS712 GND | GND | Common ground |
| ACS712 OUT | GPIO 34 | ADC Input (Orange wire) |
| Relay VCC | 5V | From separate 5V supply |
| Relay GND | GND | Common ground (tied to ESP32 GND) |
| Relay IN (Signal) | GPIO 27 | Control signal (Purple wire) |
| Motor Power | Through Relay COM/NO | See motor connection below |

### Wire Color Guide

| Purpose | Color (Recommended) |
|---|---|
| 5V (VCC) | Red |
| GND | Black |
| I2C SCL | Green |
| I2C SDA | Yellow |
| 1-Wire DATA (DS18B20) | Light Blue |
| ACS712 OUT | Orange |
| Relay IN | Purple |

## Power Supply Considerations (CRITICAL SAFETY)

**⚠️ IMPORTANT SAFETY NOTE:**
This prototype is designed for **low-voltage DC motors only (6-12V)**. DO NOT interface with mains/AC electricity.

### Recommended Setup:

1. **Logic Power**: ESP32 can be powered via USB (5V) or 5V pin
2. **Motor & Relay Power**: Use a **separate 5V supply** (e.g., 5V 2A adapter, or 6-12V with buck converter) for the relay and DC motor
3. **Common Ground**: Connect the GND of the separate motor supply to the ESP32 GND - this is essential for proper relay triggering
4. **Current Requirements**: DC motors can draw high inrush current. Ensure your power supply can handle it

### Relay Connection Example:

```
[5V Motor Supply +] ──┬── [Motor +]
                    │
                  [Relay COM] (Common)
                    │
                  [Relay NO] (Normally Open)
                    │
[5V Motor Supply -] ──┴── [Motor -]
                    │
                  [Shared GND with ESP32]
```

**Why NO (Normally Open)?** So motor is OFF by default when relay is de-energized - this provides fail-safe behavior.

## Sensor Specific Notes

### MPU6050 (Vibration)
- Uses I2C bus (SDA/SCL). Can be daisy-chained with other I2C devices if needed
- Default I2C address: 0x68
- On ESP32, I2C is software-configurable; we're using default GPIO21/22

### DS18B20 (Temperature)
- **REQUIRED**: 4.7kΩ pull-up resistor between DATA (GPIO4) and **3.3V** (NOT 5V — ESP32 GPIOs are not 5V-tolerant; a 5V pull-up would put 5V on GPIO4 at idle)
- Uses OneWire protocol - multiple DS18B20 can share same data line with unique addresses
- For single sensor setup, simple as shown

### ACS712 (Current)
- Measures current through the motor circuit. Connect in series: Motor Supply → ACS712 (IN+) → ACS712 (OUT+) → Motor, and share GND
- **Calibration required**: Need to measure zero-current offset during boot (no motor running)
- ESP32 ADC is 12-bit (0-4095) and operates at 3.3V. GPIO34 is an input-only ADC pin - ideal for sensor readings
- Avoid using GPIO34/35 during boot if pulled high/low

### Relay Module
- Most 5V relay modules are active-LOW (triggered when IN pin goes LOW). Check your module - if active-HIGH, adjust in firmware
- Opto-isolated modules are recommended for additional protection
- Ensure relay contacts are rated for your motor's voltage/current

## Quick Verification Checklist

- [ ] All GNDs connected together (ESP32, sensors, motor supply)
- [ ] DS18B20 has 4.7kΩ pull-up between DATA and 3.3V
- [ ] ACS712 connected in series with motor load (not parallel)
- [ ] Relay uses separate 5V supply with common GND
- [ ] GPIO34 is used only for ADC (ACS712) - input-only
- [ ] Motor is low-voltage DC (6-12V) for testing
- [ ] No connections to AC mains
- [ ] Relay COM/NO configured for fail-safe (OFF when de-energized)

## Testing with Low-Power Load First

**Before connecting your main motor:**
1. Test with a small DC motor (e.g., 6V toy motor) or even an LED+resistor via relay for logic testing
2. Verify sensor readings in serial monitor
3. Calibrate ACS712 at zero-current
4. Test relay toggle via manual command
5. Only progress to full load after confirming basic operation

## References

- ESP32 Pinout: https://docs.espressif.com/projects/esp-idf/en/latest/esp32/_images/esp32-devkitC-v4-pinout.png
- MPU6050 Datasheet: InvenSense MPU-6000/MPU-6050
- DS18B20 Datasheet: Maxim Integrated
- ACS712 Datasheet: Allegro Microsystems
