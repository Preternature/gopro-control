import time, sys, json
from rail_helpers import send, state, jitter, load
def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)
print("SGT80", send("SGT80","OK"), "TMODE:STEALTH", send("TMODE:STEALTH","OK"))
cfgs=[("S150 ramp2000 CUR900",150,2000,900),("S100 ramp2000 CUR900",100,2000,900),("S60 ramp2000 CUR900",60,2000,900),("S60 ramp2000 CUR1200",60,2000,1200),("S40 ramp2000 CUR1200",40,2000,1200)]
DUR=int(sys.argv[1]) if len(sys.argv)>1 else 6000
for name,s,ramp,cur in cfgs:
    print(f"\n=== {name} === jog U {DUR} ms")
    send(f"CUR{cur}","OK"); send(f"RAMP{ramp}","OK"); send(f"S{s},{DUR}"); time.sleep(0.8)
    p0=state().get('pos'); t0=time.time(); send("U"); time.sleep(1.5); last=time.time(); worst=0
    while time.time()-t0<DUR/1000+4:
        st=state()
        if not st.get('moving'): break
        if time.time()-last>1.5:
            j=jitter(last,time.time()); last=time.time()
            if j:
                worst=max(worst,j['jit_y']); print(f"   pos={st['pos']} jit_y={j['jit_y']:.3f} jit_x={j['jit_x']:.3f} drift_y={j['drift_y']:+.1f}")
                if abs(j['drift_y'])>30 or j['jit_y']>1.5: send("X"); print("   !! ABORT tilt/shake"); break
        time.sleep(0.3)
    st=state(); j=jitter(t0,time.time())
    print(f"   RESULT pos {p0}->{st.get('pos')} stalled={st.get('stalled')} | jit_y={j['jit_y']:.3f} jit_x={j['jit_x']:.3f} p95|dy|={j['p95_absdy']:.2f} drift_y={j['drift_y']:+.1f} (still baseline jit_y=0.147)" if j else "   no frames")
    time.sleep(1.5)
