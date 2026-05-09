/*
 * unified_controller.ino
 * GoPro Web Controller — Unified Arduino Firmware
 *
 * Merges stepper_motor_controller.ino + rgb_gui_controller.ino
 *
 * Pin assignments (Arduino Mega):
 *   Rail stepper:  DIR=2,  STEP=3
 *   Base servo:    pin 52
 *   Camera servo:  pin 36
 *   RGB LED:       R=9, G=10, B=11  (default; changed with PINS:r,g,b)
 *
 * ─── Serial Protocol (9600 baud, newline-terminated) ─────────────────────────
 *   P              → "PONG"
 *   S<delay>,<dur> → set step delay (µs) and duration (ms)  e.g. S91,10000
 *   U              → spin forward (DIR=HIGH) for spinDuration ms
 *   D              → spin backward (DIR=LOW)
 *   X              → stop mid-spin
 *   BUS<us>        → base servo microseconds         e.g. BUS1500
 *   CUS<us>        → camera servo microseconds       e.g. CUS1500
 *   B<angle>       → base servo angle 0-180          e.g. B90
 *   C<angle>       → camera servo angle              e.g. C90
 *   b / c / a      → sweep base / camera / both
 *   n              → center both servos (1500µs)
 *   PINS:r,g,b     → set active LED pins             → "OK:PINS"
 *   RGB:r,g,b      → set color (0-255 each)          → "OK:RGB"
 *   EFFECT:name    → RAINBOW / FADE / STOP           → "OK:EFFECT"
 */

#include <Servo.h>
#include <math.h>

// ─── Pin Assignments ──────────────────────────────────────────────────────────
#define DIR_PIN   2
#define STEP_PIN  3
#define BASE_PIN  52
#define CAM_PIN   36

#define LED_R_DEFAULT  8
#define LED_G_DEFAULT  9
#define LED_B_DEFAULT 10

const bool COMMON_ANODE = false;

// ─── State ────────────────────────────────────────────────────────────────────
Servo baseServo;
Servo camServo;

int  stepDelay   = 91;    // µs between steps
long spinDuration = 10000; // ms to spin

uint8_t ledPinR = LED_R_DEFAULT;
uint8_t ledPinG = LED_G_DEFAULT;
uint8_t ledPinB = LED_B_DEFAULT;
uint8_t ledR = 0, ledG = 0, ledB = 0;

enum EffectMode { EFF_NONE, EFF_RAINBOW, EFF_FADE };
EffectMode currentEffect = EFF_NONE;
unsigned long effectStart = 0;
uint16_t effectStep = 0;

String inputString   = "";
bool   stringComplete = false;

// ─── LED helpers ─────────────────────────────────────────────────────────────

static inline uint8_t gamma8(uint8_t v) {
    return (uint16_t(v) * uint16_t(v)) / 255;
}

void writeLED(uint8_t r, uint8_t g, uint8_t b) {
    uint8_t rg = gamma8(r), gg = gamma8(g), bg = gamma8(b);
    if (COMMON_ANODE) {
        analogWrite(ledPinR, 255 - rg);
        analogWrite(ledPinG, 255 - gg);
        analogWrite(ledPinB, 255 - bg);
    } else {
        analogWrite(ledPinR, rg);
        analogWrite(ledPinG, gg);
        analogWrite(ledPinB, bg);
    }
    ledR = r; ledG = g; ledB = b;
}

void hsvToRgb(uint16_t h, uint8_t s, uint8_t v,
              uint8_t &r, uint8_t &g, uint8_t &b) {
    if (s == 0) { r = g = b = v; return; }
    h %= 360;
    float hf = h / 60.0f;
    int   i  = int(hf);
    float f  = hf - i;
    float sv = s / 255.0f;
    float pv = v * (1.0f - sv);
    float qv = v * (1.0f - sv * f);
    float tv = v * (1.0f - sv * (1.0f - f));
    switch (i) {
        case 0: r=v;  g=tv; b=pv; break;
        case 1: r=qv; g=v;  b=pv; break;
        case 2: r=pv; g=v;  b=tv; break;
        case 3: r=pv; g=qv; b=v;  break;
        case 4: r=tv; g=pv; b=v;  break;
        default: r=v; g=pv; b=qv; break;
    }
}

