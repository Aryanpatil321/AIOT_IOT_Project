#ifndef MQTT_CLIENT_H
#define MQTT_CLIENT_H

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include "sensors.h"

class MqttManager {
public:
    MqttManager();
    void begin();
    bool connectWiFi();
    bool connectMqtt();
    void ensureConnected();
    void loop();

    bool publishSensors(const SensorReading& reading);
    bool publishStatus(int relayState, int wifiRssi);
    bool publishCommand(const char* relay, const char* reason, const char* source);

    // Callback registration for incoming commands
    void setCommandCallback(void (*cb)(const char* relay, const char* reason));

    // Timestamp (millis) of the last ML prediction seen on the bus.
    // Used by the local fallback to know whether the ML layer is alive.
    unsigned long lastPredictionMs() const;

    bool isConnected();

private:
    WiFiClient wifiClient;
    PubSubClient mqtt;
    unsigned long lastReconnectAttempt;
    unsigned long lastPredictionAt;  // millis() of last machine/prediction message

    static void mqttCallback(char* topic, byte* payload, unsigned int length);
};

#endif // MQTT_CLIENT_H
