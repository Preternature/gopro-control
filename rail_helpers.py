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