void setPins(uint8_t r, uint8_t g, uint8_t b) {
    analogWrite(ledPinR, COMMON_ANODE ? 255 : 0);
    analogWrite(ledPinG, COMMON_ANODE ? 255 : 0);
    analogWrite(ledPinB, COMMON_ANODE ? 255 : 0);
    ledPinR = r; ledPinG = g; ledPinB = b;
    pinMode(ledPinR, OUTPUT);
    pinMode(ledPinG, OUTPUT);
    pinMode(ledPinB, OUTPUT);
}

// ─── Rail ─────────────────────────────────────────────────────────────────────

void spinMotor(int direction, long durationMs) {
    digitalWrite(DIR_PIN, direction);
    unsigned long startTime = millis();
    unsigned long endTime   = startTime + durationMs;
    int startDelay = max(stepDelay, 500);

    while (millis() < endTime) {
        // Check for stop or servo commands mid-spin
        if (Serial.available()) {
            String cmd = Serial.readStringUntil('\n');
            cmd.trim();
            if (cmd == "X") { Serial.println("STOPPED"); return; }
            if      (cmd.startsWith("BUS")) baseServo.writeMicroseconds(constrain(cmd.substring(3).toInt(), 400, 2600));
            else if (cmd.startsWith("CUS")) camServo.writeMicroseconds(constrain(cmd.substring(3).toInt(), 400, 2600));
            else if (cmd.startsWith("B"))   baseServo.write(constrain(cmd.substring(1).toInt(), 0, 180));
            else if (cmd.startsWith("C"))   camServo.write(constrain(cmd.substring(1).toInt(), 0, 180));
        }

        long elapsed = (long)(millis() - startTime);
        int  curDelay = (elapsed < 500)
            ? startDelay - (int)((long)(startDelay - stepDelay) * elapsed / 500)
            : stepDelay;

        digitalWrite(STEP_PIN, HIGH);
        delayMicroseconds(curDelay);
        digitalWrite(STEP_PIN, LOW);
        delayMicroseconds(curDelay);
    }
    Serial.println("OK");
}

// ─── Servo sweep ─────────────────────────────────────────────────────────────

void sweepServo(Servo &sv, int minUs, int maxUs) {
    for (int us = minUs; us <= maxUs; us += 20) { sv.writeMicroseconds(us); delay(20); }
    for (int us = maxUs; us >= minUs; us -= 20) { sv.writeMicroseconds(us); delay(20); }
    sv.writeMicroseconds((minUs + maxUs) / 2);
}

// ─── Command handler ─────────────────────────────────────────────────────────

void handleCommand(String line) {
    line.trim();
    if (line.length() == 0) return;

    if (line == "P")  { Serial.println("PONG"); return; }
    if (line == "X")  { Serial.println("STOPPED"); return; }  // idle stop

    if (line == "U")  { spinMotor(HIGH, spinDuration); return; }
    if (line == "D")  { spinMotor(LOW,  spinDuration); return; }

    if (line == "n")  {
        baseServo.writeMicroseconds(1500);
        camServo.writeMicroseconds(1500);
        Serial.println("Centered");
        return;
    }
    if (line == "b")  { sweepServo(baseServo, 500, 2500); return; }
    if (line == "c")  { sweepServo(camServo,  500, 2500); return; }
    if (line == "a")  { sweepServo(baseServo, 500, 2500); delay(300); sweepServo(camServo, 500, 2500); return; }

    // S<delay>,<duration>
    if (line.charAt(0) == 'S' && line.length() > 1) {
        int comma = line.indexOf(',');
        if (comma > 1) {
            int  nd = line.substring(1, comma).toInt();
            long dur = line.substring(comma + 1).toInt();
            if (nd >= 10 && dur > 0) {
                stepDelay    = nd;
                spinDuration = dur;
                Serial.print("OK "); Serial.print(stepDelay);
                Serial.print(","); Serial.println(spinDuration);
            } else { Serial.println("ERROR"); }
        }
        return;
    }

    // BUS / B
    if (line.startsWith("BUS")) { baseServo.writeMicroseconds(constrain(line.substring(3).toInt(), 400, 2600)); return; }
    if (line.charAt(0) == 'B' && line.length() > 1) { baseServo.write(constrain(line.substring(1).toInt(), 0, 180)); return; }

    // CUS / C
    if (line.startsWith("CUS")) { camServo.writeMicroseconds(constrain(line.substring(3).toInt(), 400, 2600)); return; }
    if (line.charAt(0) == 'C' && line.length() > 1) { camServo.write(constrain(line.substring(1).toInt(), 0, 180)); return; }

    // PINS:r,g,b
    if (line.startsWith("PINS:")) {
        String s = line.substring(5);
        int c1 = s.indexOf(','), c2 = s.indexOf(',', c1 + 1);
        if (c1 > 0 && c2 > 0)
            setPins(constrain(s.substring(0, c1).toInt(), 0, 53),
                    constrain(s.substring(c1+1, c2).toInt(), 0, 53),
                    constrain(s.substring(c2+1).toInt(), 0, 53));
        Serial.print("OK:PINS:"); Serial.print(ledPinR); Serial.print(",");
        Serial.print(ledPinG); Serial.print(","); Serial.println(ledPinB);
        return;
    }

    // RGB:r,g,b
    if (line.startsWith("RGB:")) {
        currentEffect = EFF_NONE;
        String s = line.substring(4);
        int c1 = s.indexOf(','), c2 = s.indexOf(',', c1 + 1);
        if (c1 > 0 && c2 > 0)
            writeLED(constrain(s.substring(0, c1).toInt(), 0, 255),
                     constrain(s.substring(c1+1, c2).toInt(), 0, 255),
                     constrain(s.substring(c2+1).toInt(), 0, 255));
        Serial.println("OK:RGB");
        return;
    }

    // EFFECT:name
    if (line.startsWith("EFFECT:")) {
        String eff = line.substring(7);
        eff.toUpperCase();
        if      (eff == "RAINBOW") { currentEffect = EFF_RAINBOW; effectStart = millis(); effectStep = 0; }
        else if (eff == "FADE")    { currentEffect = EFF_FADE;    effectStart = millis(); effectStep = 0; }
        else                       { currentEffect = EFF_NONE;    writeLED(0, 0, 0); }
        Serial.println("OK:EFFECT");
        return;
    }
}

