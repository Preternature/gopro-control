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
 *   TMC?           → TMC2209 driver status report    → "TMC:OK ..." / "TMC:NOT_CONNECTED"
 *   CUR<mA>        → set motor RMS current via UART  e.g. CUR800  (overrides VREF pot)
 *   TMODE:STEALTH  → quiet stealthChop mode          → "OK:TMODE"
 *   TMODE:SPREAD   → high-torque spreadCycle mode
 *   H / HF         → home rail via StallGuard (HF also measures rail length)
 *                    → "HOMED <len>" / "ERR:DIAG_NOT_WIRED" / "ERR:HOME_TIMEOUT"
 *   HDIR:U / HDIR:D→ which direction creeps toward the homing hard stop
 *                    (timer rail: U; rotor rail: set after discovery) → "OK:HDIR:x"
 *   HSPD<us>       → homing creep step delay, 60-1000µs (default 150)
 *   HMAX<steps>    → homing creep travel cap (default 200000)
 *   HCUR<mA>       → reduced motor current during homing, 0 = disabled.
 *                    Anti-wedge for leadscrew (rotor) rails: limits how hard
 *                    the screw can jam into the stop before SG detection trips.
 *   HCFG?          → "HCFG:dir=U,spd=150,max=200000,cur=0"
 *   M<steps>       → move to absolute position, homed only → "OK POS <p>" / "STALL <p>"
 *   POS?           → "POS:<pos>,<homed>,<stalled>,<len>,<moving>"  (works mid-move)
 *   SGT<0-255>     → StallGuard sensitivity, higher = more sensitive (default 60)
 *
 * TMC2209 UART wiring: driver PDN_UART → Mega pin 19 (RX1) direct,
 * pin 18 (TX1) bridged to pin 19 through 1K resistor.
 * TMC2209 DIAG → Mega pin 21 (stall detection / homing; H reports if missing).
 */

#include <Servo.h>
#include <math.h>
#include <TMCStepper.h>

// ─── Pin Assignments ──────────────────────────────────────────────────────────
#define DIR_PIN   2
#define STEP_PIN  3
#define BASE_PIN  52
#define CAM_PIN   36

#define LED_R_DEFAULT  8
#define LED_G_DEFAULT  9
#define LED_B_DEFAULT 10

const bool COMMON_ANODE = false;

// ─── TMC2209 (rail stepper driver, UART on Serial1) ──────────────────────────
#define TMC_SERIAL  Serial1
#define TMC_R_SENSE 0.11f   // standard stepstick sense resistor; adjust if yours differs
#define DIAG_PIN    21      // TMC2209 DIAG output — goes HIGH on stall

TMC2209Stepper* tmc = nullptr;
bool tmcOk = false;
uint8_t sgThreshold = 80;   // raw SG_RESULT floor: below this (2x in a row) = stall

// Rail position state. Home (position 0) is the hard stop reached by creeping
// in homeDir; positions increase moving AWAY from home. The far end is fenced
// by railLength (measured or set via LEN<steps>).
// TIMER rail (belt): home = the U end — the only end with clean stall margin.
// ROTOR rail (leadscrew): ONE hard stop total — home there, NEVER stall-hunt
// the open end (the nut can run off the screw); always fence it with LEN.
long railPos     = 0;
long railLength  = 0;      // steps between ends after HF, 0 = unknown
bool railHomed   = false;
bool railStalled = false;
bool railMoving  = false;
bool diagOk      = false;  // true once homing has verified the DIAG wire works

// Homing profile — tunable per rail type over serial (HDIR/HSPD/HMAX/HCUR).
// Timer rail (belt) works with the defaults; the rotor rail (leadscrew) wants
// reduced current (HCUR ~600) so a missed stall can't wedge the screw into
// the stop, and a tight HMAX until its true travel is known.
int  homeDir      = HIGH;     // DIR level that creeps toward the homing stop (U)
int  homeDelayUs  = 150;      // creep step half-period (HSPD<us>)
long homeMaxSteps = 200000L;  // creep travel cap (HMAX<steps>)
int  homeCurMa    = 0;        // homing current, 0 = leave current alone (HCUR<mA>)
int  uartCurMa    = 0;        // last CUR<mA>; 0 = current still on the VREF pot

