import json, time, sys
def load(p):
    for _ in range(10):
        try:
            with open(p) as f: return json.load(f)
        except Exception: time.sleep(0.03)
    return {}
r = load('rail_state.json'); v = load('vision_state.json')
print("pos=%7s homed=%s mov=%s st=%s load=%4s | dx=%+.2f dy=%+.2f zoom=%+.3f%% cumx=%+.1f cumy=%+.1f cumzoom=%+.1f inl=%s ev=%s" % (
    r.get('pos'), int(bool(r.get('homed'))), int(bool(r.get('moving'))), int(bool(r.get('stalled'))), r.get('load'),
    v.get('dx',0), v.get('dy',0), v.get('zoom_pct',0), v.get('cum_dx',0), v.get('cum_dy',0), v.get('cum_zoom_pct',0), v.get('inliers'), v.get('last_event')))
sys.exit(3 if (r.get('homed') and not r.get('moving')) else 0)
