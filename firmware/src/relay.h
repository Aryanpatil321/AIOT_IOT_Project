#ifndef RELAY_H
#define RELAY_H

#include <Arduino.h>

// Some relay modules are active-LOW. Set ACTIVE_LOW accordingly.
#define RELAY_ACTIVE_LOW true

class RelayController {
public:
    RelayController();
    void begin();
    void on();
    void off();
    void set(bool state);
    bool state();

private:
    bool currentState;
};

#endif // RELAY_H