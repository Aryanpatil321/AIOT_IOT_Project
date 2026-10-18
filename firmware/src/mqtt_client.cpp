#include "mqtt_client.h"
#include "config.h"
#include <ArduinoJson.h>

static void (*g_commandCallback)(const char* relay, const char* reason) = nullptr;
// Static instance pointer so the static PubSubClient callback can reach state.
static MqttManager* s_instance = nullptr;

MqttManager::MqttManager()
    : mqtt(wifiClient), lastReconnectAttempt(0), lastPredictionAt(0) {
    s_instance = this;
}

void MqttManager::begin() {
    mqtt.setServer(MQTT_SERVER, MQTT_PORT);
    mqtt.setCallback(MqttManager::mqttCallback);
    mqtt.setBufferSize(512);
}

bool MqttManager::connectWiFi() {
    if (WiFi.status() == WL_CONNECTED) return true;

    Serial.printf("[WiFi] Connecting to %s", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
        delay(500);
        Serial.print(".");
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[WiFi] Connected. IP: %s\n", WiFi.localIP().toString().c_str());
        return true;
    }
    Serial.println("[WiFi] Connection failed.");
    return false;
}

bool MqttManager::connectMqtt() {
    if (mqtt.connected()) return true;

    Serial.printf("[MQTT] Connecting to %s:%d...", MQTT_SERVER, MQTT_PORT);
    // Last Will: broker announces the device as offline if it drops unexpectedly.
    const char* willPayload = "{\"ts\":0,\"relay_state\":\"OFFLINE\",\"online\":false}";
    if (mqtt.connect(MQTT_CLIENT_ID, TOPIC_STATUS, 0, false, willPayload)) {
        Serial.println("connected.");
        mqtt.subscribe(TOPIC_COMMAND, 1);
        mqtt.subscribe(TOPIC_PREDICTION, 0);
        Serial.printf("[MQTT] Subscribed to %s + %s\n", TOPIC_COMMAND, TOPIC_PREDICTION);
        return true;
    }
    Serial.printf("failed (rc=%d)\n", mqtt.state());
    return false;
}

void MqttManager::ensureConnected() {
    if (WiFi.status() != WL_CONNECTED) {
        connectWiFi();
    }
    if (!mqtt.connected()) {
        unsigned long now = millis();
        if (now - lastReconnectAttempt >= MQTT_RECONNECT_DELAY_MS) {
            lastReconnectAttempt = now;
            connectMqtt();
        }
    }
}

void MqttManager::loop() {
    ensureConnected();
    if (mqtt.connected()) {
        mqtt.loop();
    }
}

bool MqttManager::publishSensors(const SensorReading& reading) {
    if (!mqtt.connected()) return false;

    JsonDocument doc;
    doc["ts"] = reading.ts;
    doc["temp"] = round(reading.temp * 100) / 100.0;
    doc["vib_rms"] = reading.vib_rms;
    doc["vib_peak"] = reading.vib_peak;
    doc["current"] = reading.current;

    char buffer[256];
    size_t n = serializeJson(doc, buffer, sizeof(buffer));
    return mqtt.publish(TOPIC_SENSORS, buffer, n);
}

bool MqttManager::publishStatus(int relayState, int wifiRssi) {
    if (!mqtt.connected()) return false;

    JsonDocument doc;
    doc["ts"] = millis();
    doc["relay"] = relayState;
    doc["relay_state"] = relayState ? "ON" : "OFF";
    doc["wifi_rssi"] = wifiRssi;
    doc["uptime_s"] = millis() / 1000;
    doc["free_heap"] = ESP.getFreeHeap();
    doc["ip_addr"] = WiFi.localIP().toString();
    doc["fw_version"] = "0.1.0";

    char buffer[320];
    size_t n = serializeJson(doc, buffer, sizeof(buffer));
    return mqtt.publish(TOPIC_STATUS, buffer, n);
}

bool MqttManager::publishCommand(const char* relay, const char* reason, const char* source) {
    if (!mqtt.connected()) return false;

    JsonDocument doc;
    doc["ts"] = millis();
    doc["relay"] = relay;
    doc["reason"] = reason;
    doc["source"] = source;

    char buffer[256];
    size_t n = serializeJson(doc, buffer, sizeof(buffer));
    return mqtt.publish(TOPIC_COMMAND, buffer, n);
}

void MqttManager::setCommandCallback(void (*cb)(const char* relay, const char* reason)) {
    g_commandCallback = cb;
}

unsigned long MqttManager::lastPredictionMs() const {
    return lastPredictionAt;
}

void MqttManager::mqttCallback(char* topic, byte* payload, unsigned int length) {
    char buf[256];
    unsigned int len = length < sizeof(buf) - 1 ? length : sizeof(buf) - 1;
    memcpy(buf, payload, len);
    buf[len] = '\0';

    // Track ML liveness from prediction heartbeats (published ~1 Hz).
    if (strcmp(topic, TOPIC_PREDICTION) == 0) {
        if (s_instance) s_instance->lastPredictionAt = millis();
        return;
    }

    if (strcmp(topic, TOPIC_COMMAND) != 0) return;

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, buf);
    if (err || !doc["relay"].is<const char*>()) return;

    // Ignore our own fallback commands echoed back to us.
    const char* source = doc["source"] | "";
    if (strcmp(source, "firmware_fallback") == 0) return;

    const char* relay = doc["relay"];
    const char* reason = doc["reason"] | "UNKNOWN";
    if (g_commandCallback) {
        g_commandCallback(relay, reason);
    }
}

bool MqttManager::isConnected() {
    return mqtt.connected();
}
