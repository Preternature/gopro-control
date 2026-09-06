import time, json, shutil, cv2, numpy as np
from rail_helpers import send, state, jitter
def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)
def snap(name): time.sleep(1.0); shutil.copy('vision_live.jpg', name)
def shift(a,b):
    A=cv2.imread(a,0).astype(np.float32); B=cv2.imread(b,0).astype(np.float32)
    w=cv2.createHanningWindow(A.shape[::-1], cv2.CV_32F); (dx,dy),r=cv2.phaseCorrelate(A,B,w); return dx,dy,r
def wait_idle(tmax, abort_tilt=30):
    t0=time.time(); time.sleep(1.5); last=time.time()
    while time.time()-t0<tmax:
        st=state()
        if not st.get('moving'): return st
        if time.time()-last>2:
            j=jitter(last,time.time()); last=time.time()
            if j:
                log(f"   pos={st['pos']} jit_y={j['jit_y']:.3f} drift_y={j['drift_y']:+.1f}")
                if abs(j['drift_y'])>abort_tilt or j['jit_y']>1.0: send("X"); log("   !! ABORT tilt/shake"); raise SystemExit
        time.sleep(0.3)
    return state()
def home():
    send("SGT80","OK"); p=state()['pos']; t0=time.time(); send("H"); lo=p
    while time.time()-t0<120:
        st=state(); lo=min(lo, st.get('pos',lo))
        if not st.get('moving') and time.time()-t0>2.5: break
        time.sleep(0.3)
    st=state(); log(f"   home: homed={st.get('homed')} pos={st['pos']} creep travel {p-lo}"); return st

RECIPE=lambda: (send("TMODE:STEALTH","OK"), send("CUR1200","OK"), send("RAMP2000","OK"), send("SGT80","OK"), send("S60,10000"))
log("1) homing at motor end"); st=home()
if not st.get('homed'): log("not homed, stop"); raise SystemExit
snap('t_home.jpg')
log("2) direction check: M20000 then M0"); RECIPE(); time.sleep(0.8)
send("M20000"); st=wait_idle(15); snap('t_20k.jpg'); log(f"   at {st['pos']} stalled={st.get('stalled')}")
send("M0"); st=wait_idle(15); snap('t_home2.jpg'); log(f"   back at {st['pos']} stalled={st.get('stalled')}")
log("   picture shift home->20k: dx=%.2f dy=%.2f r=%.2f" % shift('t_home.jpg','t_20k.jpg'))
log("   picture shift home->home2: dx=%.2f dy=%.2f r=%.2f" % shift('t_home.jpg','t_home2.jpg'))
if st.get('stalled') or not st.get('homed'): log("stalled/unhomed during check, stop"); raise SystemExit
log("3) cruise toward away end in fenced chunks (stall detect on)")
for tgt in (100000, 200000):
    send(f"M{tgt}"); st=wait_idle(60); log(f"   at {st['pos']} stalled={st.get('stalled')} homed={st.get('homed')}")
    if st.get('stalled') or not st.get('homed'): log("   stopped early — treating as end"); break
snap('t_200k.jpg')
log("4) creep D at CUR600/S150 until the far stop")
send("CUR600","OK"); send("SGT80","OK"); far=None
for chunk in range(6):
    send("S150,40000"); time.sleep(0.8); p=state()['pos']; send("D"); st=wait_idle(50, abort_tilt=40)
    log(f"   creep chunk {chunk}: {p} -> {st['pos']} stalled={st.get('stalled')}")
    if st.get('stalled'): far=st['pos']; break
    if st['pos']-p < 500: far=st['pos']; log("   no advance -> end"); break
send("CUR1200","OK")
if far is None: log("far end not found within cap; stopping"); raise SystemExit
fence=far-800; log(f"   FAR STOP at {far}; fence LEN{fence} ->", send(f"LEN{fence}","OK"))
c=json.load(open('rail_calib.json')); c['rails']['rotor']['length']=fence; json.dump(c,open('rail_calib.json','w'),indent=2)
snap('t_far.jpg')
log("5) back off and re-home to verify")
send("S60,4000"); time.sleep(0.8); send("U"); st=wait_idle(10); log(f"   backed off to {st['pos']}")
send("M0"); st=wait_idle(90); log(f"   M0 -> {st['pos']} stalled={st.get('stalled')} homed={st.get('homed')}")
st=home(); snap('t_home3.jpg')
log("   picture shift home->home3: dx=%.2f dy=%.2f r=%.2f" % shift('t_home.jpg','t_home3.jpg'))
log(f"DONE: rail length ~{far} steps, fence {fence}. state={json.dumps(state())}")
