import json, time, sys, numpy as np, re
HERE='.'
def load(p):
    for _ in range(20):
        try:
            with open(p) as f: return json.load(f)
        except Exception: time.sleep(0.03)
    return {}
_id=[int(time.time())%100000]
def send(cmd, expect=None, timeout=4):
    _id[0]+=1; d={"id":_id[0],"cmd":cmd}
    if expect: d.update(expect=expect, timeout=timeout)
    json.dump(d, open('rail_cmd.json','w'))
    t=time.time()
    while time.time()-t<timeout+1.5:
        r=load('rail_reply.json')
        if r.get('id')==_id[0]: return r.get('reply')
        time.sleep(0.15)
    return None
def state(): return load('rail_state.json')
def wait_idle(tmax=60):
    t=time.time(); time.sleep(1.2)
    while time.time()-t<tmax:
        s=state()
        if not s.get('moving'): return s
        time.sleep(0.25)
    return state()
def jitter(t0,t1):
    dys=[];dxs=[];inl=[]
    for line in open('vision.log',encoding='utf-8',errors='ignore'):
        if line.startswith('#'): continue
        try: ts=float(line.split()[0])
        except: continue
        if ts<t0 or ts>t1: continue
        m=dict(re.findall(r'(\w+)=([+-]?[\d.]+)', line))
        if m.get('mov')!='1': continue
        dys.append(float(m['dy'])); dxs.append(float(m['dx'])); inl.append(int(m['inl']))
    if len(dys)<10: return None
    dy=np.array(dys); dx=np.array(dxs)
    k=5; sm=np.convolve(dy,np.ones(k)/k,'same'); smx=np.convolve(dx,np.ones(k)/k,'same')
    return dict(frames=len(dy), jit_y=float(np.std(dy-sm)), jit_x=float(np.std(dx-smx)),
                p95_absdy=float(np.percentile(np.abs(dy),95)), drift_y=float(dy.sum()), drift_x=float(dx.sum()),
                low_inl=float(np.mean(np.array(inl)<120)))
configs=[
 dict(name="STEALTH S150 ramp1000", mode="STEALTH", s=150, ramp=1000, cur=1200),
 dict(name="STEALTH S100 ramp1000", mode="STEALTH", s=100, ramp=1000, cur=1200),
 dict(name="STEALTH S60  ramp1000", mode="STEALTH", s=60,  ramp=1000, cur=1200),
 dict(name="SPREAD  S60  ramp1000", mode="SPREAD",  s=60,  ramp=1000, cur=1200),
 dict(name="SPREAD  S40  ramp1000", mode="SPREAD",  s=40,  ramp=1000, cur=1200),
 dict(name="STEALTH S100 ramp1000 CUR900", mode="STEALTH", s=100, ramp=1000, cur=900),
]
STEPS=int(sys.argv[1]) if len(sys.argv)>1 else 20000
print("SGT->", send("SGT0","OK"))        # disable mid-move stall trips for the sweep
print("RAMP->", send("RAMP1000","OK"))
results=[]
for c in configs:
    s=state()
    if not s.get('homed'): print("NOT HOMED, abort"); break
    base=s['pos']; tgt=base+STEPS if base+STEPS<340000 else base-STEPS
    print(f"\n=== {c['name']} ===  pos {base} -> {tgt}")
    print("  TMODE", send("TMODE:"+c['mode'],"OK"), "S", send(f"S{c['s']},10000"), "CUR", send(f"CUR{c['cur']}","OK"), "RAMP", send(f"RAMP{c['ramp']}","OK"))
    time.sleep(0.8)
    t0=time.time(); send(f"M{tgt}"); s=wait_idle(); t1=time.time()
    j=jitter(t0,t1); print("  out :", s['pos'], "stalled" if s.get('stalled') else "ok", j)
    if j and abs(j['drift_y'])>40: print("  !! camera tilted, stopping sweep"); results.append((c['name'],j,None)); break
    time.sleep(1.0)
    t0=time.time(); send(f"M{base}"); s=wait_idle(); t1=time.time()
    j2=jitter(t0,t1); print("  back:", s['pos'], "stalled" if s.get('stalled') else "ok", j2)
    results.append((c['name'],j,j2))
    if j2 and abs(j2['drift_y'])>40: print("  !! camera tilted, stopping sweep"); break
    time.sleep(1.0)
print("\nSUMMARY (jitter = px/frame std after detrending; drift = total px):")
for name,j,j2 in results:
    for tag,jj in (("out",j),("back",j2)):
        if jj: print(f"{name:32s} {tag:4s} jit_y={jj['jit_y']:.3f} jit_x={jj['jit_x']:.3f} p95|dy|={jj['p95_absdy']:.2f} drift_y={jj['drift_y']:+.1f} drift_x={jj['drift_x']:+.1f} blur={jj['low_inl']:.2f} n={jj['frames']}")
