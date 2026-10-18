// AIoT Predictive Safety System - ESP32 Firmware Header
// sensors.h - MPU6050, DS18B20, ACS712 Sensor Interface

#ifndef SENSORS_H
#define SENSORS_H

#include <Arduino.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <OneWire.h>
#include <DallasTemperature.h>

struct SensorReading {
    float temp;        // °C
    float vib_rms;     // g
    float vib_peak;    // g
    float current;     // A
    unsigned long ts;  // timestamp (ms)
};

class Sensors {
public:
    Sensors();
    bool begin();
    void update();  // Called periodically to collect samples
    bool hasNewWindow();  // True when window is complete
    SensorReading getWindowReading();  // Get averaged/windowed features
    void calibrateACS712();  // Zero-current calibration
    float getTemperature();
    float getCurrentRaw();  // For debugging

private:
    // MPU6050
    Adafruit_MPU6050 mpu;
    sensors_event_t a, g, temp_evt;
    
    // DS18B20
    OneWire oneWire;
    DallasTemperature ds18b20;
    
    // Buffers for vibration window
    static const int MAX_VIB_SAMPLES = 256;
    float vibMagBuffer[MAX_VIB_SAMPLES];
    int vibSampleCount;
    unsigned long lastVibSampleTime;
    unsigned long windowStartTime;
    
    // Current sampling
    float acs712Offset;  // Zero-current ADC offset
    float currentSum;
    int currentSampleCount;
    unsigned long lastCurrentSampleTime;
    
    // Window state
    bool windowReady;
    SensorReading currentWindow;
    
    // Helper methods
    float computeMagnitude(float ax, float ay, float az);
    void computeRMSAndPeak();
    float adcToCurrent(int adcValue);
};

#endif // SENSORS_H