// SINGLE source of truth for the position sign convention. Every stepping
// loop must use these — four hand-copied sign expressions once diverged
// (moveToPos counted HIGH as +1 while everything else counted it -1).
static inline int awayDir()        { return homeDir == HIGH ? LOW : HIGH; }
static inline int dirSignOf(int d) { return (d == homeDir) ? -1 : +1; }

// Stall sensing reads RAW SG_RESULT over UART — on this module (BTT TMC2209
// V1.3) both the physical DIAG pin AND the chip's IOIN diag flag proved
// unreliable (flag tripped in free motion, silent during locked-rotor grind),
// while SG_RESULT itself is rock solid: free cruise ~130-250, true stall
// 36-86. Stall = SG below sgThreshold on TWO consecutive polls (~60ms apart).
// Each poll pauses stepping ~2-4ms. StallGuard works in stealthChop only.
unsigned long lastStallPoll = 0;
uint8_t sgLowStreak = 0;
uint16_t sgPollMs   = 280;    // poll cadence via SGP<ms> — 60ms self-perturbs:
                              // each poll's step-pause craters the next reading
uint8_t  sgStreakN  = 2;      // consecutive lows to declare stall, via SGN<n>
bool     sgDebug    = false;  // SGDBG1 → stream each poll's value ("g<val>")

// Mirrors the chopper mode last written by applyModeForSpeed()/homeRail() —
// read here instead of a live tmc->en_spreadCycle() so the hot stepping loop
// never pays a UART round-trip. SG_RESULT is bogus in spreadCycle on this
// module (garbage low readings), so railStallCheck must not trust it there:
// without this gate, STRONG mode false-STALLs within ~1s of every move start.
bool spreadActive = false;

bool railStallCheck() {
    if (!tmcOk || spreadActive) return false;
    unsigned long now = millis();
    if (now - lastStallPoll < sgPollMs) return false;
    lastStallPoll = now;
    uint16_t sg = tmc->SG_RESULT();
    if (sgDebug) { Serial.print("g"); Serial.println(sg); }
    if (sg < sgThreshold) {
        if (++sgLowStreak >= sgStreakN) { sgLowStreak = 0; return true; }
    } else {
        sgLowStreak = 0;
    }
    return false;
}

void printPos() {
    Serial.print("POS:");  Serial.print(railPos);
    Serial.print(",");     Serial.print(railHomed ? 1 : 0);
    Serial.print(",");     Serial.print(railStalled ? 1 : 0);
    Serial.print(",");     Serial.print(railLength);
    Serial.print(",");     Serial.println(railMoving ? 1 : 0);
}

// ─── State ────────────────────────────────────────────────────────────────────
Servo baseServo;
Servo camServo;

int  stepDelay   = 40;    // µs between steps
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
    // Switch active pin set WITHOUT zeroing old pins — other lights stay on
    ledPinR = r; ledPinG = g; ledPinB = b;
    pinMode(ledPinR, OUTPUT);
    pinMode(ledPinG, OUTPUT);
    pinMode(ledPinB, OUTPUT);
    // Initialize new pins to off so they're dark until the next RGB command
    analogWrite(ledPinR, COMMON_ANODE ? 255 : 0);
    analogWrite(ledPinG, COMMON_ANODE ? 255 : 0);
    analogWrite(ledPinB, COMMON_ANODE ? 255 : 0);
}

// ─── Rail ─────────────────────────────────────────────────────────────────────

// Shared ease-in: ramp speed (1/delay) linearly, not the delay itself —
// otherwise nearly all the acceleration bunches into the end of the ramp.
long rampMs = 500;  // runtime-tunable via RAMP<ms>

int rampDelay(long elapsed, int startDelay) {
    if (elapsed >= rampMs) return stepDelay;
    long dTarget = max(stepDelay, 1);  // stepDelay can be 0 (max speed)
    return (int)((long)startDelay * dTarget * rampMs /
                 (dTarget * rampMs + (long)(startDelay - dTarget) * elapsed));
}

// ─── S-curve ramps (SCURVE1/0, default on) ───────────────────────────────────
// Speed follows a smoothstep between the creep speed and the cruise speed, so
// acceleration is zero at both ends of every ramp (bounded jerk) instead of the
// linear ramp's corners and the old dead stop. M moves also DEcelerate: the
// decel mirrors the accel in step space (u = remaining / stepsUsedToAccelerate).
// Jogs ease out over the last rampMs of their duration. X still stops instantly.
bool sCurve = true;

