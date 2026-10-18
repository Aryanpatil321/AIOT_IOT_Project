#include "sensors.h"
#include "config.h"

// Adafruit_MPU6050 reports acceleration in m/s^2; the model is trained in g.
static const float MPU6050_ACCEL_TO_G = 9.80665f;

Sensors::Sensors()
    : oneWire(DS18B20_PIN), ds18b20(&oneWire),
      vibSampleCount(0), lastVibSampleTime(0), windowStartTime(0),
      acs712Offset(ACS712_VREF_V), currentSum(0.0f), currentSampleCount(0),
      lastCurrentSampleTime(0), windowReady(false) {
    currentWindow = {0, 0, 0, 0, 0};
}

bool Sensors::begin() {
    // Initialize MPU6050
    if (!mpu.begin()) {
        Serial.println("[Sensors] MPU6050 init failed!");
        return false;
    }
    mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
    mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
    Serial.println("[Sensors] MPU6050 initialized.");

    // Initialize DS18B20
    ds18b20.begin();
    int deviceCount = ds18b20.getDeviceCount();
    if (deviceCount == 0) {
        Serial.println("[Sensors] DS18B20 not found!");
        return false;
    }
    Serial.printf("[Sensors] DS18B20 found (%d device(s)).\n", deviceCount);

    // ADC setup for ACS712
    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);

    windowStartTime = millis();
    return true;
}

void Sensors::calibrateACS712() {
    // Measure average ADC with no motor current to find zero offset
    const int samples = 500;
    long sum = 0;
    for (int i = 0; i < samples; i++) {
        sum += analogRead(ACS712_PIN);
        delay(2);
    }
    float avgAdc = (float)sum / samples;
    acs712Offset = (avgAdc / ACS712_ADC_RESOLUTION) * ACS712_ADC_VREF;
    Serial.printf("[Sensors] ACS712 calibrated. Offset: %.3f V (ADC avg: %.1f)\n",
                  acs712Offset, avgAdc);
}

float Sensors::computeMagnitude(float ax, float ay, float az) {
    return sqrtf(ax * ax + ay * ay + az * az);
}

float Sensors::adcToCurrent(int adcValue) {
    float voltage = (adcValue / ACS712_ADC_RESOLUTION) * ACS712_ADC_VREF;
    return (voltage - acs712Offset) / ACS712_SENSITIVITY_V_PER_A;
}

void Sensors::update() {
    // --- Vibration sampling (high rate) ---
    if (millis() - lastVibSampleTime >= (unsigned long)(1000.0 / VIB_SAMPLE_RATE_HZ)) {
        lastVibSampleTime = millis();
        mpu.getEvent(&a, &g, &temp_evt);
        float mag = computeMagnitude(a.acceleration.x, a.acceleration.y, a.acceleration.z);
        if (vibSampleCount < MAX_VIB_SAMPLES) {
            vibMagBuffer[vibSampleCount++] = mag;
        }
    }

    // Sample current periodically
    if (millis() - lastCurrentSampleTime >= 20) {
        lastCurrentSampleTime = millis();
        currentSum += adcToCurrent(analogRead(ACS712_PIN));
        currentSampleCount++;
    }

    // Check if window period elapsed
    if (millis() - windowStartTime >= SENSOR_WINDOW_MS) {
        computeRMSAndPeak();

        // Temperature (DS18B20)
        ds18b20.requestTemperatures();
        currentWindow.temp = ds18b20.getTempCByIndex(0);
        if (currentWindow.temp < -100) currentWindow.temp = 0;  // sensor error guard

        // Average current
        currentWindow.current = (currentSampleCount > 0)
            ? (currentSum / currentSampleCount) : 0.0f;

        currentWindow.ts = millis();
        windowReady = true;

        // Reset window accumulators
        vibSampleCount = 0;
        currentSum = 0.0f;
        currentSampleCount = 0;
        windowStartTime = millis();
    }
}

void Sensors::computeRMSAndPeak() {
    if (vibSampleCount == 0) {
        currentWindow.vib_rms = 0.0f;
        currentWindow.vib_peak = 0.0f;
        return;
    }
    // The magnitude vector always contains the 1g gravity DC component
    // (~9.81 m/s^2). Removing the window mean high-pass filters it out so
    // only true vibration remains, then convert m/s^2 -> g.
    float sum = 0.0f;
    for (int i = 0; i < vibSampleCount; i++) {
        sum += vibMagBuffer[i];
    }
    float mean = sum / vibSampleCount;

    float sumSq = 0.0f;
    float peak = 0.0f;
    for (int i = 0; i < vibSampleCount; i++) {
        float v = vibMagBuffer[i] - mean;
        sumSq += v * v;
        float av = fabsf(v);
        if (av > peak) peak = av;
    }
    currentWindow.vib_rms = sqrtf(sumSq / vibSampleCount) / MPU6050_ACCEL_TO_G;
    currentWindow.vib_peak = peak / MPU6050_ACCEL_TO_G;
}

bool Sensors::hasNewWindow() {
    return windowReady;
}

SensorReading Sensors::getWindowReading() {
    windowReady = false;
    return currentWindow;
}

float Sensors::getTemperature() {
    ds18b20.requestTemperatures();
    return ds18b20.getTempCByIndex(0);
}

float Sensors::getCurrentRaw() {
    return adcToCurrent(analogRead(ACS712_PIN));
}