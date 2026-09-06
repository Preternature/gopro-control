"""
rail_vision.py — camera-based rail odometry / end detector.

Reads the GoPro preview stream (UDP), estimates per-frame camera motion (translation,
zoom, rotation) from tracked features plus a phase-correlation global shift, and logs it
against the rail position published by Rail Lab (rail_state.json). In --watch mode it
acts as an independent stall sensor: if the rail reports moving=true, visual motion was
established, and then the picture goes still for --stall-frames consecutive frames, it
drops an "X" into rail_cmd.json (once per move).

Outputs (all in this folder):
  vision_state.json  live summary for the assistant to read
  vision.log         one line per frame
  vision_live.jpg    ~2 fps 960px snapshot (what the camera sees right now)

Run:  python rail_vision.py --watch --width 640 [--start-stream]
Kill this AND ffmpeg.exe before restarting (an orphan ffmpeg keeps UDP 8555 busy).
"""
import subprocess, time, json, os, argparse, threading
import numpy as np, cv2, requests

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "rail_state.json")
CMD_IN = os.path.join(HERE, "rail_cmd.json")
VSTATE = os.path.join(HERE, "vision_state.json")
VLOG = os.path.join(HERE, "vision.log")
VLIVE = os.path.join(HERE, "vision_live.jpg")
FFMPEG = r"C:\ffmpeg\bin\ffmpeg.exe"

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8555)
ap.add_argument("--cam", default="172.27.177.51")
ap.add_argument("--width", type=int, default=640)
ap.add_argument("--watch", action="store_true", help="send X when picture stops while rail says moving")
ap.add_argument("--thresh", type=float, default=0.35, help="px/frame below which the picture counts as still")
ap.add_argument("--zoom-thresh", type=float, default=0.0015, help="log-scale/frame below which zoom counts as still")
ap.add_argument("--stall-frames", type=int, default=6)
ap.add_argument("--start-stream", action="store_true")
a = ap.parse_args()

W = a.width
H = int(round(1080 * W / 1920))
H -= H % 2
FRAME_BYTES = W * H


def rail_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def send_rail(cmd):
    try:
        with open(CMD_IN, "w") as f:
            json.dump({"id": int(time.time() * 1000) % 1000000000, "cmd": cmd}, f)
    except Exception as e:
        print("send_rail failed:", e, flush=True)


def keepalive():
    while True:
        try:
            requests.get("http://%s:8080/gopro/camera/keep_alive" % a.cam, timeout=2)
        except Exception:
            pass
        time.sleep(3)


def similarity(p0, p1):
    """Fit x' = s*R*x + t to tracked points.
    Returns (tx, ty, log_scale, rot_deg, n_inliers) with translation measured at the image centre."""
    M, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=2.0)
    if M is None:
        return None
    s = float(np.hypot(M[0, 0], M[1, 0]))
    rot = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    cx, cy = W / 2, H / 2
    tx = float(M[0, 0] * cx + M[0, 1] * cy + M[0, 2] - cx)
    ty = float(M[1, 0] * cx + M[1, 1] * cy + M[1, 2] - cy)
    ninl = int(inl.sum()) if inl is not None else 0
    return tx, ty, float(np.log(max(s, 1e-6))), rot, ninl


if a.start_stream:
    try:
        requests.get("http://%s:8080/gopro/camera/stream/start" % a.cam, params={"port": a.port}, timeout=5)
    except Exception as e:
        print("stream start failed:", e, flush=True)
    time.sleep(0.5)
threading.Thread(target=keepalive, daemon=True).start()

cmd = [FFMPEG, "-hide_banner", "-loglevel", "error",
       "-fflags", "nobuffer+genpts+discardcorrupt", "-flags", "low_delay",
       "-probesize", "500000", "-analyzeduration", "500000",
       "-i", "udp://0.0.0.0:%d?timeout=20000000&overrun_nonfatal=1" % a.port,
       "-map", "0:v:0", "-vf", "scale=%d:%d,format=gray" % (W, H), "-f", "rawvideo", "pipe:1",
       "-map", "0:v:0", "-vf", "scale=960:-2", "-r", "2", "-update", "1", "-q:v", "4", "-y", VLIVE]
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
print("ffmpeg started, frame %dx%d, watch=%s" % (W, H, a.watch), flush=True)