static inline float smoothF(float t) {
    if (t <= 0.0f) return 0.0f;
    if (t >= 1.0f) return 1.0f;
    return t * t * (3.0f - 2.0f * t);
}
// delay for a speed blended from slowDelay (u=0) to fastDelay (u=1) along the S
int sDelay(float u, int slowDelay, int fastDelay) {
    float vs = 1.0f / (float)max(slowDelay, 1);
    float vf = 1.0f / (float)max(fastDelay, 1);
    float v  = vs + (vf - vs) * smoothF(u);
    return (int)(1.0f / v);
}

// Handle commands arriving mid-move. Returns false if an X (stop) came in.
// NON-BLOCKING: only consumes bytes already in the RX buffer, accumulating
// partial lines across calls — a blocking read here pauses stepping and
// makes the rail visibly stutter whenever the UI polls POS?.
char    midBuf[64];
uint8_t midLen = 0;
char    pendingMove = 0;  // 'U'/'D': direction pressed mid-move — restart that way

bool handleMidMoveSerial() {
    while (Serial.available()) {
        char ch = (char)Serial.read();
        if (ch == '\r') continue;
        if (ch != '\n') {
            if (midLen < sizeof(midBuf) - 1) midBuf[midLen++] = ch;
            continue;
        }
        midBuf[midLen] = '\0';
        String cmd = String(midBuf);
        midLen = 0;
        cmd.trim();
        if (cmd == "X") { pendingMove = 0; Serial.println("STOPPED"); return false; }
        // A new direction press mid-move used to be silently DISCARDED, so
        // buttons felt dead until the (up to 10s) move timed out. Now it
        // aborts the current move; loop() immediately starts the new one.
        if (cmd == "U" || cmd == "D") {
            pendingMove = cmd.charAt(0);
            Serial.println("STOPPED");  // terminal line so a homing worker isn't left waiting
            return false;
        }
        if      (cmd == "POS?")         printPos();
        else if (cmd == "DIAG?") {
            if (tmcOk) {
                Serial.print("DIAG:chip="); Serial.print(tmc->diag() ? 1 : 0);
                Serial.print(",pin=");      Serial.println(digitalRead(DIAG_PIN));
            } else Serial.println("TMC:NOT_CONNECTED");
        }
        else if (cmd == "SG?") {
            // Mid-move load sample. The UART read pauses stepping ~2-4ms —
            // fine for characterization runs, avoid during filmed moves.
            if (tmcOk) { Serial.print("SG:"); Serial.println(tmc->SG_RESULT()); }
            else Serial.println("TMC:NOT_CONNECTED");
        }
        else if (cmd == "TSTEP?") {
            if (tmcOk) { Serial.print("TSTEP:"); Serial.println(tmc->TSTEP()); }
            else Serial.println("TMC:NOT_CONNECTED");
        }
        else if (cmd.startsWith("BUS")) baseServo.writeMicroseconds(constrain(cmd.substring(3).toInt(), 400, 2600));
        else if (cmd.startsWith("CUS")) camServo.writeMicroseconds(constrain(cmd.substring(3).toInt(), 400, 2600));
        else if (cmd.startsWith("RGB:") || cmd.startsWith("PINS:") ||
                 cmd.startsWith("SETRGB:") || cmd.startsWith("EFFECT:"))
            handleCommand(cmd);  // lights keep working during rail moves
        else if (cmd.charAt(0) == 'S' && cmd.length() > 1 && isDigit(cmd.charAt(1))) {
            // Live speed/duration update mid-move (timeline sends S right before U/D)
            int comma = cmd.indexOf(',');
            if (comma > 1) {
                int  nd  = cmd.substring(1, comma).toInt();
                long dur = cmd.substring(comma + 1).toInt();
                if (nd >= 0 && dur > 0) { stepDelay = nd; spinDuration = dur; }
            }
        }
        else if (cmd.charAt(0) == 'B') baseServo.write(constrain(cmd.substring(1).toInt(), 0, 180));
        else if (cmd.charAt(0) == 'C') camServo.write(constrain(cmd.substring(1).toInt(), 0, 180));
    }
    return true;
}

// stealthChop is quiet but loses torque at high step rates — switch the driver
// to spreadCycle for fast moves. NOTE: TMC2209 StallGuard only works in
// stealthChop, so spreadCycle moves have no stall detection — DIAG simply
// never asserts there, which railStallCheck() tolerates.
#define SPREAD_DELAY_THRESHOLD 60   // µs; faster than this → spreadCycle (in AUTO)

