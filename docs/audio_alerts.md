# Browser Audio Alerts (Laptop Speakers)

Software-only audible anomaly notification for the AIoT Predictive Safety
Cockpit. Synthesized tones are played through the laptop's current audio output
using the **Web Audio API** — no buzzer, no speaker module, no GPIO, no audio
files, no npm packages, no extra Python dependencies, no firmware changes.

This feature is an **additional notification channel only**. It never influences
ML thresholds, relay actuation, MQTT, or the ESP32 safety automaton.

## 1. Architecture & data flow

```
Flask SSE /api/stream (dashboard/app.py)
   ├── event: snapshot    ──► AudioAlerts.seed(prediction)         silent sync (reload-safe)
   ├── event: prediction  ──► AudioAlerts.handlePrediction(payload) ─┐
   └── event: alert       ──► AudioAlerts.handleAlert(payload)     ─┤ (MQTT machine/alerts)
                                                                   ▼
                                        Incident Tracker (edge detector, dedup)
                                                                   ▼ alert decision
                                   AudioContext + Oscillator/Gain (tone synthesis)
                                                                   ▼
                                        window speakers  +  visual flash toast
```

Sources of truth discovered in the repo (no topic/field names invented):

| SSE event | Origin | Payload fields used |
|---|---|---|
| `prediction` | `app.py::broadcast("prediction", ...)` (local inference ~1 Hz and/or MQTT `machine/prediction`) | `state` (`NORMAL`/`WARN`/`CRITICAL`), `fault` (8-class taxonomy or `NONE`) |
| `alert` | MQTT `machine/alerts` from `ml_service/infer_realtime.py` | `level` (`WARN`/`CRIT`), `fault`, `msg` |
| `snapshot` | SSE connect/reconnect | `prediction` (used only to seed state silently) |

Files:

| File | Role |
|---|---|
| `dashboard/static/js/audio-alert.js` | Audio engine + incident tracker (isolated, UMD: `window.AudioAlerts` in browser, `require()`-able in Node) |
| `dashboard/static/js/dashboard.js` | Controls, SSE wiring, visual flash (`bindAudioControls`, `renderAudioStatus`, `showAudioFlash`) |
| `dashboard/templates/index.html` | Enable / Test / Mute buttons, status chip, flash toast |
| `dashboard/static/css/style.css` | `.audio-controls`, `.audio-btn`, `.audio-status`, `.audio-flash` styles |
| `dashboard/tests/audio_alert.test.js` | Zero-dependency automated tests (mocked Web Audio) |

## 2. Deduplication & escalation rules

The system has **no incident/event ID**, so a stable incident is derived from
`fault` + severity edge detection (documented in `audio-alert.js`):

1. `state: NORMAL` clears the active incident → no sound.
2. First anomaly for a fault → **one** alert (`kind: "new"`).
3. Repeats of the same fault/severity (every 1 Hz prediction) → **silence**.
4. Same fault at higher severity → **one** escalation alert (`kind: "escalation"`).
5. New fault class while another warning is active → alert, rate-limited by
   `warnFlapGuardMs` (3 s) so an oscillating classifier cannot alarm-loop.
   Critical alerts always fire.
6. `seed()` (SSE snapshot on load/reconnect) updates state **without** sounding,
   so a page reload never replays a historical alert.
7. Patterns are bounded by `maxAlertMs` (4 s) and `maxRepeats` (8); `stop()`
   cancels playback immediately. Only **one** `AudioContext` is ever created,
   and a new decision stops the previous tone (no overlaps).

Tone patterns (`AUDIO_CONFIG.patterns` in `audio-alert.js`):

| Event | Type | Pattern |
|---|---|---|
| Warning anomaly | `triangle` | 880→660 Hz, 170 ms, ×2 passes (~1.4 s) |
| Critical / escalation | `square` | 1046→784 Hz, 130 ms, ×4 passes (~2 s, urgent) |
| Activation test | `sine` | 740 Hz, 200 ms single tone |