win = cv2.createHanningWindow((W, H), cv2.CV_32F)
feat = dict(maxCorners=400, qualityLevel=0.01, minDistance=8, blockSize=7)
lk = dict(winSize=(21, 21), maxLevel=3,
          criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
prev = prev_u8 = prev_pts = None
cum_dx = cum_dy = cum_ls = cum_rot = 0.0
hist, hist_s = [], []
n = 0
t0 = time.time()
last_write = 0.0
move_active = motion_established = stall_sent = False
still_run = 0
log = open(VLOG, "a", buffering=1)
log.write("# start %s W=%d H=%d watch=%s mode=features+phase\n" % (time.strftime("%H:%M:%S"), W, H, a.watch))

while True:
    buf = proc.stdout.read(FRAME_BYTES)
    if len(buf) < FRAME_BYTES:
        print("stream ended", flush=True)
        break
    cur_u8 = np.frombuffer(buf, np.uint8).reshape(H, W)
    cur = cur_u8.astype(np.float32)
    n += 1
    t = time.time()
    dx = dy = ls = rot = 0.0
    resp = 0.0
    ninl = 0
    pdx = pdy = 0.0
    if prev is not None:
        (pdx, pdy), resp = cv2.phaseCorrelate(prev, cur, win)
        if prev_pts is None or len(prev_pts) < 60:
            prev_pts = cv2.goodFeaturesToTrack(prev_u8, **feat)
        if prev_pts is not None and len(prev_pts) >= 8:
            p1, st, _ = cv2.calcOpticalFlowPyrLK(prev_u8, cur_u8, prev_pts, None, **lk)
            good = st.reshape(-1) == 1
            if good.sum() >= 8:
                fit = similarity(prev_pts[good], p1[good])
                if fit:
                    dx, dy, ls, rot, ninl = fit
                prev_pts = p1[good].reshape(-1, 1, 2)
            else:
                prev_pts = None
        if ninl < 8:  # feature fit failed -> fall back to the phase-correlation shift
            dx, dy = (pdx, pdy) if resp >= 0.05 else (0.0, 0.0)
    prev, prev_u8 = cur, cur_u8
    cum_dx += dx
    cum_dy += dy
    cum_ls += ls
    cum_rot += rot
    hist.append(dx)
    hist = hist[-8:]
    hist_s.append(ls)
    hist_s = hist_s[-8:]
    dx_avg = float(np.mean(hist))
    ls_avg = float(np.mean(hist_s))
    rs = rail_state()
    moving = bool(rs.get("moving"))
    pos = rs.get("pos")
    visual_moving = abs(dx_avg) > a.thresh or abs(ls_avg) > a.zoom_thresh

    event = ""
    if moving and not move_active:
        move_active = True
        motion_established = False
        still_run = 0
        stall_sent = False
    if not moving and move_active:
        move_active = False
    if move_active:
        if abs(dx) > a.thresh * 2 or abs(ls) > a.zoom_thresh * 2:
            motion_established = True
            still_run = 0
        elif motion_established:
            still_run += 1
        if a.watch and motion_established and still_run >= a.stall_frames and not stall_sent:
            send_rail("X")
            stall_sent = True
            event = "VISUAL_STALL pos=%s -> X sent" % pos
            print(time.strftime("%H:%M:%S"), event, flush=True)

    cumzoom = (float(np.exp(cum_ls)) - 1) * 100
    log.write("%.3f n=%d dx=%+.2f dy=%+.2f zoom=%+.3f%% rot=%+.2f inl=%d pdx=%+.2f pdy=%+.2f r=%.2f "
              "cumx=%+.1f cumy=%+.1f cumzoom=%+.1f%% cumrot=%+.1f pos=%s mov=%d %s\n"
              % (t, n, dx, dy, ls * 100, rot, ninl, pdx, pdy, resp,
                 cum_dx, cum_dy, cumzoom, cum_rot, pos, int(moving), event))
    if t - last_write > 0.25:
        last_write = t
        try:
            with open(VSTATE, "w") as f:
                json.dump({"t": t, "frames": n, "fps": round(n / max(t - t0, 1e-6), 1),
                           "dx": round(dx, 2), "dy": round(dy, 2), "dx_avg": round(dx_avg, 2),
                           "zoom_pct": round(ls * 100, 3), "zoom_avg_pct": round(ls_avg * 100, 3),
                           "rot": round(rot, 3), "inliers": ninl, "resp": round(resp, 2),
                           "cum_dx": round(cum_dx, 1), "cum_dy": round(cum_dy, 1),
                           "cum_zoom_pct": round(cumzoom, 2), "cum_rot": round(cum_rot, 2),
                           "visual_moving": visual_moving, "rail_pos": pos, "rail_moving": moving,
                           "watch": a.watch, "motion_established": motion_established,
                           "still_run": still_run, "last_event": event or None}, f)
        except Exception:
            pass
