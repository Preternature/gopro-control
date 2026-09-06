import json, time, re, sys, numpy as np
from rail_helpers import load, send, state, wait_idle, jitter
def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)
def pos(): return state().get('pos')
def watch_move(cmd, tmax=90, tilt_abort=30):
    """fire a move, poll rail+vision, abort with X if the camera starts tilting or shaking."""
    t0=time.time(); send(cmd); time.sleep(1.2); last=t0
    while time.time()-t0<tmax:
        s=state()
        if not s.get('moving'): break
        if time.time()-last>2.0:
            j=jitter(last,time.time()); last=time.time()
            if j: 
                log(f"  pos={s['pos']} jit_y={j['jit_y']:.3f} drift_y={j['drift_y']:+.1f}")
                if abs(j['drift_y'])>tilt_abort or j['jit_y']>0.4:
                    send("X"); log("  !! ABORT: camera tilting/shaking"); return None
        time.sleep(0.3)
    return state()

# 1) integrity check: re-home, measure creep travel
log("SGT80 ->", send("SGT80","OK"), "| CUR1200 ->", send("CUR1200","OK"))
p0=pos(); log(f"re-homing from counted pos {p0}")
t0=time.time(); send("H"); lo=p0
while time.time()-t0<120:
    s=state(); lo=min(lo, s.get('pos',lo))
    if s.get('homed') and not s.get('moving') and time.time()-t0>2: break
    time.sleep(0.3)
log(f"homed. lowest count during creep {lo} -> true distance was ~{p0-lo} vs counted {p0} (diff {p0-lo-p0:+d} incl. ~1500 press-in)")

# 2) cruise toward motor in the quiet recipe
log("TMODE:STEALTH", send("TMODE:STEALTH","OK"), "RAMP1000", send("RAMP1000","OK"), "SGT0", send("SGT0","OK"), "S60", send("S60,10000"))
time.sleep(0.8)
CRUISE=int(sys.argv[1]) if len(sys.argv)>1 else 300000
s=watch_move(f"M{CRUISE}", tmax=120)
if s is None or s.get('stalled'): log("cruise failed", s); sys.exit(1)
log(f"cruise done at {s['pos']}")

# 3) creep to the far stop at low current with stall detect on
log("CUR600", send("CUR600","OK"), "SGT80", send("SGT80","OK"), "S150", send("S150,40000"))
time.sleep(0.8)
t0=time.time(); send("D"); time.sleep(1.2)
while time.time()-t0<60:
    s=state()
    if not s.get('moving'): break
    time.sleep(0.3)
far=s['pos']; log(f"creep stopped at {far} stalled={s.get('stalled')} homed={s.get('homed')}")
# confirm: try to creep further for 4 s; a real stop won't advance
send("S150,4000"); time.sleep(0.8); p1=pos(); send("D"); time.sleep(1.2)
while state().get('moving'): time.sleep(0.3)
p2=pos(); log(f"confirm creep: {p1} -> {p2} (advance {p2-p1})")
far=max(far,p2)
# 4) fence it and go home
send("CUR1200","OK"); fence=far-800
log("LEN ->", send(f"LEN{fence}","OK"))
try:
    c=json.load(open('rail_calib.json')); c['rails']['rotor']['length']=fence; json.dump(c,open('rail_calib.json','w'),indent=2); log("rail_calib.json rotor length =", fence)
except Exception as e: log("calib save failed", e)
# firmware cleared homed on the STALL, so: creep back off the stop, then home for real
log("SGT0", send("SGT0","OK"), "TMODE:STEALTH", send("TMODE:STEALTH","OK"), "S60", send("S60,30000"))
time.sleep(0.8); pa=pos(); t0=time.time(); send("U"); time.sleep(1.2)
while state().get('moving') and time.time()-t0<60: time.sleep(0.3)
pb=pos(); log(f"ran back U at S60: {pa} -> {pb}")
log("SGT80", send("SGT80","OK")); p0=pos(); t0=time.time(); send("H"); lo=p0
while time.time()-t0<180:
    s=state(); lo=min(lo, s.get('pos',lo))
    if s.get('homed') and not s.get('moving') and time.time()-t0>2: break
    time.sleep(0.3)
log(f"HOMED again. creep travel {p0-lo}; far stop at ~{far} from this home -> rail length ~{far} steps; fence {fence}")
print(json.dumps(state()))
