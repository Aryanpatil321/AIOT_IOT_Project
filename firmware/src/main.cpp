#include <Arduino.h>
#include "config.h"
#include "sensors.h"
#include "mqtt_client.h"
#include "relay.h"

Sensors sensors;
MqttManager mqtt;
RelayController relay;

// Safety state
static bool criticalLatched = false;
static int consecutiveCritical = 0;
static int fallbackViolations = 0;

// Forward declarations
void onCommand(const char* relayCmd, const char* reason);
void runLocalFallback(const SensorReading& r);
bool fallbackCondition(const SensorReading& r);

void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("\n=== AIoT Predictive Safety System ===");
    Serial.printf("Firmware v0.1.0 | Built %s\n", __DATE__);

    // Relay first (fail-safe OFF)
    relay.begin();
    Serial.println("[Relay] Initialized OFF (fail-safe)");

    // Sensors
    if (!sensors.begin()) {
        Serial.println("[FATAL] Sensor initialization failed. Halting.");
        while (1) { delay(1000); }
    }

    // Zero-current calibration: must run with the motor OFF (relay is OFF at
    // this point), otherwise the offset would absorb the running current.
    sensors.calibrateACS712();

    // Connectivity
    mqtt.begin();
    mqtt.setCommandCallback(onCommand);
    mqtt.connectWiFi();
    mqtt.connectMqtt();

    Serial.println("[System] Setup complete. Entering main loop.\n");
}

void loop() {
    // Drive sensor sampling FIRST: update() fills the window buffers and
    // raises windowReady once SENSOR_WINDOW_MS of data has been collected.
    sensors.update();
    mqtt.ensureConnected();

    // Read sensor window when ready
    if (sensors.hasNewWindow()) {
        SensorReading r = sensors.getWindowReading();

        // Publish telemetry
        mqtt.publishSensors(r);

        // Log to serial (debug)
        Serial.printf("T=%.1fC Vrms=%.3f Vpk=%.3f I=%.3fA\n",
                      r.temp, r.vib_rms, r.vib_peak, r.current);

        // Local fallback if ML is silent
        runLocalFallback(r);
    }

    // Periodic status heartbeat
    static unsigned long lastStatus = 0;
    if (millis() - lastStatus >= STATUS_PUBLISH_INTERVAL_MS) {
        lastStatus = millis();
        mqtt.publishStatus(relay.state() ? 1 : 0, WiFi.RSSI());
    }

    mqtt.loop();
    delay(10);
}

bool fallbackCondition(const SensorReading& r) {
    return (r.vib_rms > FALLBACK_VIB_RMS_CRIT ||
            r.current > FALLBACK_CURRENT_CRIT ||
            r.temp > FALLBACK_TEMP_CRIT);
}

void runLocalFallback(const SensorReading& r) {
    // Trust the ML layer while it is publishing predictions (~1 Hz).
    bool mlActive = (millis() - mqtt.lastPredictionMs()) < ML_TIMEOUT_MS;
    if (mlActive) {
        fallbackViolations = 0;
        return;
    }

    if (fallbackCondition(r)) {
        fallbackViolations++;
    } else {
        fallbackViolations = 0;
    }

    if (fallbackViolations >= FALLBACK_CONSECUTIVE_COUNT && !criticalLatched) {
        Serial.println("[SAFETY] ML silent + fallback threshold exceeded -> RELAY OFF");
        relay.off();
        criticalLatched = true;
        mqtt.publishCommand("OFF", "ML_TIMEOUT_FALLBACK", "firmware_fallback");
    }
}

void onCommand(const char* relayCmd, const char* reason) {
    if (strcmp(relayCmd, "OFF") == 0) {
        // Firmware-side debounce: require CRITICAL_DEBOUNCE_COUNT consecutive
        // critical OFF commands before latching (single glitch immunity).
        if (strcmp(reason, "CRITICAL_FAULT") == 0) {
            consecutiveCritical++;
            if (consecutiveCritical < CRITICAL_DEBOUNCE_COUNT) {
                Serial.printf("[CMD] OFF (reason: %s) %d/%d - debouncing, not latched yet\n",
                              reason, consecutiveCritical, CRITICAL_DEBOUNCE_COUNT);
                relay.off();  // still cut power immediately; debounce only gates the latch
                return;
            }
        }
        Serial.printf("[CMD] Relay OFF (reason: %s)\n", reason);
        relay.off();
        // Latch if due to critical fault; requires manual reset to re-enable
        if (strcmp(reason, "CRITICAL_FAULT") == 0) {
            criticalLatched = true;
        }
    } else if (strcmp(relayCmd, "ON") == 0) {
        if (criticalLatched && strcmp(reason, "MANUAL_RESET") != 0) {
            Serial.println("[CMD] Ignored ON: critical latched, manual reset required.");
            return;
        }
        Serial.printf("[CMD] Relay ON (reason: %s)\n", reason);
        relay.on();
        criticalLatched = false;
        consecutiveCritical = 0;
        fallbackViolations = 0;
    }
}