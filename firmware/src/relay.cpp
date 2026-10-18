#include "relay.h"
#include "config.h"

static inline int relayLevel(bool on) {
#if RELAY_ACTIVE_LOW
    return on ? LOW : HIGH;
#else
    return on ? HIGH : LOW;
#endif
}

RelayController::RelayController() : currentState(false) {}

void RelayController::begin() {
    pinMode(RELAY_PIN, OUTPUT);
    // Fail-safe: relay OFF at boot
    off();
}

void RelayController::on() {
    digitalWrite(RELAY_PIN, relayLevel(true));
    currentState = true;
}

void RelayController::off() {
    digitalWrite(RELAY_PIN, relayLevel(false));
    currentState = false;
}

void RelayController::set(bool state) {
    state ? on() : off();
}

bool RelayController::state() {
    return currentState;
}