enum TmcModePolicy { MODE_AUTO, MODE_STEALTH, MODE_SPREAD };
TmcModePolicy tmcModePolicy = MODE_AUTO;

void applyModeForSpeed() {
    if (!tmcOk) return;
    spreadActive = (tmcModePolicy == MODE_AUTO) ? (stepDelay < SPREAD_DELAY_THRESHOLD)
                                                 : (tmcModePolicy == MODE_SPREAD);
    tmc->en_spreadCycle(spreadActive);
}

void spinMotor(int direction, long durationMs) {
    applyModeForSpeed();
    digitalWrite(DIR_PIN, direction);
    int dirSign = dirSignOf(direction);
    unsigned long startTime = millis();
    unsigned long endTime   = startTime + durationMs;
    int startDelay = max(stepDelay, 300);
    railStalled = false;
    railMoving  = true;
    sgLowStreak = 0;

    int  curDelay  = startDelay;
    long stepsDone = 0;
    while (millis() < endTime) {
        if (!handleMidMoveSerial()) { railMoving = false; return; }

        long elapsed = (long)(millis() - startTime);

        // StallGuard is unreliable while accelerating, so only trust DIAG post-ramp
        if (elapsed > rampMs && railStallCheck()) {
            railStalled = true;
            railHomed   = false;  // steps were lost — position untrustworthy until re-home
            railMoving  = false;
            Serial.print("STALL "); Serial.println(railPos);
            return;
        }

        if (!sCurve) {
            curDelay = rampDelay(elapsed, startDelay);
        } else if ((stepsDone & 7) == 0) {           // float math every 8 steps only
            long remainMs = (long)(endTime - millis());
            float uIn  = (float)elapsed  / (float)rampMs;
            float uOut = (float)remainMs / (float)rampMs;
            float u = (uIn < uOut) ? uIn : uOut;      // ease in, cruise, ease out
            curDelay = (u >= 1.0f) ? stepDelay : sDelay(u, startDelay, stepDelay);
        }
        digitalWrite(STEP_PIN, HIGH);
        delayMicroseconds(curDelay);
        digitalWrite(STEP_PIN, LOW);
        delayMicroseconds(curDelay);
        railPos += dirSign;
        stepsDone++;
    }
    railMoving = false;
    Serial.println("OK");
}

// Move to an absolute step position. Requires homing first.
void moveToPos(long target) {
    if (!railHomed) { Serial.println("ERR:NOTHOMED"); return; }
    if (railLength > 0) target = constrain(target, 0L, railLength);
    else if (target < 0) target = 0;
    if (target == railPos) { Serial.print("OK POS "); Serial.println(railPos); return; }

    applyModeForSpeed();
    int direction = (target > railPos) ? awayDir() : homeDir;
    int dirSign   = dirSignOf(direction);
    digitalWrite(DIR_PIN, direction);
    unsigned long startTime = millis();
    int startDelay = max(stepDelay, 300);
    railStalled = false;
    railMoving  = true;
    sgLowStreak = 0;

    int  curDelay  = startDelay;
    long stepsDone = 0;
    long nAccel    = 0;           // steps the accel phase used -> decel mirrors it
    int  peakDelay = stepDelay;   // fastest delay actually reached
    bool accelDone = false;
    while (railPos != target) {
        if (!handleMidMoveSerial()) { railMoving = false; return; }

        long elapsed = (long)(millis() - startTime);
        if (elapsed > rampMs && railStallCheck()) {
            railStalled = true;
            railHomed   = false;
            railMoving  = false;
            Serial.print("STALL "); Serial.println(railPos);
            return;
        }

        if (!sCurve) {
            curDelay = rampDelay(elapsed, startDelay);
        } else {
            long remaining = labs(target - railPos);
            if (!accelDone) {
                if (elapsed >= rampMs)            { accelDone = true; nAccel = stepsDone; peakDelay = stepDelay; }
                else if (remaining <= stepsDone)  { accelDone = true; nAccel = stepsDone; peakDelay = curDelay; } // short move: turn around early
            }
            if ((stepsDone & 7) == 0) {
                if (!accelDone)
                    curDelay = sDelay((float)elapsed / (float)rampMs, startDelay, stepDelay);
                else if (nAccel > 0 && remaining <= nAccel)
                    curDelay = sDelay((float)remaining / (float)nAccel, startDelay, peakDelay);
                else
                    curDelay = stepDelay;
            }
        }
        digitalWrite(STEP_PIN, HIGH);
        delayMicroseconds(curDelay);
        digitalWrite(STEP_PIN, LOW);
        delayMicroseconds(curDelay);
        railPos += dirSign;
        stepsDone++;
    }
    railMoving = false;
    Serial.print("OK POS "); Serial.println(railPos);
}