// ─── Setup ────────────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(9600);
    Serial.setTimeout(100);
    inputString.reserve(64);

    pinMode(DIR_PIN,  OUTPUT);
    pinMode(STEP_PIN, OUTPUT);

    baseServo.attach(BASE_PIN, 500, 2500);
    camServo.attach(CAM_PIN,   500, 2500);
    baseServo.writeMicroseconds(1500);
    camServo.writeMicroseconds(1500);

    // Servo library sets Timer1 to normal-counting mode, breaking analogWrite
    // on pin 11 (OC1A) and pin 12 (OC1B).  Reset Timer1 back to Arduino's
    // default 8-bit phase-correct PWM.  Safe when servos are on Timer5
    // (current Servo library default for Mega); if servos were on Timer1
    // (old library) servo signals will stop — update your Servo library.
    TIMSK1 &= ~_BV(OCIE1A);           // release any Servo Timer1 ISR
    TCCR1A  = _BV(WGM10);             // 8-bit phase-correct PWM
    TCCR1B  = _BV(CS11) | _BV(CS10); // prescaler /64 → ~490 Hz

    pinMode(ledPinR, OUTPUT);
    pinMode(ledPinG, OUTPUT);
    pinMode(ledPinB, OUTPUT);
    analogWrite(ledPinR, COMMON_ANODE ? 255 : 0);
    analogWrite(ledPinG, COMMON_ANODE ? 255 : 0);
    analogWrite(ledPinB, COMMON_ANODE ? 255 : 0);

    Serial.println("READY");
}

// ─── Loop ─────────────────────────────────────────────────────────────────────

void loop() {
    if (stringComplete) {
        handleCommand(inputString);
        inputString    = "";
        stringComplete = false;
    }

    // LED effects — non-blocking ~20 ms tick
    if (currentEffect != EFF_NONE) {
        static unsigned long lastTick = 0;
        if (millis() - lastTick >= 20) {
            lastTick = millis();
            if (currentEffect == EFF_RAINBOW) {
                uint8_t r, g, b;
                hsvToRgb(effectStep, 255, 255, r, g, b);
                writeLED(r, g, b);
                effectStep = (effectStep + 2) % 360;
            } else if (currentEffect == EFF_FADE) {
                float br = (sin(effectStep * 0.05f) + 1.0f) / 2.0f;
                writeLED((uint8_t)(ledR * br), (uint8_t)(ledG * br), (uint8_t)(ledB * br));
                effectStep = (effectStep + 1) % 126;
            }
        }
    }
}

// ─── Serial ISR ───────────────────────────────────────────────────────────────

void serialEvent() {
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n') {
            stringComplete = true;
        } else if (c != '\r') {
            inputString += c;
        }
    }
}