Volume: `masterVolume: 0.35` (conservative lab default) — adjust in
`AUDIO_CONFIG`.

## 3. Using the controls (header, top-right)

| Control | Behaviour |
|---|---|
| **Enable Sound Alerts** | User gesture → creates/resumes the `AudioContext`, shows a green **Sound: Enabled** chip, plays a test tone. On failure the chip turns red with the real reason and *never* claims enabled. |
| **Test** | Replays the test tone while enabled. |
| **Mute / Unmute** | Silences (and immediately stops) dashboard audio. Detection, MQTT, logging, charts, and relay protection are untouched. |
| **Status chip** | `Not enabled` / `Enabled` / `Muted` / `click to restore` / error text. |
| **Flash toast** | Bottom-right notice on every alert decision (`New anomaly` / `Critical escalation`, with `(sound off)` when muted/disabled) + the Enable button pulses to invite activation. |

The preference `{enabled, muted}` persists in `localStorage`
(`aiot.soundAlerts`). After a reload the engine stays silent until the first
user gesture or an explicit button click (autoplay policy), and the still-active
incident is only seeded — never replayed.

## 4. Running & verifying (Windows / PowerShell)

```powershell
# one-time setup (creates ml_service\.venv and dashboard\.venv)
powershell -File scripts\setup_python.ps1

# start Mosquitto (separate terminal), then ML service + dashboard
powershell -File scripts\start_all.ps1
# open http://localhost:5000

# automated JS tests (Node 18+; no npm install required)
node dashboard\tests\audio_alert.test.js
node --test dashboard\tests\

# existing Python verification suite (unchanged behaviour)
dashboard\.venv\Scripts\python.exe scripts\verify_system.py
```

Hardware-free demo: the built-in digital twin runs by default — click any fault
scenario button to produce real `prediction` events.

## 5. Manual browser test checklist

Map 1:1 to the required acceptance checks. Run in Chrome/Edge/Firefox at
http://localhost:5000.

1. **Normal → no sound**: scenario `Nominal`; status stays silent for 30 s.
2. **New warning → one tone**: click `Imbalance`; exactly one warning pattern.
3. **New critical → urgent tone**: `Nominal` → `Critical`; distinguishable,
   louder-urgency repeating pattern (relay also trips, as before).
4. **No repeats**: stay on an active scenario ≥ 30 s (≈30 predictions) — silence
   after the first tone; page refresh → still silence (no historical replay).
5. **Escalation → one extra tone**: `Imbalance` → `Critical` plays one critical
   pattern only.
6. **Clear then new**: back to `Nominal`, wait for NORMAL, then `Overload` →
   one fresh warning tone.
7. **Mute**: click `Mute` while an alert plays → sound stops instantly; inject
   a new fault → flash + charts + log update, no sound; `Unmute`, then a *new*
   incident sounds again.
8. **Init failure**: block sound for the site (or use a Web Audio-less browser)
   → status shows the error, dashboard/charts/relay keep working, visual flash
   still appears.
9. **Reload safety**: with an active anomaly, press F5 → no sound replays; only
   future state changes can sound.
10. **System invariants**: relay auto-cutoff, MANUAL_RESET, simulator, XAI
    panel, fault log and charts behave exactly as before the feature.

## 6. Known limitations

- Sound is produced only while the dashboard tab is open, the laptop is on, and
  audio is enabled — it is a **notification**, never a safety mechanism. Relay
  shutdown stays fully autonomous in the ML service / ESP32.
- No incident ID exists in the payloads; identity is derived from
  `fault` + severity edges. A pathological classifier that oscillates between
  two warning classes faster than `warnFlapGuardMs` could still emit repeated
  (but rate-limited) warning tones.
- Browser autoplay policies require one user gesture before any sound can play
  (handled via the Enable button / first-click restore).
- If both the MQTT ML service and dashboard-local inference publish, their
  predictions converge on the same tracker, so a duplicated prediction still
  yields a single sound.