// ─── Homing (StallGuard) ─────────────────────────────────────────────────────

#define HOME_BACKOFF   400    // steps to back away from a found end

// Creep until the chip's stall flag says the carriage hit an end.
// Returns 1 = stalled (end found), 0 = timed out, -1 = aborted by X.
#define CREEP_RAMP_MS   400L  // ease into creep speed — a jerk start makes
#define CREEP_BLIND_MS  700L  // StallGuard misread and false-trip immediately

int creepUntilStall(int direction, long maxSteps) {
    digitalWrite(DIR_PIN, direction);
    int dirSign = dirSignOf(direction);
    unsigned long start = millis();
    int cruise = homeDelayUs;
    int rampFrom = max(400, cruise);  // HSPD slower than 400 → no ramp, just creep
    sgLowStreak = 0;
    for (long i = 0; i < maxSteps; i++) {
        if (!handleMidMoveSerial()) return -1;  // X aborts (STOPPED already printed)
        long el = (long)(millis() - start);
        // Stall checks only after ramp + settle (SG-based, self rate-limited)
        if (el > CREEP_BLIND_MS && railStallCheck()) return 1;
        int d = (el >= CREEP_RAMP_MS) ? cruise
                : (int)(rampFrom - (long)(rampFrom - cruise) * el / CREEP_RAMP_MS);
        digitalWrite(STEP_PIN, HIGH); delayMicroseconds(d);
        digitalWrite(STEP_PIN, LOW);  delayMicroseconds(d);
        railPos += dirSign;
    }
    return 0;
}

void backOff(int direction, long steps) {
    digitalWrite(DIR_PIN, direction);
    int dirSign = dirSignOf(direction);
    for (long i = 0; i < steps; i++) {
        digitalWrite(STEP_PIN, HIGH); delayMicroseconds(homeDelayUs);
        digitalWrite(STEP_PIN, LOW);  delayMicroseconds(homeDelayUs);
        railPos += dirSign;
    }
}

// Undo the HCUR homing-current reduction, returning to whatever run current
// was active before (last CUR<mA>, or the VREF pot if CUR was never used).
void restoreRunCurrent() {
    if (!tmcOk || homeCurMa <= 0) return;
    if (uartCurMa > 0) tmc->rms_current(uartCurMa, 0.5f);
    else               tmc->I_scale_analog(true);
}

void homeRail(bool measureLength) {
    if (!tmcOk) { Serial.println("ERR:NO_TMC"); return; }
    // No wiring pre-flight needed — stall sensing reads the chip's diag flag
    // over UART (the physical DIAG pin on this module is dead).
    diagOk      = true;
    railStalled = false;
    railHomed   = false;
    railMoving  = true;
    tmc->en_spreadCycle(false);  // StallGuard needs stealthChop — never home in spread mode
    spreadActive = false;
    if (homeCurMa > 0) {         // anti-wedge: cap the force a missed stall can apply
        tmc->I_scale_analog(false);
        tmc->rms_current(homeCurMa, 0.5f);
    }

    // Creep into the homing hard stop (direction chosen by HDIR:).
    int r = creepUntilStall(homeDir, homeMaxSteps);
    if (r != 1) { restoreRunCurrent(); railMoving = false; if (r == 0) Serial.println("ERR:HOME_TIMEOUT"); return; }
    railPos = 0;
    backOff(awayDir(), HOME_BACKOFF);  // rest at +400, inside the usable range
    railHomed = true;

    if (measureLength) {
        // CAUTION: only valid on rails with a hard stop at BOTH ends, and even
        // on the timer rail the far end cruises at near-stall load — detection
        // there is unreliable. On the rotor rail (open far end) NEVER use HF;
        // use H + LEN<steps>.
        r = creepUntilStall(awayDir(), homeMaxSteps);
        if (r != 1) { restoreRunCurrent(); railMoving = false; if (r == 0) Serial.println("ERR:HOME_TIMEOUT"); return; }
        railLength = railPos - HOME_BACKOFF;  // usable range stops short of the hard end
        backOff(homeDir, HOME_BACKOFF);
        if (railLength < 1) { railLength = 0; railHomed = false; }
    }
    restoreRunCurrent();
    railMoving = false;
    Serial.print("HOMED "); Serial.println(railLength);
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
    if (line == "VER?") { Serial.print("VER:"); Serial.println(__DATE__ " " __TIME__); return; }
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

    // Rail position commands
    if (line == "POS?") { printPos(); return; }
    if (line == "HF")   { homeRail(true);  return; }
    if (line == "H")    { homeRail(false); return; }
    if (line.charAt(0) == 'M' && line.length() > 1) { moveToPos(line.substring(1).toInt()); return; }

    // Homing profile (per rail type — timer=belt, rotor=leadscrew/one stop)
    if (line.startsWith("HDIR:")) {
        char d = line.charAt(5);
        if (d != 'U' && d != 'D') { Serial.println("ERR:HDIR"); return; }
        homeDir = (d == 'U') ? HIGH : LOW;
        railHomed = false;  // old zero was measured from the other end
        Serial.print("OK:HDIR:"); Serial.println(d);
        return;
    }
    if (line.startsWith("HSPD")) {
        homeDelayUs = constrain((int)line.substring(4).toInt(), 60, 1000);
        Serial.print("OK:HSPD"); Serial.println(homeDelayUs);
        return;
    }
    if (line.startsWith("HMAX")) {
        homeMaxSteps = constrain(line.substring(4).toInt(), 1000L, 2000000L);
        Serial.print("OK:HMAX"); Serial.println(homeMaxSteps);
        return;
    }
    if (line.startsWith("HCUR")) {
        homeCurMa = constrain((int)line.substring(4).toInt(), 0, 1500);
        if (homeCurMa > 0 && homeCurMa < 100) homeCurMa = 100;  // driver minimum
        Serial.print("OK:HCUR"); Serial.println(homeCurMa);
        return;
    }
    if (line == "HCFG?") {
        Serial.print("HCFG:dir="); Serial.print(homeDir == HIGH ? 'U' : 'D');
        Serial.print(",spd=");     Serial.print(homeDelayUs);
        Serial.print(",max=");     Serial.print(homeMaxSteps);
        Serial.print(",cur=");     Serial.println(homeCurMa);
        return;
    }

    // DIAG? — chip's diag flag (via UART IOIN) vs. what pin 21 physically reads.
    // chip=1,pin=0 → jumper on wrong pad; chip=0 under stall → config problem.
    if (line == "DIAG?") {
        if (!tmcOk) { Serial.println("TMC:NOT_CONNECTED"); return; }
        Serial.print("DIAG:chip="); Serial.print(tmc->diag() ? 1 : 0);
        Serial.print(",pin=");      Serial.println(digitalRead(DIAG_PIN));
        return;
    }

    // SG? — live StallGuard load reading (lower = harder-working motor).
    // Only meaningful while moving in stealthChop.
    if (line == "SG?") {
        if (!tmcOk) { Serial.println("TMC:NOT_CONNECTED"); return; }
        Serial.print("SG:"); Serial.println(tmc->SG_RESULT());
        return;
    }

    // SCURVE1 / SCURVE0 — smooth (bounded-jerk) ramps with deceleration, or legacy
    if (line.startsWith("SCURVE")) {
        sCurve = line.charAt(6) != '0';
        Serial.print("OK:SCURVE"); Serial.println(sCurve ? 1 : 0);
        return;
    }
    // RAMP<ms> — ease-in duration for all moves
    if (line.startsWith("RAMP")) {
        rampMs = constrain(line.substring(4).toInt(), 50L, 5000L);
        Serial.print("OK:RAMP"); Serial.println(rampMs);
        return;
    }

    // SGT<val> — StallGuard sensitivity (must be checked before the S handler).
    // Write-only register, silent corruption possible — write 3x.
    if (line.startsWith("SGT")) {
        if (!tmcOk) { Serial.println("TMC:NOT_CONNECTED"); return; }
        sgThreshold = (uint8_t)constrain((int)line.substring(3).toInt(), 0, 255);
        for (uint8_t k = 0; k < 3; k++) tmc->SGTHRS(sgThreshold);
        Serial.print("OK:SGT"); Serial.println(sgThreshold);
        return;
    }

    // SG detector tuning: SGP<ms> poll cadence, SGN<n> streak, SGDBG0/1 debug
    if (line.startsWith("SGP")) {
        sgPollMs = (uint16_t)constrain((int)line.substring(3).toInt(), 20, 1000);
        Serial.print("OK:SGP"); Serial.println(sgPollMs);
        return;
    }
    if (line.startsWith("SGN")) {
        sgStreakN = (uint8_t)constrain((int)line.substring(3).toInt(), 1, 10);
        Serial.print("OK:SGN"); Serial.println(sgStreakN);
        return;
    }
    if (line.startsWith("SGDBG")) {
        sgDebug = line.charAt(5) == '1';
        Serial.print("OK:SGDBG"); Serial.println(sgDebug ? 1 : 0);
        return;
    }

    // ZERO — declare the current spot as position 0 and homed (manual idler mark)
    if (line == "ZERO") {
        railPos = 0;
        railHomed = true;
        railStalled = false;
        Serial.println("OK:ZERO");
        return;
    }

    // LEN<steps> — set rail length manually (motor-end fence for M moves);
    // used instead of HF because the motor end can't be stall-detected reliably
    if (line.startsWith("LEN")) {
        railLength = constrain(line.substring(3).toInt(), 0L, 500000L);
        Serial.print("OK:LEN"); Serial.println(railLength);
        return;
    }

    // TSTEP? — velocity register (small = fast); gate check vs TCOOLTHRS=3000
    if (line == "TSTEP?") {
        if (!tmcOk) { Serial.println("TMC:NOT_CONNECTED"); return; }
        Serial.print("TSTEP:"); Serial.println(tmc->TSTEP());
        return;
    }

    // S<delay>,<duration>  (digit guard keeps SETRGB:/SGT out of this handler)
    if (line.charAt(0) == 'S' && line.length() > 1 && isDigit(line.charAt(1))) {
        int comma = line.indexOf(',');
        if (comma > 1) {
            int  nd = line.substring(1, comma).toInt();
            long dur = line.substring(comma + 1).toInt();
            if (nd >= 0 && dur > 0) {
                stepDelay    = nd;
                spinDuration = dur;
                Serial.print("OK "); Serial.print(stepDelay);
                Serial.print(","); Serial.println(spinDuration);
            } else { Serial.println("ERROR"); }
        }
        return;
    }

    // TMC? — TMC2209 status report (must be handled before B/C servo commands)
    if (line == "TMC?") {
        if (!tmcOk) { Serial.println("TMC:NOT_CONNECTED"); return; }
        uint32_t drv = tmc->DRV_STATUS();
        Serial.print("TMC:OK v");   Serial.print(tmc->version());
        Serial.print(" mA=");       Serial.print(tmc->rms_current());
        Serial.print(" spread=");   Serial.print(tmc->en_spreadCycle() ? 1 : 0);
        Serial.print(" otpw=");     Serial.print((uint8_t)((drv >> 26) & 1));  // overtemp warning
        Serial.print(" ot=");       Serial.print((uint8_t)((drv >> 25) & 1));  // overtemp shutdown
        Serial.print(" standstill=");Serial.println((uint8_t)((drv >> 31) & 1));
        return;
    }

    // CUR<mA> — set motor RMS current over UART (takes over from the VREF pot)
    if (line.startsWith("CUR")) {
        if (!tmcOk) { Serial.println("TMC:NOT_CONNECTED"); return; }
        int mA = constrain((int)line.substring(3).toInt(), 100, 1500);
        tmc->I_scale_analog(false);
        tmc->rms_current(mA, 0.5f);   // hold current = 50% of run
        uartCurMa = mA;               // homeRail restores to this after HCUR homing
        Serial.print("OK:CUR"); Serial.println(mA);
        return;
    }

    // TMODE:AUTO / TMODE:STEALTH / TMODE:SPREAD — mode policy for rail moves
    if (line.startsWith("TMODE:")) {
        if (!tmcOk) { Serial.println("TMC:NOT_CONNECTED"); return; }
        String m = line.substring(6);
        m.toUpperCase();
        if      (m == "SPREAD")  tmcModePolicy = MODE_SPREAD;
        else if (m == "STEALTH") tmcModePolicy = MODE_STEALTH;
        else                     tmcModePolicy = MODE_AUTO;
        applyModeForSpeed();
        Serial.println("OK:TMODE");
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

    // SETRGB:pR,pG,pB,r,g,b — atomic direct write to named pins, no global state change
    if (line.startsWith("SETRGB:")) {
        currentEffect = EFF_NONE;
        String s = line.substring(7);
        int c[5], pos = 0;
        bool ok = true;
        for (int i = 0; i < 5; i++) {
            c[i] = s.indexOf(',', pos);
            if (c[i] < 0) { ok = false; break; }
            pos = c[i] + 1;
        }
        if (ok) {
            uint8_t pR = constrain(s.substring(0,      c[0]).toInt(), 0, 53);
            uint8_t pG = constrain(s.substring(c[0]+1, c[1]).toInt(), 0, 53);
            uint8_t pB = constrain(s.substring(c[1]+1, c[2]).toInt(), 0, 53);
            uint8_t r  = gamma8(constrain(s.substring(c[2]+1, c[3]).toInt(), 0, 255));
            uint8_t g  = gamma8(constrain(s.substring(c[3]+1, c[4]).toInt(), 0, 255));
            uint8_t b  = gamma8(constrain(s.substring(c[4]+1).toInt(),       0, 255));
            pinMode(pR, OUTPUT); pinMode(pG, OUTPUT); pinMode(pB, OUTPUT);
            if (COMMON_ANODE) {
                analogWrite(pR, 255-r); analogWrite(pG, 255-g); analogWrite(pB, 255-b);
            } else {
                analogWrite(pR, r); analogWrite(pG, g); analogWrite(pB, b);
            }
        }
        Serial.println("OK:SETRGB");
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

    pinMode(DIAG_PIN, INPUT_PULLUP);

    // TMC2209: UART address is set by the module's MS1/MS2 pins, so scan all 4
    TMC_SERIAL.begin(115200);
    for (uint8_t addr = 0; addr < 4 && !tmcOk; addr++) {
        TMC2209Stepper* t = new TMC2209Stepper(&TMC_SERIAL, TMC_R_SENSE, addr);
        t->begin();
        if (t->test_connection() == 0) { tmc = t; tmcOk = true; }
        else delete t;
    }
    if (tmcOk) {
        // Leave motion behavior exactly as standalone mode had it: microstepping
        // stays pin-controlled and current stays on the VREF pot until a CUR
        // command opts into UART control.
        tmc->mstep_reg_select(false);
        tmc->I_scale_analog(true);
        tmc->irun(31);
        tmc->ihold(16);      // ~50% hold current — cooler motor/driver when parked
        tmc->iholddelay(8);
        tmc->TPOWERDOWN(64);
        // StallGuard velocity gate: active only above a real movement speed.
        // 0xFFFFF (max) kept evaluation on at standstill, where SG_RESULT=0 —
        // a properly wired DIAG would idle high and trip the wiring pre-flight.
        // 3000 covers homing creep and everything faster; standstill is clean.
        // These registers are WRITE-ONLY over a single-wire UART — a corrupted
        // write is silent and unverifiable, so write critical ones 3x.
        for (uint8_t k = 0; k < 3; k++) {
            tmc->TCOOLTHRS(3000);
            tmc->SGTHRS(sgThreshold);
        }
    }

    Serial.println(tmcOk ? "READY TMC" : "READY");
}

// ─── Loop ─────────────────────────────────────────────────────────────────────

void loop() {
    // A move may end with a half-received command sitting in midBuf (its tail
    // still incoming). Hand the fragment to the normal parser so the command
    // survives the move boundary instead of being split and dropped.
    if (midLen) {
        midBuf[midLen] = '\0';
        inputString += midBuf;
        midLen = 0;
    }

    if (stringComplete) {
        handleCommand(inputString);
        inputString    = "";
        stringComplete = false;
    }

    // Direction press received mid-move → start the new move immediately
    if (pendingMove) {
        char m = pendingMove;
        pendingMove = 0;
        spinMotor(m == 'U' ? HIGH : LOW, spinDuration);
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
    // Stop at the first newline — draining the whole buffer here glues
    // back-to-back commands into one garbage line ("POS?U") and loses both.
    // Remaining bytes stay in the HW buffer for the next call.
    while (Serial.available() && !stringComplete) {
        char c = (char)Serial.read();
        if (c == '\n') {
            stringComplete = true;
        } else if (c != '\r') {
            inputString += c;
        }
    }
}
