// GoPro Controller — Dual Camera Client

const socket = io();

// Per-camera state
const camState = {
    1: { recording: false, intervalRunning: false, hlsPlayer: null, ip: null, flipped: true },
    2: { recording: false, intervalRunning: false, hlsPlayer: null, ip: null, flipped: true },
};

let activeMediaCam = 1;

// ─── Utilities ────────────────────────────────────────────────────────────────

function showNotification(message, type = 'info') {
    const el = document.createElement('div');
    el.className = `notification ${type}`;
    el.textContent = message;
    document.getElementById('notifications').appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

async function apiCall(endpoint, method = 'GET', data = null) {
    console.log(`[LCA] → ${method} /api${endpoint}`, data ?? '');
    try {
        const opts = { method, headers: { 'Content-Type': 'application/json' } };
        if (data) opts.body = JSON.stringify(data);
        const res = await fetch(`/api${endpoint}`, opts);
        const json = await res.json();
        console.log(`[LCA] ← ${method} /api${endpoint} HTTP ${res.status}`, json);
        return json;
    } catch (err) {
        console.error(`[LCA] ✗ ${method} /api${endpoint}`, err);
        showNotification('Connection error', 'error');
        return null;
    }
}

function el(id) { return document.getElementById(id); }

// ─── Connection status ─────────────────────────────────────────────────────────

function updateCamStatus(camId, connected, ip = null, connType = null) {
    const pill = el(`cam${camId}-status`);
    const connInfo = el(`cam${camId}-conn-info`);
    const notConnected = el(`cam${camId}-not-connected`);
    const content = el(`cam${camId}-content`);

    camState[camId].connected = connected;
    if (connected) {
        pill.classList.add('connected');
        pill.classList.remove('disconnected');
        if (ip) {
            camState[camId].ip = ip;
            connInfo.textContent = `${connType ? connType.toUpperCase() + ' · ' : ''}${ip}`;
        }
        notConnected.style.display = 'none';
        content.style.display = 'block';
        // Apply default flip state visually
        if (camState[camId].flipped) {
            el(`cam${camId}-video`).style.transform = 'rotate(180deg)';
            el(`cam${camId}-btn-flip`).style.background = 'var(--primary-color)';
        }
    } else {
        pill.classList.remove('connected');
        pill.classList.add('disconnected');
        connInfo.textContent = '—';
        notConnected.style.display = 'block';
        content.style.display = 'none';
    }
}

async function retryConnection(camId) {
    showNotification(`Checking Camera ${camId}...`, 'info');
    const result = await apiCall(`/${camId}/status`);
    if (result) {
        updateCamStatus(camId, result.connected, result.ip, result.type);
        if (result.connected) {
            showNotification(`Camera ${camId} connected!`, 'success');
        } else {
            showNotification(`Camera ${camId} not found`, 'error');
        }
    }
}

// ─── BLE Wake ─────────────────────────────────────────────────────────────────

async function connectCamWifi(camId) {
    showNotification(`Joining Camera ${camId} WiFi... page may drop briefly`, 'info');
    // Fire and forget — network drops during switch
    fetch(`/api/${camId}/wifi/connect`, { method: 'POST' }).catch(() => {});
    // Poll until server is back up, then retry camera connection
    setTimeout(async () => {
        let tries = 12;
        while (tries-- > 0) {
            try {
                const res = await fetch(`/api/${camId}/status`);
                if (res.ok) {
                    const data = await res.json();
                    updateCamStatus(camId, data.connected, data.ip, data.type);
                    showNotification(data.connected ? `Camera ${camId} connected!` : `Camera ${camId} not found after WiFi switch`, data.connected ? 'success' : 'error');
                    return;
                }
            } catch (_) {}
            await new Promise(r => setTimeout(r, 1000));
        }
        showNotification('Could not reconnect — try refreshing the page', 'error');
    }, 4000);
}

function toggleFlip(camId) {
    const video = el(`cam${camId}-video`);
    const btn = el(`cam${camId}-btn-flip`);
    const flipped = video.style.transform === 'rotate(180deg)';
    camState[camId].flipped = !flipped;
    video.style.transform = flipped ? '' : 'rotate(180deg)';
    btn.style.background = flipped ? '' : 'var(--primary-color)';
    const pipImg = el(`pip-img-${camId}`);
    if (pipImg) pipImg.style.transform = camState[camId].flipped ? 'rotate(180deg)' : '';
}

async function wakeWifiBle(camId) {
    showNotification(`Scanning for Camera ${camId} via Bluetooth...`, 'info');
    const result = await apiCall(`/${camId}/ble/wake-wifi`, 'POST');
    if (result?.success) {
        showNotification(result.message || `Camera ${camId} WiFi enabled!`, 'success');
        setTimeout(() => retryConnection(camId), 3000);
    } else {
        showNotification(result?.error || `Failed to wake Camera ${camId}`, 'error');
    }
}

// ─── Camera Actions ────────────────────────────────────────────────────────────

async function camAction(camId, action) {
    console.log(`[LCA] camAction cam${camId} action="${action}" recordingState=${camState[camId].recording}`);
    const endpoints = {
        'photo':        [`/${camId}/photo`, 'POST'],
        'video-start':  [`/${camId}/video/start`, 'POST'],
        'video-stop':   [`/${camId}/video/stop`, 'POST'],
        'stream-start': [`/${camId}/stream/start`, 'POST'],
        'stream-stop':  [`/${camId}/stream/stop`, 'POST'],
    };

    const [endpoint, method] = endpoints[action];
    const body = action === 'video-stop' ? { save_to_pc: true } : null;
    const result = await apiCall(endpoint, method, body);

    console.log(`[LCA] camAction cam${camId} action="${action}" result=`, result);
    if (!result) return;

    if (action === 'photo') {
        showNotification(result.success ? `Cam ${camId}: Photo captured!` : `Cam ${camId}: Photo failed`, result.success ? 'success' : 'error');
        if (result.success) {
            // Taking a photo switches the camera to photo mode and back, which
            // restarts the preview stream server-side and breaks the browser's old
            // MJPEG connection. FFmpeg can take several seconds to relock onto the
            // GoPro feed, so poll until the server confirms frames are flowing, then
            // reconnect the <img> once (deterministic — no broken-image flicker).
            reconnectPreviewWhenReady(camId);
        }
    }

    if (action === 'video-start' && result.success) {
        camState[camId].recording = true;
        el(`cam${camId}-btn-rec-start`).disabled = true;
        el(`cam${camId}-btn-rec-stop`).disabled = false;
        el(`cam${camId}-rec-indicator`).classList.remove('hidden');
        const q = el(`cam${camId}-btn-rec-quick`);
        if (q) { q.textContent = 'Stop'; q.classList.add('btn-danger'); q.classList.remove('btn-record'); }
        showNotification(`Cam ${camId}: Recording started`, 'success');
    }

    if (action === 'video-stop' && result.success) {
        camState[camId].recording = false;
        el(`cam${camId}-btn-rec-start`).disabled = false;
        el(`cam${camId}-btn-rec-stop`).disabled = true;
        el(`cam${camId}-rec-indicator`).classList.add('hidden');
        const q = el(`cam${camId}-btn-rec-quick`);
        if (q) { q.textContent = 'Record'; q.classList.remove('btn-danger'); q.classList.add('btn-record'); }
        showNotification(`Cam ${camId}: Recording stopped`, 'info');
    }
}

async function quickRecord(camId) {
    const recording = camState[camId].recording;
    await camAction(camId, recording ? 'video-stop' : 'video-start');
    const btn = el(`cam${camId}-btn-rec-quick`);
    if (btn) btn.textContent = camState[camId].recording ? 'Stop' : 'Record';
}

async function timerPhoto(camId) {
    const delay = parseInt(el(`cam${camId}-delay`).value) || 5;
    const result = await apiCall(`/${camId}/photo/timer`, 'POST', { delay });
    if (result?.success) {
        showNotification(`Cam ${camId}: Photo in ${delay}s...`, 'info');
    }
}

async function toggleInterval(camId) {
    const state = camState[camId];
    const statusEl = el(`cam${camId}-interval-status`);

    if (!state.intervalRunning) {
        const interval = parseInt(el(`cam${camId}-interval`).value) || 10;
        const result = await apiCall(`/${camId}/photo/interval/start`, 'POST', { interval });
        if (result?.success) {
            state.intervalRunning = true;
            statusEl.classList.remove('hidden');
            showNotification(`Cam ${camId}: Interval started (${interval}s)`, 'success');
        }
    } else {
        const result = await apiCall(`/${camId}/photo/interval/stop`, 'POST');
        if (result?.success) {
            state.intervalRunning = false;
            statusEl.classList.add('hidden');
            showNotification(`Cam ${camId}: Interval stopped`, 'info');
        }
    }
}

async function applySettings(camId) {
    const res = el(`cam${camId}-resolution`).value;
    const fps = parseInt(el(`cam${camId}-fps`).value);
    const [r, f] = await Promise.all([
        apiCall(`/${camId}/settings/resolution`, 'POST', { resolution: res }),
        apiCall(`/${camId}/settings/fps`, 'POST', { fps }),
    ]);
    showNotification(
        r?.success && f?.success ? `Cam ${camId}: Settings applied` : `Cam ${camId}: Some settings failed`,
        r?.success && f?.success ? 'success' : 'error'
    );
}

// ─── Both Cameras ─────────────────────────────────────────────────────────────

async function bothCameras(action) {
    if (action === 'photo') {
        await Promise.all([camAction(1, 'photo'), camAction(2, 'photo')]);
    } else if (action === 'video-start') {
        await Promise.all([camAction(1, 'video-start'), camAction(2, 'video-start')]);
    } else if (action === 'video-stop') {
        await Promise.all([camAction(1, 'video-stop'), camAction(2, 'video-stop')]);
    } else if (action === 'stream-start') {
        await Promise.all([startPreview(1), startPreview(2)]);
    } else if (action === 'stream-stop') {
        await Promise.all([stopPreview(1), stopPreview(2)]);
    }
}

// ─── HLS Preview ──────────────────────────────────────────────────────────────

// Poll the server until the MJPEG stream is confirmed producing frames, then
// reconnect the preview <img> (and PiP) once. Used to restore the preview after a
// photo, where the camera switches modes and FFmpeg needs a few seconds to relock.
async function reconnectPreviewWhenReady(camId, maxMs = 60000) {
    const img = el(`cam${camId}-video`);
    const pipImg = el(`pip-img-${camId}`);
    const wantMain = img && img.style.display !== 'none';
    const wantPip = pipImg && el(`pip-stream-${camId}`)?.style.display !== 'none';
    if (!wantMain && !wantPip) return;  // preview wasn't live; nothing to restore

    // The GoPro takes ~15-20s to resume its video feed after a photo (firmware
    // limitation). Show a live countdown-ish status and reconnect the moment frames
    // actually start flowing.
    showNotification(`Cam ${camId}: restoring preview — the GoPro needs ~20s after a photo…`, 'info');
    const start = Date.now();
    while (Date.now() - start < maxMs) {
        let ready = false;
        try {
            ready = (await fetch(`/api/${camId}/stream/ready`).then(r => r.json())).ready;
        } catch (e) { /* server momentarily busy — keep trying */ }
        if (ready) {
            const url = `/api/${camId}/mjpeg?t=${Date.now()}`;
            if (wantMain) img.src = url;
            if (wantPip) pipImg.src = url;
            showNotification(`Cam ${camId}: preview restored (${Math.round((Date.now()-start)/1000)}s)`, 'success');
            return;
        }
        await new Promise(r => setTimeout(r, 700));
    }
    showNotification(`Cam ${camId}: preview still not back — click Start Preview`, 'warning');
}

async function startPreview(camId) {
    showNotification(`Cam ${camId}: Starting preview...`, 'info');
    const result = await apiCall(`/${camId}/stream/start`, 'POST');
    if (!result?.success) {
        showNotification(`Cam ${camId}: Failed to start preview`, 'error');
        return;
    }
    const img = el(`cam${camId}-video`);
    const placeholder = el(`cam${camId}-preview-placeholder`);
    img.src = `/api/${camId}/mjpeg`;
    img.style.display = 'block';
    placeholder.style.display = 'none';
    el(`cam${camId}-btn-start-preview`).style.display = 'none';
    el(`cam${camId}-btn-stop-preview`).style.display = 'inline-block';
    pipShow(camId);
    showNotification(`Cam ${camId}: Preview live`, 'success');
}

async function stopPreview(camId) {
    // Stop tracker if it's running on this camera
    const tStatus = await apiCall('/tracker/status');
    if (tStatus?.active && tStatus.cam_id === camId) {
        await apiCall('/tracker/stop', 'POST');
        _stopTrackPoll(camId);
    }
    await apiCall(`/${camId}/stream/stop`, 'POST');
    const img = el(`cam${camId}-video`);
    img.src = '';
    img.style.display = 'none';
    el(`cam${camId}-preview-placeholder`).style.display = 'flex';
    el(`cam${camId}-btn-start-preview`).style.display = 'inline-block';
    el(`cam${camId}-btn-stop-preview`).style.display = 'none';
    pipHide(camId);
    showNotification(`Cam ${camId}: Preview stopped`, 'info');
}

// ─── Fullscreen ───────────────────────────────────────────────────────────────

function enterFullscreen(elemId) {
    const el_ = el(elemId);
    if (!el_ || el_.style.display === 'none') return;
    (el_.requestFullscreen || el_.webkitRequestFullscreen || el_.mozRequestFullScreen)?.call(el_);
}

// ─── Floating mini player (PiP) ───────────────────────────────────────────────

let _pipCollapsed = false;
let _pipDrag = null;  // { startX, startY, origRight, origBottom }

function pipShow(camId) {
    const stream = el(`pip-stream-${camId}`);
    const img    = el(`pip-img-${camId}`);
    if (stream && img) {
        img.src = `/api/${camId}/mjpeg`;
        img.style.transform = camState[camId]?.flipped ? 'rotate(180deg)' : '';
        stream.style.display = 'block';
    }
    _pipRefreshTitle();
    el('pip').classList.remove('hidden');
}

function pipHide(camId) {
    const stream = el(`pip-stream-${camId}`);
    const img    = el(`pip-img-${camId}`);
    if (stream) stream.style.display = 'none';
    if (img)    img.src = '';
    _pipRefreshTitle();
    // Hide pip entirely if no streams left
    const anyVisible = [1, 2].some(id => el(`pip-stream-${id}`)?.style.display !== 'none');
    if (!anyVisible) el('pip').classList.add('hidden');
}

function _pipRefreshTitle() {
    const active = [1, 2].filter(id => el(`pip-stream-${id}`)?.style.display !== 'none');
    el('pip-title').textContent = active.length === 2 ? 'Cam 1 + 2' : active.length === 1 ? `Cam ${active[0]}` : 'Preview';
}

function pipClose() {
    [1, 2].forEach(id => stopPreview(id));
}

function pipFullscreen(camId) {
    // If no camId given, use whichever stream is visible (prefer cam1)
    const active = camId || [1, 2].find(id => el(`pip-stream-${id}`)?.style.display !== 'none');
    if (!active) return;
    const flipped   = camState[active]?.flipped;
    const transform = flipped ? 'rotate(180deg)' : '';
    const origin    = window.location.origin;
    const html = `<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>LCA \xb7 Cam ${active}</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box }
  body { background:#000; display:flex; align-items:center; justify-content:center;
         width:100vw; height:100vh; overflow:hidden }
  img  { max-width:100%; max-height:100%; object-fit:contain; transform:${transform} }
</style></head><body>
<img src="${origin}/api/${active}/mjpeg">
</body></html>`;
    // Real OS window — Win+Shift+Arrow moves it to another monitor, then F11 to fullscreen there
    const popup = window.open('', `lca-cam${active}`, 'width=960,height=720,resizable=yes');
    if (popup) { popup.document.open(); popup.document.write(html); popup.document.close(); }
}

function pipToggleCollapse() {
    _pipCollapsed = !_pipCollapsed;
    el('pip-body').style.display    = _pipCollapsed ? 'none' : 'block';
    el('pip-collapse-btn').textContent = _pipCollapsed ? '▲' : '—';
}

// Drag to reposition
function pipDragStart(e) {
    if (e.button !== 0) return;
    const pip = el('pip');
    const rect = pip.getBoundingClientRect();
    _pipDrag = {
        startX: e.clientX,
        startY: e.clientY,
        origRight:  window.innerWidth  - rect.right,
        origBottom: window.innerHeight - rect.bottom,
    };
    document.addEventListener('mousemove', _pipDragMove);
    document.addEventListener('mouseup',   _pipDragEnd);
    e.preventDefault();
}

function _pipDragMove(e) {
    if (!_pipDrag) return;
    const pip = el('pip');
    const dx = e.clientX - _pipDrag.startX;
    const dy = e.clientY - _pipDrag.startY;
    pip.style.right  = Math.max(0, _pipDrag.origRight  - dx) + 'px';
    pip.style.bottom = Math.max(0, _pipDrag.origBottom + dy) + 'px';
}

function _pipDragEnd() {
    _pipDrag = null;
    document.removeEventListener('mousemove', _pipDragMove);
    document.removeEventListener('mouseup',   _pipDragEnd);
}

// ─── Media Browser ─────────────────────────────────────────────────────────────

function selectMediaCam(camId, btn) {
    activeMediaCam = camId;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    loadMedia();
}

async function loadMedia() {
    const mediaList = el('media-list');
    mediaList.innerHTML = '<p class="placeholder">Loading...</p>';
    const result = await apiCall(`/${activeMediaCam}/media/list`);
    const ip = camState[activeMediaCam].ip || '10.5.5.9';

    if (result?.files?.length > 0) {
        mediaList.innerHTML = '';
        result.files.reverse().forEach(file => {
            const item = document.createElement('div');
            item.className = 'media-item';
            const isVideo = file.filename.toLowerCase().endsWith('.mp4');
            const thumb = `http://${ip}:8080/gopro/media/thumbnail?path=${file.directory}/${file.filename}`;
            item.innerHTML = `
                <img src="${thumb}" alt="${file.filename}" onerror="this.style.display='none'">
                <div class="media-item-info">
                    <div class="media-item-name">${file.filename}</div>
                    <div class="media-item-actions">
                        <button class="btn btn-secondary" onclick="downloadMedia(${activeMediaCam},'${file.directory}','${file.filename}')">DL</button>
                        <button class="btn btn-primary" onclick="playMedia('${file.url}',${isVideo})">Play</button>
                    </div>
                </div>`;
            mediaList.appendChild(item);
        });
    } else {
        mediaList.innerHTML = '<p class="placeholder">No media found</p>';
    }
}

async function downloadMedia(camId, directory, filename) {
    showNotification(`Downloading ${filename}...`, 'info');
    const result = await apiCall(`/${camId}/media/download`, 'POST', { directory, filename });
    showNotification(result?.success ? `Downloaded: ${filename}` : 'Download failed', result?.success ? 'success' : 'error');
}

async function showDownloads(btn) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    const mediaList = el('media-list');
    const result = await apiCall('/downloads/list');

    if (result?.files?.length > 0) {
        mediaList.innerHTML = '';
        result.files.forEach(filename => {
            const item = document.createElement('div');
            item.className = 'media-item';
            const isVideo = filename.toLowerCase().endsWith('.mp4');
            item.innerHTML = `
                <div class="media-item-info" style="padding:20px">
                    <div class="media-item-name">${filename}</div>
                    <div class="media-item-actions">
                        <button class="btn btn-primary" onclick="playMedia('/downloads/${filename}',${isVideo})">Play</button>
                    </div>
                </div>`;
            mediaList.appendChild(item);
        });
    } else {
        mediaList.innerHTML = '<p class="placeholder">No downloads</p>';
    }
}

function playMedia(url, isVideo) {
    el('playback-section').classList.remove('hidden');
    const vp = el('video-player');
    const iv = el('image-viewer');
    if (isVideo) {
        vp.classList.remove('hidden'); iv.classList.add('hidden');
        vp.src = url; vp.play();
    } else {
        vp.classList.add('hidden'); iv.classList.remove('hidden');
        iv.src = url;
    }
}

function closePlayback() {
    el('playback-section').classList.add('hidden');
    const vp = el('video-player');
    vp.pause(); vp.src = '';
    el('image-viewer').src = '';
}

// ─── Person Tracking ──────────────────────────────────────────────────────────

let _trackPollInterval = null;

async function toggleTracking(camId) {
    const btn = el(`cam${camId}-btn-track`);
    const status = await apiCall('/tracker/status');

    if (status?.active && status.cam_id === camId) {
        // Stop tracking
        await apiCall('/tracker/stop', 'POST');
        _stopTrackPoll(camId);
        showNotification(`Cam ${camId}: Tracking stopped`, 'info');
    } else {
        // Start tracking
        const result = await apiCall(`/tracker/${camId}/start`, 'POST', { flip: camState[camId].flipped });
        if (result?.success) {
            btn.textContent = 'Tracking';
            btn.style.background = 'var(--success-color)';
            const img = el(`cam${camId}-video`);
            if (img && img.style.display !== 'none') {
                img.src = `/api/${camId}/mjpeg/annotated`;
            }
            showNotification(`Cam ${camId}: Person tracking active`, 'success');
            _startTrackPoll(camId);
        } else {
            showNotification(result?.error || `Cam${camId}: Start preview first`, 'error');
        }
    }
}

function _startTrackPoll(camId) {
    if (_trackPollInterval) clearInterval(_trackPollInterval);
    _trackPollInterval = setInterval(async () => {
        const status = await apiCall('/tracker/status');
        if (!status) return;
        _updateTrackerUI(status, camId);
        if (!status.active) {
            clearInterval(_trackPollInterval);
            _trackPollInterval = null;
        }
    }, 500);
}

function _stopTrackPoll(camId) {
    if (_trackPollInterval) { clearInterval(_trackPollInterval); _trackPollInterval = null; }
    const btn = el(`cam${camId}-btn-track`);
    if (btn) { btn.textContent = 'Track'; btn.style.background = ''; }
    const indicator = el(`cam${camId}-track-indicator`);
    if (indicator) indicator.classList.add('hidden');
    // Switch back to plain MJPEG and restore CSS flip if needed
    const img = el(`cam${camId}-video`);
    if (img && img.src.includes('/annotated')) {
        img.src = `/api/${camId}/mjpeg`;
    }
}

function _updateTrackerUI(status, camId) {
    const activeCam = status.cam_id || camId;
    const indicator = el(`cam${activeCam}-track-indicator`);
    const textEl = el(`cam${activeCam}-track-text`);
    const btn = el(`cam${activeCam}-btn-track`);

    if (status.active) {
        if (indicator) indicator.classList.remove('hidden');
        if (btn) { btn.textContent = 'Tracking'; btn.style.background = 'var(--success-color)'; }
        if (textEl) {
            if (status.detected) {
                const dx = status.offset_x >= 0 ? `+${status.offset_x.toFixed(2)}` : status.offset_x.toFixed(2);
                const dy = status.offset_y >= 0 ? `+${status.offset_y.toFixed(2)}` : status.offset_y.toFixed(2);
                textEl.textContent = `Face (x:${dx} y:${dy})`;
            } else {
                textEl.textContent = 'Scanning...';
            }
        }
        // Move gimbal sliders to reflect actual tracked position
        // Pan slider is inverted (sends 180-val), so slider = 180 - current_angle
        const panVal = 180 - status.base_angle;
        const panSlider = el('base-angle');
        if (panSlider) panSlider.value = panVal;
        const panDisp = el('base-angle-val');
        if (panDisp) panDisp.textContent = `${panVal}°`;
        const tiltSlider = el('cam-us');
        if (tiltSlider) tiltSlider.value = status.cam_us;
        const tiltDisp = el('cam-us-val');
        if (tiltDisp) tiltDisp.textContent = `${status.cam_us}\u03bcs`;
    } else {
        _stopTrackPoll(activeCam);
    }
}

// ─── Arduino ──────────────────────────────────────────────────────────────────

const _arduinoTimers = {};
function debounceArduino(key, fn, ms = 80) {
    clearTimeout(_arduinoTimers[key]);
    _arduinoTimers[key] = setTimeout(fn, ms);
}

async function arduinoConnect() {
    showNotification('Connecting to Arduino...', 'info');
    const result = await apiCall('/arduino/connect', 'POST');
    updateArduinoStatus(result);
    showNotification(result?.success ? `Arduino connected on ${result.port}` : 'Arduino not found', result?.success ? 'success' : 'error');
}

function updateArduinoStatus(data) {
    const pill = el('arduino-status');
    const txt = el('arduino-status-text');
    const controls = el('arduino-controls');
    if (data?.connected) {
        pill.classList.add('connected'); pill.classList.remove('disconnected');
        txt.textContent = data.port || 'Connected';
        controls.style.display = 'grid';
        // Refresh gimbal readouts from known server state
        if (data.base_angle !== undefined) {
            const panVal = 180 - data.base_angle;
            const panDisp = el('base-angle-val');
            if (panDisp) panDisp.textContent = `${panVal}°`;
            const panSlider = el('base-angle');
            if (panSlider) panSlider.value = panVal;
        }
        if (data.cam_us !== undefined) {
            const tiltDisp = el('cam-us-val');
            if (tiltDisp) tiltDisp.textContent = `${data.cam_us}\u03bcs`;
            const tiltSlider = el('cam-us');
            if (tiltSlider) tiltSlider.value = data.cam_us;
        }
    } else {
        pill.classList.remove('connected'); pill.classList.add('disconnected');
        txt.textContent = 'Disconnected';
        controls.style.display = 'none';
    }
}

async function arduinoPost(endpoint, body = {}) {
    const result = await apiCall(`/arduino/${endpoint}`, 'POST', body);
    if (result && !result.success) showNotification(`Arduino: ${endpoint} failed`, 'error');
    return result;
}

function arduinoRailSlider() {
    el('rail-speed-val').textContent = el('rail-speed').value;
    el('rail-duration-val').textContent = el('rail-duration').value;
    debounceArduino('rail-settings', arduinoSendSettings, 150);
}

async function arduinoSendSettings() {
    await arduinoPost('rail/settings', {
        speed: parseInt(el('rail-speed').value),
        duration: parseInt(el('rail-duration').value),
    });
}

// ── Gimbal limits (persisted to localStorage) ─────────────────────────────────
const _GIMBAL_LIMIT_KEY = 'gimbal_limits';
const _gimbalLimits = (() => {
    try { return JSON.parse(localStorage.getItem(_GIMBAL_LIMIT_KEY)) || {}; } catch { return {}; }
})();
function _gl(key, def) { return _gimbalLimits[key] ?? def; }

function _saveGimbalLimits() {
    localStorage.setItem(_GIMBAL_LIMIT_KEY, JSON.stringify(_gimbalLimits));
}

function _initGimbalUI() {
    const bMin = el('base-min'), bMax = el('base-max');
    const cMin = el('cam-min'),  cMax = el('cam-max');
    if (bMin) bMin.value = _gl('baseMin', 45);
    if (bMax) bMax.value = _gl('baseMax', 144);
    if (cMin) cMin.value = _gl('camMin', 1300);
    if (cMax) cMax.value = _gl('camMax', 2350);
    const baseSlider = el('base-angle'), camSlider = el('cam-us');
    if (baseSlider) { baseSlider.min = _gl('baseMin', 45);   baseSlider.max = _gl('baseMax', 144); }
    if (camSlider)  { camSlider.min  = _gl('camMin', 1300);  camSlider.max  = _gl('camMax', 2350); }
}

function _syncTrackerLimits() {
    fetch('/api/tracker/limits', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            pan_min:  _gl('baseMin', 45),  pan_max:  _gl('baseMax', 144),
            tilt_min: _gl('camMin', 1300), tilt_max: _gl('camMax', 2350),
        })
    });
}

function setTrackSpeed(speed) {
    // speed 1–10: linearly scales ramp rates and poll interval
    apiCall('/tracker/params', 'POST', {
        ramp_pan_rate:  speed * 0.5,          // 1→0.5°/s … 10→5.0°/s
        ramp_tilt_rate: speed * 12,           // 1→12μs/s … 10→120μs/s
        poll_interval:  Math.max(1.0, 4.0 - speed * 0.3), // 1→3.7s … 10→1.0s
    });
}

function applyGimbalLimit() {
    _gimbalLimits.baseMin = Math.max(0,   Math.min(180,  parseInt(el('base-min')?.value ?? 45)));
    _gimbalLimits.baseMax = Math.max(0,   Math.min(180,  parseInt(el('base-max')?.value ?? 144)));
    _gimbalLimits.camMin  = Math.max(400, Math.min(2600, parseInt(el('cam-min')?.value  ?? 1300)));
    _gimbalLimits.camMax  = Math.max(400, Math.min(2600, parseInt(el('cam-max')?.value  ?? 2350)));
    _saveGimbalLimits();
    _syncTrackerLimits();
    _initGimbalUI();
    // Clamp current slider positions to new limits
    const bSlider = el('base-angle');
    if (bSlider) arduinoBaseAngle(Math.max(_gl('baseMin',45), Math.min(_gl('baseMax',144), parseInt(bSlider.value))));
    const cSlider = el('cam-us');
    if (cSlider) arduinoCamUs(Math.max(_gl('camMin',1300), Math.min(_gl('camMax',2350), parseInt(cSlider.value))));
}

function resetGimbalLimits(axis) {
    if (axis === 'base') { _gimbalLimits.baseMin = 45;   _gimbalLimits.baseMax = 144; }
    else                 { _gimbalLimits.camMin  = 1300; _gimbalLimits.camMax  = 2350; }
    _saveGimbalLimits();
    _initGimbalUI();
}

function arduinoBaseAngle(val) {
    val = Math.max(_gl('baseMin', 45), Math.min(_gl('baseMax', 144), parseInt(val)));
    const slider = el('base-angle');
    if (slider) slider.value = val;
    const disp = el('base-angle-val');
    if (disp) disp.textContent = `${val}°`;
    // Invert so slider-left = physical left
    debounceArduino('base-angle', () => arduinoPost('gimbal/base', { angle: 180 - val }));
}

function arduinoCamUs(val) {
    val = Math.max(_gl('camMin', 1300), Math.min(_gl('camMax', 2350), parseInt(val)));
    const slider = el('cam-us');
    if (slider) slider.value = val;
    const disp = el('cam-us-val');
    if (disp) disp.textContent = `${val}\u03bcs`;
    debounceArduino('cam-us', () => arduinoPost('gimbal/cam', { us: val }));
}

function arduinoSetBase(angle) {
    arduinoBaseAngle(angle);
}

function arduinoSetCamUs(us) {
    arduinoCamUs(us);
}

// ─── Socket.IO ────────────────────────────────────────────────────────────────

socket.on('connection_status', (data) => {
    console.log('[LCA] socket connection_status', data);
    if (data.cam1 !== undefined) updateCamStatus(1, data.cam1);
    if (data.cam2 !== undefined) updateCamStatus(2, data.cam2);
});

socket.on('photo_taken', (data) => {
    if (data.success) showNotification(`Cam ${data.cam_id || '?'}: Photo captured!`, 'success');
});

socket.on('download_progress', (data) => {
    console.log('[LCA] socket download_progress', data);
    if (data.progress === 0) {
        showNotification(`Cam ${data.cam_id}: Saving "${data.filename}" to PC...`, 'info');
    }
});

socket.on('download_complete', (data) => {
    console.log('[LCA] socket download_complete', data);
    if (data.success) {
        showNotification(`Cam ${data.cam_id}: Saved "${data.filename}"`, 'success');
    } else {
        showNotification(`Cam ${data.cam_id}: Auto-save failed — ${data.error || 'unknown error'}`, 'error');
    }
});

socket.on('audio_status', (data) => {
    console.log('[LCA] socket audio_status', data);
    if (data.event === 'start' && data.started) {
        showNotification(`🎙 Audio recording @ ${data.rate} Hz`, 'success');
        if (data.warning) showNotification(data.warning, 'warning');
    } else if (data.event === 'start' && data.error) {
        showNotification(`Audio: ${data.error}`, 'error');
    }
});

socket.on('audio_saved', (data) => {
    console.log('[LCA] socket audio_saved', data);
    if (data.files && data.files.length) {
        showNotification(`🎙 Audio saved: ${data.files.join(', ')} (${data.duration}s)`, 'success');
    } else if (data.error) {
        showNotification(`Audio: ${data.error}`, 'warning');
    }
});

// Keep-alive for both cameras
setInterval(() => {
    socket.emit('keep_alive_cam', { cam_id: 1 });
    socket.emit('keep_alive_cam', { cam_id: 2 });
}, 30000);

// Poll status every 6 seconds
setInterval(async () => {
    const [s1, s2] = await Promise.all([apiCall('/1/status'), apiCall('/2/status')]);
    if (s1) updateCamStatus(1, s1.connected, s1.ip, s1.type);
    if (s2) updateCamStatus(2, s2.connected, s2.ip, s2.type);
}, 6000);

// ─── RGB Lights ───────────────────────────────────────────────────────────────

const _lightTimers = {};

function debounceLightSend(key, fn, ms = 60) {
    clearTimeout(_lightTimers[key]);
    _lightTimers[key] = setTimeout(fn, ms);
}

async function lightApi(lightId, path, method = 'GET', body = null) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    try {
        const r = await fetch(`/api/lights/${lightId}${path}`, opts);
        return r.ok ? r.json() : null;
    } catch { return null; }
}

function rgbToHex(r, g, b) {
    return '#' + [r, g, b].map(v => v.toString(16).padStart(2, '0')).join('');
}
function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

// ─── RGB Arduino Section-Level Connect ───────────────────────────────────────

async function refreshLightPorts() {
    const data = await fetch('/api/lights/ports').then(r => r.json()).catch(() => ({ ports: [] }));
    const sel = document.getElementById('rgb-port-select');
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">-- port --</option>';
    (data.ports || []).forEach(p => {
        const o = document.createElement('option');
        o.value = p; o.textContent = p;
        if (p === cur) o.selected = true;
        sel.appendChild(o);
    });
}

async function connectRGBArduino() {
    const port = document.getElementById('rgb-port-select')?.value;
    if (!port) { showNotification('Select a port first', 'warning'); return; }
    const data = await fetch('/api/lights/arduino/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ port })
    }).then(r => r.json()).catch(() => null);
    _updateRGBStatus(data);
    showNotification(data?.connected ? `RGB Arduino connected on ${data.port}` : 'Connection failed', data?.connected ? 'success' : 'error');
}

async function disconnectRGBArduino() {
    await fetch('/api/lights/arduino/disconnect', { method: 'POST' });
    _updateRGBStatus({ connected: false });
}

function _updateRGBStatus(data) {
    const dot = document.getElementById('rgb-status-dot');
    const connBtn = document.getElementById('rgb-connect-btn');
    const discBtn = document.getElementById('rgb-disconnect-btn');
    const label = document.getElementById('rgb-status-label');
    if (!dot) return;
    const connected = data?.connected;
    const fallback = data?.fallback;
    if (connected) {
        dot.className = 'status-dot connected';
        if (label) label.textContent = data.port;
    } else if (fallback) {
        dot.className = 'status-dot connected';
        if (label) label.textContent = `via gimbal (${data.fallback_port})`;
    } else {
        dot.className = 'status-dot disconnected';
        if (label) label.textContent = '';
    }
    if (connBtn) connBtn.style.display = connected ? 'none' : '';
    if (discBtn) discBtn.style.display = connected ? '' : 'none';
}

async function loadRGBStatus() {
    const data = await fetch('/api/lights/arduino').then(r => r.json()).catch(() => null);
    _updateRGBStatus(data);
    if (data?.port) {
        const sel = document.getElementById('rgb-port-select');
        if (sel) {
            // ensure current port is in the list
            if (![...sel.options].find(o => o.value === data.port)) {
                const o = document.createElement('option');
                o.value = data.port; o.textContent = data.port;
                sel.appendChild(o);
            }
            sel.value = data.port;
        }
    }
}

// ─── Light Panel Builder ──────────────────────────────────────────────────────

function _buildLightPanel(status) {
    const id = status.id;
    _tlInit(id);  // ensure _tlDuration[id] exists before we read it
    const hex = rgbToHex(status.r ?? 255, status.g ?? 255, status.b ?? 255);
    const bri = status.brightness ?? 100;
    const name = (status.name || `Light ${id}`).replace(/</g, '&lt;');
    return `
    <div class="light-panel" id="light-panel-${id}">
        <div class="light-panel-header">
            <span class="light-name" id="light${id}-name" contenteditable="true"
                onblur="saveLightName(${id})" onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur()}"
                title="Click to rename">${name}</span>
            <div style="display:flex;gap:5px;align-items:center">
                <button class="btn btn-sm btn-preview" id="light${id}-preview-btn" onclick="togglePreview(${id})">Preview</button>
                <button class="btn btn-sm btn-danger" style="padding:2px 7px;font-size:0.7rem" onclick="removeLight(${id}, '${(status.name || '').replace(/'/g, "\\'")}')" title="Remove">✕</button>
            </div>
        </div>
        <div class="light-pins-row">
            <label>R pin <input type="number" class="pin-input" id="light${id}-pin-r" value="${status.pin_r ?? 9}" min="0" max="53"></label>
            <label>G pin <input type="number" class="pin-input" id="light${id}-pin-g" value="${status.pin_g ?? 10}" min="0" max="53"></label>
            <label>B pin <input type="number" class="pin-input" id="light${id}-pin-b" value="${status.pin_b ?? 11}" min="0" max="53"></label>
            <button class="btn btn-sm btn-secondary" onclick="saveLightPins(${id})">Save Pins</button>
        </div>
        <div class="light-controls">
            <div class="light-color-row">
                <label>Color</label>
                <input type="color" id="light${id}-color" value="${hex}" oninput="sendLightColor(${id})">
            </div>
            <div class="light-bri-row">
                <label>Brightness</label>
                <input type="range" id="light${id}-bri" min="0" max="100" value="${bri}"
                    oninput="sendLightBrightness(${id})">
                <span id="light${id}-bri-val">${bri}%</span>
            </div>
            <div class="light-presets">
                <button class="btn btn-sm preset-btn" style="background:#fff;color:#000" onclick="setLightPreset(${id},255,255,255)">White</button>
                <button class="btn btn-sm preset-btn" style="background:#ff6600" onclick="setLightPreset(${id},255,102,0)">Warm</button>
                <button class="btn btn-sm preset-btn" style="background:#00f" onclick="setLightPreset(${id},0,0,255)">Blue</button>
                <button class="btn btn-sm preset-btn" style="background:#f00" onclick="setLightPreset(${id},255,0,0)">Red</button>
                <button class="btn btn-sm preset-btn" style="background:#00ff00;color:#000" onclick="setLightPreset(${id},0,255,0)">Green</button>
                <button class="btn btn-sm preset-btn" style="background:#8800ff" onclick="setLightPreset(${id},136,0,255)">Purple</button>
            </div>
            <div class="light-effects">
                <button class="btn btn-sm btn-secondary" onclick="sendLightEffect(${id},'RAINBOW')">Rainbow</button>
                <button class="btn btn-sm btn-secondary" onclick="sendLightEffect(${id},'FADE')">Fade</button>
                <button class="btn btn-sm btn-danger" onclick="sendLightEffect(${id},'STOP')">Stop FX</button>
                <button class="btn btn-sm btn-danger" onclick="turnLightOff(${id})">Off</button>
            </div>
        </div>
        <div class="light-timeline">
            <div class="tl-toolbar">
                <button class="btn btn-sm btn-primary" onclick="tlAddBlock(${id})">+ Block</button>
                <button class="btn btn-sm btn-secondary" onclick="tlPasteBlock(${id})" title="Paste copied block (Ctrl+V)">Paste</button>
                <button class="btn btn-sm btn-play" id="light${id}-tl-play" onclick="tlPlay(${id})">&#9654; Play</button>
                <button class="btn btn-sm btn-secondary" onclick="tlStop(${id})">&#9632; Stop</button>
                <button class="btn btn-sm tl-loop-btn" id="light${id}-tl-loop" onclick="tlToggleLoop(${id})" title="Loop">&#8635; Loop</button>
                <button class="btn btn-sm btn-secondary" onclick="tlSave(${id})" title="Save JSON">Save</button>
                <label class="btn btn-sm btn-secondary" style="cursor:pointer;margin:0" title="Load JSON">
                    Load<input type="file" accept=".json" style="display:none" onchange="tlLoad(${id},this)">
                </label>
                <label style="font-size:0.75rem;color:var(--text-secondary);margin-left:6px;display:flex;align-items:center;gap:4px">
                    Total<input type="number" id="light${id}-tl-duration" min="1" step="1"
                        value="${_tlDuration[id] || 30}"
                        style="width:52px;padding:2px 4px;font-size:0.75rem;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:3px;color:var(--text-primary)"
                        oninput="tlSetDuration(${id},+this.value)">s
                </label>
            </div>
            <div class="tl-canvas" id="light${id}-tl-canvas"></div>
            <div class="tl-editor" id="light${id}-tl-editor"></div>
        </div>
    </div>`;
}

async function renderLights() {
    const data = await fetch('/api/lights').then(r => r.json()).catch(() => []);
    _lightsCache = data;
    const grid = document.getElementById('lights-grid');
    if (grid) grid.innerHTML = data.map(_buildLightPanel).join('');
    mtRender();
}

async function addLight() {
    await fetch('/api/lights', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    await renderLights();
}

async function removeLight(lightId, lightName) {
    if (!confirm(`Delete "${lightName || 'this light'}"? This cannot be undone.`)) return;
    await fetch(`/api/lights/${lightId}`, { method: 'DELETE' });
    await renderLights();
}

async function saveLightName(lightId) {
    const el = document.getElementById(`light${lightId}-name`);
    if (!el) return;
    const name = el.textContent.trim();
    if (!name) return;
    await fetch(`/api/lights/${lightId}/name`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });
}

async function saveLightPins(lightId) {
    const pin_r = parseInt(document.getElementById(`light${lightId}-pin-r`)?.value ?? 9);
    const pin_g = parseInt(document.getElementById(`light${lightId}-pin-g`)?.value ?? 10);
    const pin_b = parseInt(document.getElementById(`light${lightId}-pin-b`)?.value ?? 11);
    await lightApi(lightId, '/pins', 'POST', { pin_r, pin_g, pin_b });
    showNotification(`Light ${lightId} pins saved`, 'success');
}

// ─── Preview toggle ───────────────────────────────────────────────────────────

const _lightPreviewing = {};

function togglePreview(lightId) {
    const on = !_lightPreviewing[lightId];
    _lightPreviewing[lightId] = on;
    const btn = document.getElementById(`light${lightId}-preview-btn`);
    if (btn) {
        btn.textContent = on ? 'Preview ON' : 'Preview';
        btn.classList.toggle('btn-preview-on', on);
        btn.classList.toggle('btn-preview', !on);
    }
    if (on) {
        // Immediately push current color to lights
        const hex = document.getElementById(`light${lightId}-color`)?.value;
        const bri = parseInt(document.getElementById(`light${lightId}-bri`)?.value ?? 100);
        if (hex) {
            const { r, g, b } = hexToRgb(hex);
            lightApi(lightId, '/color', 'POST', { r, g, b }).then(() =>
                lightApi(lightId, '/brightness', 'POST', { brightness: bri })
            );
        }
    } else {
        lightApi(lightId, '/off', 'POST');
    }
}

function sendLightColor(lightId) {
    const hex = document.getElementById(`light${lightId}-color`)?.value;
    if (!hex) return;
    const { r, g, b } = hexToRgb(hex);
    if (_lightPreviewing[lightId]) {
        debounceLightSend(`color${lightId}`, () => lightApi(lightId, '/color', 'POST', { r, g, b }));
    }
}

function sendLightBrightness(lightId) {
    const bri = parseInt(document.getElementById(`light${lightId}-bri`)?.value ?? 100);
    const briVal = document.getElementById(`light${lightId}-bri-val`);
    if (briVal) briVal.textContent = bri + '%';
    if (_lightPreviewing[lightId]) {
        debounceLightSend(`bri${lightId}`, () => lightApi(lightId, '/brightness', 'POST', { brightness: bri }));
    }
}

function setLightPreset(lightId, r, g, b) {
    const col = document.getElementById(`light${lightId}-color`);
    if (col) col.value = rgbToHex(r, g, b);
    if (_lightPreviewing[lightId]) {
        lightApi(lightId, '/color', 'POST', { r, g, b });
    }
}

function sendLightEffect(lightId, effect) {
    lightApi(lightId, '/effect', 'POST', { effect });
}

function turnLightOff(lightId) {
    _lightPreviewing[lightId] = false;
    const btn = document.getElementById(`light${lightId}-preview-btn`);
    if (btn) { btn.textContent = 'Preview'; btn.classList.remove('btn-preview-on'); btn.classList.add('btn-preview'); }
    lightApi(lightId, '/off', 'POST');
}

// ─── Timeline (block-based) ───────────────────────────────────────────────────

let _projectName  = '';
let _projectNotes = '';
let _lightsCache  = [];  // last-fetched lights list — used by master timeline

const _tlBlocks      = {};  // lightId → [{color, brightness, duration, start}]
const _tlTransitions = {};  // lightId → [{color_mode, brightness_mode, duration}]
const _tlSelected    = {};  // lightId → block index or null
const _tlLoop        = {};  // lightId → bool
const _tlDuration    = {};  // lightId → explicit total seconds

let _tlDrag = null;  // active drag: {type:'move'|'resize', lightId, idx, startX, origVal, totalDur, canvasWidth}

let _snapEnabled    = true;   // global snap-to-blocks toggle
let _blockClipboard = null;   // { type: 'light'|'rail'|'cam1'|'cam2', block: {...} }

const TL_TRANSITION_MODES = ['cut', 'fade', 'dissolve'];
// cut     = instant_start/instant_start
// fade    = gradual/gradual
// dissolve= gradual color / instant_start brightness

function _tlTrToServer(mode, dur) {
    if (mode === 'fade')     return { color_mode: 'gradual',        brightness_mode: 'gradual',        duration: dur };
    if (mode === 'dissolve') return { color_mode: 'gradual',        brightness_mode: 'instant_start',  duration: dur };
    return                          { color_mode: 'instant_start',  brightness_mode: 'instant_start',  duration: dur };
}

function _tlTrFromServer(tr) {
    if (!tr) return { mode: 'cut', duration: 1.0 };
    if (tr.color_mode === 'gradual' && tr.brightness_mode === 'gradual')       return { mode: 'fade',     duration: tr.duration };
    if (tr.color_mode === 'gradual' && tr.brightness_mode === 'instant_start') return { mode: 'dissolve', duration: tr.duration };
    return { mode: 'cut', duration: tr.duration ?? 1.0 };
}

// Keep a local UI-friendly transitions array per light
const _tlTrUI = {}; // lightId → [{mode, duration}]

function _tlInit(lightId) {
    if (!_tlBlocks[lightId])                _tlBlocks[lightId]      = [];
    if (!_tlTrUI[lightId])                  _tlTrUI[lightId]        = [];
    if (!_tlLoop[lightId])                  _tlLoop[lightId]        = false;
    if (_tlSelected[lightId] === undefined) _tlSelected[lightId]    = null;
    if (_tlDuration[lightId] === undefined) _tlDuration[lightId]    = 30;
    if (!_tlDuration[lightId])    _tlDuration[lightId]    = 30;
}

// Assign sequential start times to any blocks missing the start field
function _tlMigrateStarts(lightId) {
    const blocks = _tlBlocks[lightId] || [];
    const trs    = _tlTrUI[lightId]   || [];
    let t = 0;
    for (let i = 0; i < blocks.length; i++) {
        if (blocks[i].start === undefined) blocks[i].start = t;
        t = blocks[i].start + blocks[i].duration;
        if (i < blocks.length - 1) {
            const tr = trs[i] ?? { mode: 'cut', duration: 1.0 };
            if (tr.mode !== 'cut') t += tr.duration;
        }
    }
}

function tlSetDuration(lightId, val) {
    _tlInit(lightId);
    _tlDuration[lightId] = Math.max(1, parseFloat(val) || 30);
    tlRenderCanvas(lightId);
    _tlAutoSave(lightId);
}

// Convert per-light blocks → absolute-timed segments for master timeline display
function _tlAbsoluteSegments(lightId) {
    const blocks = _tlBlocks[lightId] || [];
    const trs    = _tlTrUI[lightId]   || [];
    const segs   = [];
    for (let i = 0; i < blocks.length; i++) {
        const b   = blocks[i];
        const bri = b.brightness / 100;
        const r   = parseInt(b.color.slice(1, 3), 16);
        const g   = parseInt(b.color.slice(3, 5), 16);
        const bl  = parseInt(b.color.slice(5, 7), 16);
        const rgb = `rgb(${Math.round(r*bri)},${Math.round(g*bri)},${Math.round(bl*bri)})`;
        const start = b.start ?? 0;
        segs.push({ start, duration: b.duration, color: rgb, type: 'block', blockIdx: i });
        if (i < blocks.length - 1) {
            const tr = trs[i] ?? { mode: 'cut', duration: 1.0 };
            if (tr.mode !== 'cut' && tr.duration > 0) {
                const nb   = blocks[i + 1];
                const nbri = nb.brightness / 100;
                const nr   = parseInt(nb.color.slice(1, 3), 16);
                const ng   = parseInt(nb.color.slice(3, 5), 16);
                const nbl  = parseInt(nb.color.slice(5, 7), 16);
                const nextRgb = `rgb(${Math.round(nr*nbri)},${Math.round(ng*nbri)},${Math.round(nbl*nbri)})`;
                segs.push({ start: start + b.duration, duration: tr.duration, fromColor: rgb, toColor: nextRgb, type: 'transition', mode: tr.mode });
            }
        }
    }
    return segs;
}

function tlRenderCanvas(lightId) {
    _tlInit(lightId);
    const canvas = document.getElementById(`light${lightId}-tl-canvas`);
    if (!canvas) return;

    const blocks   = _tlBlocks[lightId];
    const trs      = _tlTrUI[lightId];
    const duration = _tlDuration[lightId] || 30;

    // Sync duration input
    const durInput = document.getElementById(`light${lightId}-tl-duration`);
    if (durInput) durInput.value = duration;

    if (!blocks.length) {
        canvas.innerHTML = '<span class="tl-empty">No blocks — click + Block to add</span>';
        tlRenderEditor(lightId);
        mtRender();
        return;
    }

    _tlMigrateStarts(lightId);

    let html = '';
    for (let i = 0; i < blocks.length; i++) {
        const b   = blocks[i];
        const sel = _tlSelected[lightId] === i;
        const bri = b.brightness / 100;
        const r   = parseInt(b.color.slice(1,3), 16);
        const g   = parseInt(b.color.slice(3,5), 16);
        const bb  = parseInt(b.color.slice(5,7), 16);
        const dispCol = `rgb(${Math.round(r*bri)},${Math.round(g*bri)},${Math.round(bb*bri)})`;
        const start   = b.start ?? 0;
        const leftPct = (start / duration * 100).toFixed(3);
        const wPct    = (b.duration / duration * 100).toFixed(3);

        // Fade-in overlay (left edge)
        const fiPct = (b.fadeIn > 0) ? Math.min(50, b.fadeIn / b.duration * 100).toFixed(1) : 0;

        // Right-transition overlay (inside block, right edge)
        const nbBlock = blocks[i + 1];
        const tr = b.transition || {};
        let trOverlay = '';
        if (nbBlock && (tr.duration || 0) > 0 && (tr.color || tr.brightness)) {
            const trW = Math.min(50, tr.duration / b.duration * 100).toFixed(1);
            let toColor;
            if (tr.color) {
                const nbri = nbBlock.brightness / 100;
                const nr  = parseInt(nbBlock.color.slice(1,3), 16);
                const ng  = parseInt(nbBlock.color.slice(3,5), 16);
                const nbb = parseInt(nbBlock.color.slice(5,7), 16);
                toColor = `rgb(${Math.round(nr*nbri)},${Math.round(ng*nbri)},${Math.round(nbb*nbri)})`;
            } else {
                // Brightness-only: fade toward next block's brightness level
                const nextScale = nbBlock.brightness / 100;
                toColor = `rgb(${Math.round(r*nextScale)},${Math.round(g*nextScale)},${Math.round(bb*nextScale)})`;
            }
            trOverlay = `<div class="tl-tr-overlay" style="width:${trW}%;background:linear-gradient(to right,transparent,${toColor})"></div>`;
        }

        html += `<div class="tl-block${sel ? ' tl-block-sel' : ''}"
            style="left:${leftPct}%;width:${wPct}%;background:${dispCol}"
            onmousedown="tlBlockMouseDown(event,${lightId},${i})"
            onclick="tlSelectBlock(${lightId},${i})"
            title="${b.duration.toFixed(1)}s @ ${start.toFixed(1)}s">
            <div class="tl-resize-handle left" onmousedown="tlResizeLeftMouseDown(event,${lightId},${i})"></div>
            ${fiPct > 0 ? `<div class="tl-fade-in" style="width:${fiPct}%"></div>` : ''}
            <span class="tl-block-label">${b.duration.toFixed(1)}s</span>
            ${trOverlay}
            <div class="tl-resize-handle" onmousedown="tlResizeMouseDown(event,${lightId},${i})"></div>
        </div>`;
    }

    canvas.innerHTML = html;
    tlRenderEditor(lightId);
    mtRender();
}

// ── Drag to move / resize blocks ─────────────────────────────────────────────

// Returns an array of snap candidate positions (seconds) for a given light,
// excluding the block currently being dragged.
function _tlSnapTargets(lightId, excludeIdx) {
    const targets = [0];
    for (let i = 0; i < (_tlBlocks[lightId] || []).length; i++) {
        if (i === excludeIdx) continue;
        const b = _tlBlocks[lightId][i];
        targets.push(b.start ?? 0);
        targets.push((b.start ?? 0) + b.duration);
    }
    return targets;
}

// Snap a value to the nearest candidate within threshold (seconds). Returns
// the snapped value, or the original value if nothing is within range.
function _tlSnap(targets, value, threshold) {
    if (!_snapEnabled) return value;
    let best = value, bestDist = threshold;
    for (const t of targets) {
        const d = Math.abs(value - t);
        if (d < bestDist) { best = t; bestDist = d; }
    }
    return best;
}

function toggleSnap() {
    _snapEnabled = !_snapEnabled;
    document.querySelectorAll('.snap-toggle-btn').forEach(b =>
        b.classList.toggle('tl-loop-on', _snapEnabled));
}

function tlBlockMouseDown(e, lightId, idx) {
    if (e.target.classList.contains('tl-resize-handle')) return;
    e.preventDefault();
    const canvas = document.getElementById(`light${lightId}-tl-canvas`);
    if (!canvas) return;
    const block = _tlBlocks[lightId]?.[idx];
    if (!block) return;
    _tlDrag = {
        type:        'move',
        lightId, idx,
        startX:      e.clientX,
        origStart:   block.start ?? 0,
        origDur:     block.duration,
        totalDur:    _tlDuration[lightId] || 30,
        canvasWidth: canvas.getBoundingClientRect().width,
    };
}

function tlResizeMouseDown(e, lightId, idx) {
    e.preventDefault();
    e.stopPropagation();
    const canvas = document.getElementById(`light${lightId}-tl-canvas`);
    if (!canvas) return;
    const block = _tlBlocks[lightId]?.[idx];
    if (!block) return;
    _tlDrag = {
        type:        'resize',
        lightId, idx,
        startX:      e.clientX,
        origStart:   block.start ?? 0,
        origDur:     block.duration,
        totalDur:    _tlDuration[lightId] || 30,
        canvasWidth: canvas.getBoundingClientRect().width,
    };
}

function tlResizeLeftMouseDown(e, lightId, idx) {
    e.preventDefault();
    e.stopPropagation();
    const canvas = document.getElementById(`light${lightId}-tl-canvas`);
    if (!canvas) return;
    const block = _tlBlocks[lightId]?.[idx];
    if (!block) return;
    _tlDrag = {
        type:        'resize-left',
        lightId, idx,
        startX:      e.clientX,
        origStart:   block.start ?? 0,
        origDur:     block.duration,
        totalDur:    _tlDuration[lightId] || 30,
        canvasWidth: canvas.getBoundingClientRect().width,
    };
}

document.addEventListener('mousemove', e => {
    if (!_tlDrag) return;
    const { type, lightId, idx, startX, origStart, origDur, totalDur, canvasWidth } = _tlDrag;
    const dSec = (e.clientX - startX) / canvasWidth * totalDur;
    const block = _tlBlocks[lightId]?.[idx];
    if (!block) return;

    const snapThresh = 10 / canvasWidth * totalDur;
    const snaps      = _tlSnapTargets(lightId, idx);

    if (type === 'move') {
        let s = Math.max(0, Math.min(totalDur - origDur, origStart + dSec));
        // Snap whichever edge is closer to a candidate
        const sl = _tlSnap(snaps, s,          snapThresh);
        const sr = _tlSnap(snaps, s + origDur, snapThresh);
        if (sl !== s)              s = Math.max(0, sl);
        else if (sr !== s + origDur) s = Math.max(0, sr - origDur);
        block.start = Math.round(s * 100) / 100;
    } else if (type === 'resize') {
        // Right edge moves; left edge (block.start) is fixed
        let right = origStart + origDur + dSec;
        right = _tlSnap(snaps, right, snapThresh);
        block.duration = Math.max(0.1, Math.round((right - origStart) * 100) / 100);
    } else if (type === 'resize-left') {
        // Left edge moves; right edge (origStart + origDur) is fixed
        let s = origStart + dSec;
        s = _tlSnap(snaps, s, snapThresh);
        s = Math.max(0, Math.min(origStart + origDur - 0.1, s));
        block.start    = Math.round(s * 100) / 100;
        block.duration = Math.round((origStart + origDur - s) * 100) / 100;
    }
    tlRenderCanvas(lightId);
});

document.addEventListener('mouseup', () => {
    if (!_tlDrag) return;
    const { lightId } = _tlDrag;
    _tlDrag = null;
    _tlAutoSave(lightId);
});

function tlRenderEditor(lightId) {
    _tlInit(lightId);
    const editor = document.getElementById(`light${lightId}-tl-editor`);
    if (!editor) return;
    const sel = _tlSelected[lightId];
    const blocks = _tlBlocks[lightId];
    if (sel === null || !blocks[sel]) {
        editor.innerHTML = '';
        return;
    }
    const b        = blocks[sel];
    const prevBlock = sel > 0 ? blocks[sel - 1] : null;
    const nextBlock = sel < blocks.length - 1 ? blocks[sel + 1] : null;
    const hasLeftGap = !prevBlock ||
        ((b.start ?? 0) - ((prevBlock.start ?? 0) + prevBlock.duration)) > 0.12;
    const tr      = b.transition || { duration: 0, color: false, brightness: false };
    const maxHalf = (b.duration / 2).toFixed(1);

    editor.innerHTML = `
        <div class="tl-edit-row">
            <label>Color <input type="color" value="${b.color}" onchange="tlBlockProp(${lightId},${sel},'color',this.value)"></label>
            <label>Bri <input type="range" min="0" max="100" value="${b.brightness}" style="width:80px"
                oninput="tlBlockProp(${lightId},${sel},'brightness',+this.value);this.nextSibling.textContent=this.value+'%'"><span>${b.brightness}%</span></label>
            <label>Dur(s) <input type="number" min="0.1" step="0.1" value="${b.duration}" style="width:60px"
                onchange="tlBlockProp(${lightId},${sel},'duration',+this.value)"></label>
            <button class="btn btn-sm btn-secondary" onclick="tlCopyBlock(${lightId},${sel})">Copy</button>
            <button class="btn btn-sm btn-danger" onclick="tlDeleteBlock(${lightId},${sel})">Delete</button>
        </div>${hasLeftGap ? `
        <div class="tl-edit-row">
            <label style="display:flex;align-items:center;gap:4px">
                <input type="checkbox" ${(b.fadeIn||0)>0?'checked':''}
                    onchange="tlBlockProp(${lightId},${sel},'fadeIn',this.checked?0.5:0)"> Fade in
            </label>
            ${(b.fadeIn||0)>0 ? `<label>Dur(s) <input type="number" min="0.1" max="${maxHalf}" step="0.1"
                value="${(b.fadeIn).toFixed(1)}" style="width:55px"
                onchange="tlBlockProp(${lightId},${sel},'fadeIn',Math.min(+this.value,${maxHalf}))"></label>` : ''}
        </div>` : ''}${nextBlock ? `
        <div class="tl-edit-row tl-tr-row">
            <span class="tl-tr-label">&#8594; Transition:</span>
            <label style="display:flex;align-items:center;gap:3px">
                <input type="checkbox" ${tr.color?'checked':''}
                    onchange="tlTrBlockProp(${lightId},${sel},'color',this.checked)"> Color
            </label>
            <label style="display:flex;align-items:center;gap:3px">
                <input type="checkbox" ${tr.brightness?'checked':''}
                    onchange="tlTrBlockProp(${lightId},${sel},'brightness',this.checked)"> Bri
            </label>
            ${(tr.color||tr.brightness) ? `<label>Dur(s) <input type="number" min="0.1" max="${maxHalf}" step="0.1"
                value="${(tr.duration||0.5).toFixed(1)}" style="width:55px"
                onchange="tlTrBlockProp(${lightId},${sel},'duration',Math.min(+this.value,${maxHalf}))"></label>` : ''}
        </div>` : ''}`;
}

function tlBlockProp(lightId, idx, key, val) {
    _tlInit(lightId);
    if (_tlBlocks[lightId][idx]) {
        _tlBlocks[lightId][idx][key] = val;
        tlRenderCanvas(lightId);
        _tlAutoSave(lightId);
    }
}

function tlTrBlockProp(lightId, idx, key, val) {
    _tlInit(lightId);
    const block = _tlBlocks[lightId]?.[idx];
    if (!block) return;
    if (!block.transition) block.transition = { duration: 0.5, color: false, brightness: false };
    block.transition[key] = val;
    if (!block.transition.color && !block.transition.brightness) block.transition.duration = 0;
    else if (block.transition.duration === 0) block.transition.duration = 0.5;
    tlRenderCanvas(lightId);
    _tlAutoSave(lightId);
}

function tlSelectBlock(lightId, idx) {
    _tlSelected[lightId] = (_tlSelected[lightId] === idx) ? null : idx;
    tlRenderCanvas(lightId);
}

function tlAddBlock(lightId) {
    _tlInit(lightId);
    const blocks   = _tlBlocks[lightId];
    const last     = blocks[blocks.length - 1];
    const newStart = last ? (last.start ?? 0) + last.duration : 0;
    blocks.push({
        color: last?.color ?? '#0088ff', brightness: last?.brightness ?? 100,
        duration: 2.0, start: newStart,
        fadeIn: 0, transition: { duration: 0, color: false, brightness: false },
    });
    _tlSelected[lightId] = blocks.length - 1;
    tlRenderCanvas(lightId);
    _tlAutoSave(lightId);
}

function tlDeleteBlock(lightId, idx) {
    _tlInit(lightId);
    _tlBlocks[lightId].splice(idx, 1);
    _tlSelected[lightId] = null;
    tlRenderCanvas(lightId);
    _tlAutoSave(lightId);
}

// ── Copy / Paste ──────────────────────────────────────────────────────────────

function tlCopyBlock(lightId, idx) {
    const b = _tlBlocks[lightId]?.[idx];
    if (!b) return;
    _blockClipboard = { type: 'light', block: { ...b } };
    showNotification('Block copied', 'info');
}

function tlPasteBlock(lightId) {
    if (!_blockClipboard) { showNotification('Nothing to paste', 'info'); return; }
    _tlInit(lightId);
    const blocks = _tlBlocks[lightId];
    const last = blocks[blocks.length - 1];
    const newStart = last ? Math.round(((last.start ?? 0) + last.duration) * 100) / 100 : 0;
    blocks.push({ ..._blockClipboard.block, start: newStart });
    if (blocks.length > 1) _tlTrUI[lightId].push({ mode: 'cut', duration: 1.0 });
    _tlSelected[lightId] = blocks.length - 1;
    tlRenderCanvas(lightId);
    _tlAutoSave(lightId);
    showNotification('Block pasted', 'success');
}

function mtCopyBlock() {
    const sel = _mt.selected;
    if (!sel) return;
    if (sel.track === 'light') {
        const b = _tlBlocks[sel.lightId]?.[sel.blockIdx];
        if (!b) return;
        _blockClipboard = { type: 'light', block: { ...b } };
    } else {
        const b = _mt.tracks[sel.track]?.[sel.idx];
        if (!b) return;
        _blockClipboard = { type: sel.track, block: { ...b } };
    }
    showNotification('Block copied', 'info');
}

function mtPasteBlock() {
    const sel = _mt.selected;
    if (!_blockClipboard) { showNotification('Nothing to paste', 'info'); return; }
    if (sel?.track === 'light') {
        if (_blockClipboard.type !== 'light') { showNotification('Incompatible block type', 'info'); return; }
        tlPasteBlock(sel.lightId);
        return;
    }
    const track = sel?.track;
    if (!track || !_mt.tracks[track]) { showNotification('Select a track block first', 'info'); return; }
    if (_blockClipboard.type !== track) { showNotification('Incompatible block type', 'info'); return; }
    const blocks = _mt.tracks[track];
    const last = blocks[blocks.length - 1];
    const newStart = last ? Math.round((last.start + last.duration) * 10) / 10 : 0;
    blocks.push({ ..._blockClipboard.block, start: newStart });
    _mt.selected = { track, idx: blocks.length - 1 };
    mtRender();
    showNotification('Block pasted', 'success');
}

// Ctrl+C / Ctrl+V — skip when typing in an input
document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (e.ctrlKey && e.key === 'c') {
        e.preventDefault();
        if (_mt.selected) { mtCopyBlock(); return; }
        for (const [lid, idx] of Object.entries(_tlSelected)) {
            if (idx !== null && idx !== undefined) { tlCopyBlock(parseInt(lid), idx); return; }
        }
    }
    if (e.ctrlKey && e.key === 'v') {
        e.preventDefault();
        if (_mt.selected) { mtPasteBlock(); return; }
        for (const [lid, idx] of Object.entries(_tlSelected)) {
            if (idx !== null && idx !== undefined) { tlPasteBlock(parseInt(lid)); return; }
        }
    }
});

// ── Play button state ─────────────────────────────────────────────────────────

const _tlPlayPolling = {};  // lightId → intervalId

function _tlSetPlaying(lightId, playing) {
    const btn = document.getElementById(`light${lightId}-tl-play`);
    if (!btn) return;
    if (playing) {
        btn.innerHTML = '⏹ Stop';
        btn.classList.add('tl-playing');
        btn.onclick = () => tlStop(lightId);
    } else {
        btn.innerHTML = '&#9654; Play';
        btn.classList.remove('tl-playing');
        btn.onclick = () => tlPlay(lightId);
    }
}

function _tlStartPolling(lightId) {
    clearInterval(_tlPlayPolling[lightId]);
    _tlPlayPolling[lightId] = setInterval(async () => {
        const data = await fetch(`/api/lights/${lightId}/timeline`).then(r => r.json()).catch(() => null);
        if (!data?.playing) {
            clearInterval(_tlPlayPolling[lightId]);
            _tlSetPlaying(lightId, false);
        }
    }, 600);
}

let _mtPlayPolling = null;
let _mtPlayStart   = null;
let _mtPlayheadRaf = null;

function _mtStartPlayhead(dur) {
    _mtPlayStart = Date.now();
    const ph = document.getElementById('mt-playhead');
    if (ph) ph.classList.add('visible');
    const tick = () => {
        const ph2 = document.getElementById('mt-playhead');
        if (!ph2) return;
        const elapsed = (Date.now() - _mtPlayStart) / 1000;
        const tl = document.getElementById('mt-timeline');
        if (tl) {
            const canvasW = tl.offsetWidth - 110;
            ph2.style.left = (110 + Math.min(elapsed / dur, 1) * canvasW) + 'px';
        }
        if (elapsed < dur) {
            _mtPlayheadRaf = requestAnimationFrame(tick);
        } else {
            ph2.classList.remove('visible');
            _mtPlayheadRaf = null;
        }
    };
    _mtPlayheadRaf = requestAnimationFrame(tick);
}

function _mtStopPlayhead() {
    if (_mtPlayheadRaf) { cancelAnimationFrame(_mtPlayheadRaf); _mtPlayheadRaf = null; }
    const ph = document.getElementById('mt-playhead');
    if (ph) ph.classList.remove('visible');
}

function _mtSetPlaying(playing) {
    const btn = document.getElementById('mt-play-btn');
    if (!btn) return;
    if (playing) {
        btn.innerHTML = '⏹ Stop';
        btn.classList.add('tl-playing');
        btn.onclick = () => mtStop();
        _mtStartPlayhead(_mt.duration);
    } else {
        btn.innerHTML = '&#9654; Play All';
        btn.classList.remove('tl-playing');
        btn.onclick = () => mtPlay();
        _mtStopPlayhead();
    }
}

function _mtStartPolling() {
    clearInterval(_mtPlayPolling);
    _mtPlayPolling = setInterval(async () => {
        const data = await fetch('/api/master-timeline').then(r => r.json()).catch(() => null);
        if (!data?.playing) {
            clearInterval(_mtPlayPolling);
            _mtSetPlaying(false);
        } else if (!_mtPlayheadRaf) {
            // Loop restarted on server — restart playhead animation
            _mtStartPlayhead(_mt.duration);
        }
    }, 600);
}

// ── Auto-save / load per-light timeline to localStorage ──────────────────────

function _tlAutoSave(lightId) {
    try {
        localStorage.setItem(`tl_${lightId}`, JSON.stringify({
            blocks:      _tlBlocks[lightId]   || [],
            transitions: _tlTrUI[lightId]     || [],
            loop:        _tlLoop[lightId]      || false,
            duration:    _tlDuration[lightId]  || 30,
        }));
    } catch (e) {}
}

function _tlAutoLoad(lightId) {
    try {
        const raw = localStorage.getItem(`tl_${lightId}`);
        if (!raw) return;
        const data = JSON.parse(raw);
        _tlInit(lightId);
        _tlBlocks[lightId]   = data.blocks      || [];
        _tlTrUI[lightId]     = (data.transitions || []).map(t => t.mode !== undefined ? t : _tlTrFromServer(t));
        _tlLoop[lightId]     = data.loop         || false;
        _tlDuration[lightId] = data.duration     || 30;
        _tlSelected[lightId] = null;
        const loopBtn = document.getElementById(`light${lightId}-tl-loop`);
        if (loopBtn) loopBtn.classList.toggle('tl-loop-on', _tlLoop[lightId]);
    } catch (e) {}
}

async function tlPlay(lightId) {
    _tlInit(lightId);
    const blocks = [..._tlBlocks[lightId]].sort((a, b) => (a.start ?? 0) - (b.start ?? 0));
    const transitions = _tlTrUI[lightId].map(tr => _tlTrToServer(tr.mode, tr.duration));
    await fetch(`/api/lights/${lightId}/timeline`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ blocks, transitions, loop: _tlLoop[lightId] })
    });
    const res = await fetch(`/api/lights/${lightId}/timeline/play`, { method: 'POST' }).then(r => r.json()).catch(() => null);
    if (res?.error) { showNotification(res.error, 'error'); return; }
    showNotification(`Playing ${blocks.length} blocks`, 'info');
    _tlSetPlaying(lightId, true);
    _tlStartPolling(lightId);
}

async function tlStop(lightId) {
    clearInterval(_tlPlayPolling[lightId]);
    await fetch(`/api/lights/${lightId}/timeline/stop`, { method: 'POST' });
    _tlSetPlaying(lightId, false);
}

async function stopAllLights() {
    for (const id of Object.keys(_tlPlayPolling)) {
        clearInterval(_tlPlayPolling[id]);
        _tlSetPlaying(id, false);
    }
    const ids = (_lightsCache || []).map(l => l.id);
    await Promise.all([
        ...ids.map(id => fetch(`/api/lights/${id}/timeline/stop`, { method: 'POST' }).catch(() => {})),
        ...ids.map(id => fetch(`/api/lights/${id}/off`,           { method: 'POST' }).catch(() => {})),
    ]);
    showNotification('All lights stopped', 'info');
}

function tlToggleLoop(lightId) {
    _tlInit(lightId);
    _tlLoop[lightId] = !_tlLoop[lightId];
    const btn = document.getElementById(`light${lightId}-tl-loop`);
    if (btn) btn.classList.toggle('tl-loop-on', _tlLoop[lightId]);
    _tlAutoSave(lightId);
}

async function tlSave(lightId) {
    _tlInit(lightId);
    const data = {
        blocks:      _tlBlocks[lightId],
        transitions: _tlTrUI[lightId],
        loop:        _tlLoop[lightId],
        duration:    _tlDuration[lightId] || 30,
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `light${lightId}_timeline.json`;
    a.click();
}

async function tlLoad(lightId, input) {
    const file = input.files[0];
    if (!file) return;
    const text = await file.text();
    let data;
    try { data = JSON.parse(text); } catch { showNotification('Invalid JSON', 'error'); return; }
    _tlInit(lightId);
    _tlBlocks[lightId]   = data.blocks      ?? [];
    _tlTrUI[lightId]     = (data.transitions ?? []).map(t => t.mode !== undefined ? t : _tlTrFromServer(t));
    _tlLoop[lightId]     = data.loop        ?? false;
    _tlDuration[lightId] = data.duration    ?? 30;
    _tlSelected[lightId] = null;
    const loopBtn = document.getElementById(`light${lightId}-tl-loop`);
    if (loopBtn) loopBtn.classList.toggle('tl-loop-on', _tlLoop[lightId]);
    tlRenderCanvas(lightId);
    _tlAutoSave(lightId);
    showNotification(`Loaded ${_tlBlocks[lightId].length} blocks`, 'success');
    input.value = '';
}

// ─── Master Timeline ───────────────────────────────────────────────────────────

const _mt = {
    tracks:      { rail: [], cam1: [], cam2: [] },
    lightTracks: {},   // str(lightId) → [{start,duration,color,brightness,transition:{mode,duration}}]
    duration:    60,
    loop:        false,
    selected:    null, // {track, idx}
};

const MT_TRACK_DEFS = [
    { key: 'rail', label: 'Rail',     color: '#4caf50' },
    { key: 'cam1', label: 'Camera 1', color: '#0099ff' },
    { key: 'cam2', label: 'Camera 2', color: '#ff9900' },
];

function _mtPx(sec) {
    const total = parseFloat(document.getElementById('mt-duration')?.value || _mt.duration) || 60;
    return (sec / total * 100).toFixed(3) + '%';
}

function mtRender() {
    const tl = document.getElementById('mt-timeline');
    if (!tl) return;
    const total = parseFloat(document.getElementById('mt-duration')?.value || _mt.duration) || 60;

    // Time ruler
    const rulerTicks = Math.min(20, Math.floor(total));
    const tickStep   = Math.ceil(total / rulerTicks);
    let ruler = '<div class="mt-ruler">';
    for (let t = 0; t <= total; t += tickStep) {
        ruler += `<span class="mt-tick" style="width:${(tickStep/total*100).toFixed(2)}%">${t}s</span>`;
    }
    ruler += '</div>';

    // ── Non-light tracks (rail, cam1, cam2) ────────────────────────────────
    const nonLightRows = MT_TRACK_DEFS.map(td => {
        const blocks = _mt.tracks[td.key] || [];
        const blocksHtml = blocks.map((b, i) => {
            const left  = (b.start    / total * 100).toFixed(3);
            const width = (b.duration / total * 100).toFixed(3);
            const sel   = _mt.selected?.track === td.key && _mt.selected?.idx === i;
            const label = (b.action || b.direction || '?') + ` ${b.duration}s`;
            return `<div class="mt-block${sel ? ' mt-block-sel' : ''}"
                style="left:${left}%;width:${width}%;background:${td.color || '#666'}"
                onmousedown="mtBlockMouseDown(event,'${td.key}',${i})"
                onclick="mtSelectBlock('${td.key}',${i})" title="${label}">
                <div class="mt-resize-handle left" onmousedown="mtResizeLeftMouseDown(event,'${td.key}',${i})"></div>
                <span class="mt-block-label">${label}</span>
                <div class="mt-resize-handle" onmousedown="mtResizeMouseDown(event,'${td.key}',${i})"></div>
            </div>`;
        }).join('');
        return `<div class="mt-row">
            <div class="mt-row-label">${td.label}</div>
            <div class="mt-row-canvas" id="mt-canvas-${td.key}">
                ${blocksHtml}
                <button class="mt-add-btn" onclick="mtAddBlock('${td.key}')">+</button>
            </div>
        </div>`;
    }).join('');

    // ── Light tracks — derived from per-light _tlBlocks ────────────────────
    const lightRows = _lightsCache.map(light => {
        const lid  = light.id;
        const segs = _tlAbsoluteSegments(lid);
        const segsHtml = segs.map(s => {
            const left  = (s.start    / total * 100).toFixed(3);
            const width = Math.max(s.duration / total * 100, 0.4).toFixed(3);
            if (s.type === 'block') {
                const isSel = _mt.selected?.track === 'light' && _mt.selected?.lightId === lid && _mt.selected?.blockIdx === s.blockIdx;
                return `<div class="mt-block${isSel ? ' mt-block-sel' : ''}"
                    style="left:${left}%;width:${width}%;background:${s.color};border-radius:3px"
                    onmousedown="mtLightBlockMouseDown(event,${lid},${s.blockIdx})"
                    onclick="mtSelectLightBlock(${lid},${s.blockIdx})"
                    title="${s.duration.toFixed(1)}s @ ${(s.start??0).toFixed(1)}s">
                    <div class="mt-resize-handle left" onmousedown="mtLightResizeLeftMouseDown(event,${lid},${s.blockIdx})"></div>
                    <span class="mt-block-label">${s.duration.toFixed(1)}s</span>
                    <div class="mt-resize-handle" onmousedown="mtLightResizeMouseDown(event,${lid},${s.blockIdx})"></div>
                </div>`;
            } else {
                return `<div class="mt-tl-tr"
                    style="left:${left}%;width:${width}%;background:linear-gradient(90deg,${s.fromColor},${s.toColor})"
                    title="${s.mode}"></div>`;
            }
        }).join('');
        return `<div class="mt-row mt-row-light">
            <div class="mt-row-label" style="color:#e65100">${light.name || 'Light ' + lid}</div>
            <div class="mt-row-canvas" id="mt-canvas-light-${lid}">
                ${segsHtml}
                <button class="mt-add-btn" onclick="tlAddBlock(${lid})" title="Add light block">+</button>
            </div>
        </div>`;
    }).join('');

    tl.innerHTML = ruler + nonLightRows + lightRows;
    // Keep playhead alive across re-renders
    if (!document.getElementById('mt-playhead')) {
        const ph = document.createElement('div');
        ph.id = 'mt-playhead';
        ph.className = 'mt-playhead';
        tl.appendChild(ph);
    }
    mtRenderEditor();
}

function mtSelectBlock(track, idx) {
    if (_mt.selected?.track === track && _mt.selected?.idx === idx) {
        _mt.selected = null;
    } else {
        _mt.selected = { track, idx };
    }
    mtRender();
}

function mtSelectLightBlock(lightId, blockIdx) {
    if (_mt.selected?.track === 'light' && _mt.selected?.lightId === lightId && _mt.selected?.blockIdx === blockIdx) {
        _mt.selected = null;
    } else {
        _mt.selected = { track: 'light', lightId, blockIdx };
    }
    mtRender();
}

function mtLightBlockProp(lightId, blockIdx, key, val) {
    if (_tlBlocks[lightId]?.[blockIdx] !== undefined) {
        _tlBlocks[lightId][blockIdx][key] = val;
        _tlAutoSave(lightId);
        tlRenderCanvas(lightId); // re-renders per-light canvas AND master timeline
    }
}

function mtLightBlockTrProp(lightId, blockIdx, key, val) {
    // Simple top-level block prop (fadeIn) — routes through mtLightBlockProp
    mtLightBlockProp(lightId, blockIdx, key, val);
    mtRenderEditor(); // re-render editor to show/hide duration input
}

function mtLightDeleteBlock(lightId, blockIdx) {
    _mt.selected = null;
    tlDeleteBlock(lightId, blockIdx);
}

function mtRenderEditor() {
    const editor = document.getElementById('mt-editor');
    if (!editor) return;
    const sel = _mt.selected;
    if (!sel) { editor.innerHTML = ''; return; }

    // ── Light block from _tlBlocks (selected via master timeline row) ──────────
    if (sel.track === 'light') {
        const { lightId, blockIdx } = sel;
        const b = _tlBlocks[lightId]?.[blockIdx];
        if (!b) { editor.innerHTML = ''; return; }
        const blocks    = _tlBlocks[lightId] || [];
        const prevBlock = blockIdx > 0 ? blocks[blockIdx - 1] : null;
        const nextBlock = blockIdx < blocks.length - 1 ? blocks[blockIdx + 1] : null;
        const hasLeftGap = !prevBlock ||
            ((b.start ?? 0) - ((prevBlock.start ?? 0) + prevBlock.duration)) > 0.12;
        const tr      = b.transition || { duration: 0, color: false, brightness: false };
        const maxHalf = (b.duration / 2).toFixed(1);
        editor.innerHTML = `
            <div class="tl-edit-row">
                <label>Color <input type="color" value="${b.color}" onchange="mtLightBlockProp(${lightId},${blockIdx},'color',this.value)"></label>
                <label>Bri <input type="range" min="0" max="100" value="${b.brightness}" style="width:80px"
                    oninput="mtLightBlockProp(${lightId},${blockIdx},'brightness',+this.value);this.nextSibling.textContent=this.value+'%'"><span>${b.brightness}%</span></label>
                <label>Start(s) <input type="number" min="0" step="0.1" value="${(b.start??0).toFixed(1)}" style="width:60px"
                    onchange="mtLightBlockProp(${lightId},${blockIdx},'start',+this.value)"></label>
                <label>Dur(s) <input type="number" min="0.1" step="0.1" value="${b.duration}" style="width:60px"
                    onchange="mtLightBlockProp(${lightId},${blockIdx},'duration',+this.value)"></label>
                <button class="btn btn-sm btn-secondary" onclick="mtCopyBlock()">Copy</button>
                <button class="btn btn-sm btn-danger" onclick="mtLightDeleteBlock(${lightId},${blockIdx})">Delete</button>
            </div>${hasLeftGap ? `
            <div class="tl-edit-row">
                <label style="display:flex;align-items:center;gap:4px">
                    <input type="checkbox" ${(b.fadeIn||0)>0?'checked':''}
                        onchange="mtLightBlockTrProp(${lightId},${blockIdx},'fadeIn',this.checked?0.5:0)"> Fade in
                </label>
                ${(b.fadeIn||0)>0 ? `<label>Dur(s) <input type="number" min="0.1" max="${maxHalf}" step="0.1"
                    value="${(b.fadeIn).toFixed(1)}" style="width:55px"
                    onchange="mtLightBlockTrProp(${lightId},${blockIdx},'fadeIn',Math.min(+this.value,${maxHalf}))"></label>` : ''}
            </div>` : ''}${nextBlock ? `
            <div class="tl-edit-row tl-tr-row">
                <span class="tl-tr-label">&#8594; Transition:</span>
                <label style="display:flex;align-items:center;gap:3px">
                    <input type="checkbox" ${tr.color?'checked':''}
                        onchange="tlTrBlockProp(${lightId},${blockIdx},'color',this.checked)"> Color
                </label>
                <label style="display:flex;align-items:center;gap:3px">
                    <input type="checkbox" ${tr.brightness?'checked':''}
                        onchange="tlTrBlockProp(${lightId},${blockIdx},'brightness',this.checked)"> Bri
                </label>
                ${(tr.color||tr.brightness) ? `<label>Dur(s) <input type="number" min="0.1" max="${maxHalf}" step="0.1"
                    value="${(tr.duration||0.5).toFixed(1)}" style="width:55px"
                    onchange="tlTrBlockProp(${lightId},${blockIdx},'duration',Math.min(+this.value,${maxHalf}))"></label>` : ''}
            </div>` : ''}`;
        return;
    }

    // ── Non-light tracks (rail, cam1, cam2) ────────────────────────────────────
    const blocks   = _mt.tracks[sel.track];
    const b        = blocks?.[sel.idx];
    if (!b) { editor.innerHTML = ''; return; }

    let html = `<div class="tl-edit-row">
        <label>Start(s) <input type="number" min="0" step="0.1" value="${b.start}" style="width:65px"
            onchange="mtBlockProp('${sel.track}',${sel.idx},'start',+this.value)"></label>
        <label>Dur(s) <input type="number" min="0.1" step="0.1" value="${b.duration}" style="width:65px"
            onchange="mtBlockProp('${sel.track}',${sel.idx},'duration',+this.value)"></label>`;

    if (sel.track === 'rail') {
        const dirOpts = ['forward','backward'].map(d =>
            `<option value="${d}"${b.direction===d?' selected':''}>${d.charAt(0).toUpperCase()+d.slice(1)}</option>`
        ).join('');
        html += `
        <label>Direction <select onchange="mtBlockProp('${sel.track}',${sel.idx},'direction',this.value)">${dirOpts}</select></label>
        <label>Speed(μs) <input type="number" min="10" max="2000" value="${b.speed||91}" style="width:65px"
            onchange="mtBlockProp('${sel.track}',${sel.idx},'speed',+this.value)"></label>`;
    } else if (sel.track === 'cam1' || sel.track === 'cam2') {
        const actionOpts = [
            { v: 'video_start', l: 'Start Recording' },
            { v: 'video_stop',  l: 'Stop Recording'  },
            { v: 'photo',       l: 'Take Photo'       },
        ].map(({ v, l }) =>
            `<option value="${v}"${b.action===v?' selected':''}>${l}</option>`
        ).join('');
        html += `
        <label>Action <select onchange="mtBlockProp('${sel.track}',${sel.idx},'action',this.value)">${actionOpts}</select></label>`;
    }

    html += `<button class="btn btn-sm btn-secondary" onclick="mtCopyBlock()">Copy</button>
             <button class="btn btn-sm btn-secondary" onclick="mtPasteBlock()">Paste</button>
             <button class="btn btn-sm btn-danger" onclick="mtDeleteBlock('${sel.track}',${sel.idx})">Delete</button></div>`;
    editor.innerHTML = html;
}

function mtBlockProp(track, idx, key, val) {
    const blocks = _mt.tracks[track];
    if (blocks?.[idx] !== undefined) { blocks[idx][key] = val; mtRender(); }
}

function mtTrProp(track, idx, key, val) {
    const blocks = _mt.tracks[track];
    if (blocks?.[idx]) {
        if (!blocks[idx].transition) blocks[idx].transition = { mode: 'cut', duration: 1.0 };
        blocks[idx].transition[key] = val;
        mtRender();
    }
}

function mtAddBlock(track, lightId = null) {
    const isLight = !!lightId;
    if (isLight) {
        if (!_mt.lightTracks[lightId]) _mt.lightTracks[lightId] = [];
        const blocks = _mt.lightTracks[lightId];
        const last   = blocks[blocks.length - 1];
        const start  = last ? last.start + last.duration : 0;
        blocks.push({ start, duration: 5.0, color: last?.color || '#ff0000', brightness: last?.brightness ?? 100,
                      transition: { mode: 'cut', duration: 1.0 } });
    } else if (track === 'rail') {
        const blocks = _mt.tracks.rail;
        const last   = blocks[blocks.length - 1];
        const start  = last ? last.start + last.duration : 0;
        blocks.push({ start, duration: 10.0, direction: 'forward', speed: 91 });
    } else if (track === 'cam1' || track === 'cam2') {
        const blocks = _mt.tracks[track];
        const last   = blocks[blocks.length - 1];
        const start  = last ? last.start + last.duration : 0;
        blocks.push({ start, duration: 0.5, action: 'video_start' });
    }
    _mt.selected = null;
    mtRender();
}

function mtDeleteBlock(track, idx) {
    const blocks = _mt.tracks[track];
    if (blocks) { blocks.splice(idx, 1); _mt.selected = null; mtRender(); }
}

// ── Master timeline block drag ────────────────────────────────────────────────

let _mtDrag = null;

function mtBlockMouseDown(e, track, idx) {
    if (e.target.classList.contains('mt-resize-handle')) return;
    e.preventDefault();
    const canvas = document.getElementById(`mt-canvas-${track}`);
    if (!canvas) return;
    const block = _mt.tracks[track]?.[idx];
    if (!block) return;
    _mtDrag = {
        type:        'move',
        track, idx,
        startX:      e.clientX,
        origVal:     block.start,
        totalDur:    _mt.duration,
        canvasWidth: canvas.getBoundingClientRect().width,
    };
    _mt.selected = { track, idx };
}

function mtResizeMouseDown(e, track, idx) {
    e.preventDefault();
    e.stopPropagation();
    const canvas = document.getElementById(`mt-canvas-${track}`);
    if (!canvas) return;
    const block = _mt.tracks[track]?.[idx];
    if (!block) return;
    _mtDrag = {
        type:        'resize',
        track, idx,
        startX:      e.clientX,
        origVal:     block.duration,
        totalDur:    _mt.duration,
        canvasWidth: canvas.getBoundingClientRect().width,
    };
}

function mtResizeLeftMouseDown(e, track, idx) {
    e.preventDefault();
    e.stopPropagation();
    const canvas = document.getElementById(`mt-canvas-${track}`);
    if (!canvas) return;
    const block = _mt.tracks[track]?.[idx];
    if (!block) return;
    _mtDrag = {
        type:          'resize-left',
        track, idx,
        startX:        e.clientX,
        origStart:     block.start,
        origRightEdge: block.start + block.duration,
        totalDur:      _mt.duration,
        canvasWidth:   canvas.getBoundingClientRect().width,
    };
}

function mtLightResizeLeftMouseDown(e, lightId, blockIdx) {
    e.preventDefault();
    e.stopPropagation();
    const canvas = document.getElementById(`mt-canvas-light-${lightId}`);
    if (!canvas) return;
    const block = _tlBlocks[lightId]?.[blockIdx];
    if (!block) return;
    _mtDrag = {
        type:          'light-resize-left',
        lightId, blockIdx,
        startX:        e.clientX,
        origStart:     block.start ?? 0,
        origRightEdge: (block.start ?? 0) + block.duration,
        totalDur:      _mt.duration,
        canvasWidth:   canvas.getBoundingClientRect().width,
    };
}

function mtLightBlockMouseDown(e, lightId, blockIdx) {
    if (e.target.classList.contains('mt-resize-handle')) return;
    e.preventDefault();
    const canvas = document.getElementById(`mt-canvas-light-${lightId}`);
    if (!canvas) return;
    const block = _tlBlocks[lightId]?.[blockIdx];
    if (!block) return;
    _mtDrag = {
        type:        'light-move',
        lightId, blockIdx,
        startX:      e.clientX,
        origVal:     block.start ?? 0,
        totalDur:    _mt.duration,
        canvasWidth: canvas.getBoundingClientRect().width,
    };
}

function mtLightResizeMouseDown(e, lightId, blockIdx) {
    e.preventDefault();
    e.stopPropagation();
    const canvas = document.getElementById(`mt-canvas-light-${lightId}`);
    if (!canvas) return;
    const block = _tlBlocks[lightId]?.[blockIdx];
    if (!block) return;
    _mtDrag = {
        type:        'light-resize',
        lightId, blockIdx,
        startX:      e.clientX,
        origVal:     block.duration,
        totalDur:    _mt.duration,
        canvasWidth: canvas.getBoundingClientRect().width,
    };
}

document.addEventListener('mousemove', e => {
    if (!_mtDrag) return;
    const { type, track, idx, lightId, blockIdx, startX, origVal, origStart, origRightEdge, totalDur, canvasWidth } = _mtDrag;
    const dSec = (e.clientX - startX) / canvasWidth * totalDur;
    if (type === 'move' || type === 'resize') {
        const block = _mt.tracks[track]?.[idx];
        if (!block) return;
        if (type === 'move') {
            block.start = Math.max(0, Math.min(totalDur - block.duration, origVal + dSec));
            block.start = Math.round(block.start * 10) / 10;
        } else {
            block.duration = Math.max(0.1, Math.round((origVal + dSec) * 10) / 10);
        }
        mtRender();
    } else if (type === 'resize-left') {
        const block = _mt.tracks[track]?.[idx];
        if (!block) return;
        let s = Math.max(0, Math.min(origRightEdge - 0.1, origStart + dSec));
        s = Math.round(s * 10) / 10;
        block.start    = s;
        block.duration = Math.round((origRightEdge - s) * 10) / 10;
        mtRender();
    } else if (type === 'light-move' || type === 'light-resize') {
        const block = _tlBlocks[lightId]?.[blockIdx];
        if (!block) return;
        if (type === 'light-move') {
            block.start = Math.max(0, Math.min(totalDur - block.duration, origVal + dSec));
            block.start = Math.round(block.start * 10) / 10;
        } else {
            block.duration = Math.max(0.1, Math.round((origVal + dSec) * 10) / 10);
        }
        tlRenderCanvas(lightId);  // also calls mtRender() internally
    } else if (type === 'light-resize-left') {
        const block = _tlBlocks[lightId]?.[blockIdx];
        if (!block) return;
        let s = Math.max(0, Math.min(origRightEdge - 0.1, origStart + dSec));
        s = Math.round(s * 10) / 10;
        block.start    = s;
        block.duration = Math.round((origRightEdge - s) * 10) / 10;
        tlRenderCanvas(lightId);
    }
});

document.addEventListener('mouseup', e => {
    if (_mtDrag) {
        if (['light-move', 'light-resize', 'light-resize-left'].includes(_mtDrag.type)) {
            _tlAutoSave(_mtDrag.lightId);
        }
        _mtDrag = null;
    }
});

async function mtPlay() {
    const dur = parseFloat(document.getElementById('mt-duration')?.value || 60);
    _mt.duration = dur;

    // Build light_tracks from per-light _tlBlocks (what the user actually edited)
    const lightTracks = {};
    for (const light of _lightsCache) {
        const lid    = light.id;
        const blocks = (_tlBlocks[lid] || []).slice().sort((a, b) => (a.start ?? 0) - (b.start ?? 0));
        if (!blocks.length) continue;
        const trs = _tlTrUI[lid] || [];
        lightTracks[String(lid)] = blocks.map((b, i) => ({
            start:      b.start      ?? 0,
            duration:   b.duration,
            color:      b.color,
            brightness: b.brightness,
            transition: trs[i]       ?? { mode: 'cut', duration: 1.0 },
        }));
    }

    await fetch('/api/master-timeline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tracks: _mt.tracks, light_tracks: lightTracks,
                               duration: _mt.duration, loop: _mt.loop })
    });
    await fetch('/api/master-timeline/play', { method: 'POST' });
    showNotification('Master timeline playing', 'info');
    _mtSetPlaying(true);
    _mtStartPolling();
}

async function mtStop() {
    clearInterval(_mtPlayPolling);
    await fetch('/api/master-timeline/stop', { method: 'POST' });
    _mtSetPlaying(false);
}

function mtToggleLoop() {
    _mt.loop = !_mt.loop;
    const btn = document.getElementById('mt-loop-btn');
    if (btn) btn.classList.toggle('tl-loop-on', _mt.loop);
}

function projectSave() {
    // Snapshot all per-light timelines
    const lightTimelines = {};
    const knownIds = Object.keys(_tlBlocks);
    // Also include any lights loaded but not yet edited
    for (const lid of knownIds) {
        lightTimelines[lid] = {
            blocks:      _tlBlocks[lid]    ?? [],
            transitions: _tlTrUI[lid]      ?? [],
            loop:        _tlLoop[lid]      ?? false,
            duration:    _tlDuration[lid]  ?? 30,
        };
    }

    const project = {
        version:   1,
        name:      _projectName  || 'Untitled Project',
        notes:     _projectNotes || '',
        savedAt:   new Date().toISOString(),
        master: {
            tracks:      _mt.tracks,
            lightTracks: _mt.lightTracks,
            duration:    _mt.duration,
            loop:        _mt.loop,
        },
        lightTimelines,
    };

    const blob = new Blob([JSON.stringify(project, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    const safeName = (project.name).replace(/[^a-z0-9_\- ]/gi, '_').trim() || 'project';
    a.download = `${safeName}.json`;
    a.click();
}

async function projectLoad(input) {
    const file = input.files[0];
    if (!file) return;
    let project;
    try { project = JSON.parse(await file.text()); } catch { showNotification('Invalid JSON', 'error'); return; }

    // Restore project meta
    _projectName  = project.name  ?? '';
    _projectNotes = project.notes ?? '';
    const nameEl  = document.getElementById('mt-project-name');
    if (nameEl)  nameEl.value  = _projectName;
    const notesEl = document.getElementById('mt-notes');
    if (notesEl) notesEl.value = _projectNotes;

    // Restore master timeline
    const m = project.master ?? {};
    _mt.tracks      = m.tracks      ?? { rail: [], cam1: [], cam2: [] };
    _mt.lightTracks = m.lightTracks ?? {};
    _mt.duration    = m.duration    ?? 60;
    _mt.loop        = m.loop        ?? false;
    _mt.selected    = null;
    const durEl  = document.getElementById('mt-duration');
    if (durEl)  durEl.value = _mt.duration;
    const loopBtn = document.getElementById('mt-loop-btn');
    if (loopBtn) loopBtn.classList.toggle('tl-loop-on', _mt.loop);

    // Restore per-light timelines
    const lt = project.lightTimelines ?? {};
    for (const [lid, tl] of Object.entries(lt)) {
        const id = parseInt(lid);
        _tlInit(id);
        _tlBlocks[id]    = tl.blocks      ?? [];
        _tlTrUI[id]      = (tl.transitions ?? []).map(t => t.mode !== undefined ? t : _tlTrFromServer(t));
        _tlLoop[id]      = tl.loop        ?? false;
        _tlDuration[id]  = tl.duration    ?? 30;
        _tlSelected[id]  = null;
        const loopB = document.getElementById(`light${id}-tl-loop`);
        if (loopB) loopB.classList.toggle('tl-loop-on', _tlLoop[id]);
        tlRenderCanvas(id);
        _tlAutoSave(id);   // persist restored duration to localStorage
    }

    mtRender();
    const blockCount = Object.values(lt).reduce((s, t) => s + (t.blocks?.length ?? 0), 0);
    showNotification(`Loaded "${_projectName}" — ${blockCount} light block(s)`, 'success');
    input.value = '';
}

function mtAddLightTrack(lightId) {
    const lid = String(lightId);
    if (!_mt.lightTracks[lid]) { _mt.lightTracks[lid] = []; mtRender(); }
}

// ─── Init ─────────────────────────────────────────────────────────────────────

// ─── Instrument-feed "audible" dot ──────────────────────────────────────────
// Polls the live audio level and lights the green dot between the two previews
// when the instrument feed is audible (before and during recording).
let _audioDotTimer = null;
function startAudioDot() {
    const dot = document.getElementById('audio-dot');
    if (!dot) return;
    async function tick() {
        try {
            const r = await fetch('/api/audio/level').then(res => res.json());
            if (!r.monitoring && !r.recording) {
                dot.className = 'audio-dot offline';
                dot.style.transform = 'translate(-50%, -50%)';
                dot.title = 'Instrument feed: no input device';
                return;
            }
            dot.classList.remove('offline');
            dot.classList.toggle('present', !!r.present);
            // Breathe with the signal for a live "VU" feel (0.05 ≈ a strong note).
            const lvl = Math.max(0, Math.min(1, r.level / 0.05));
            dot.style.transform = `translate(-50%, -50%) scale(${(1 + lvl * 0.5).toFixed(2)})`;
            dot.title = `Instrument feed${r.recording ? ' — RECORDING' : ''}: ${r.dbfs} dBFS`;
        } catch (e) { /* keep last state on transient error */ }
    }
    if (_audioDotTimer) clearInterval(_audioDotTimer);
    _audioDotTimer = setInterval(tick, 180);
    tick();
}

document.addEventListener('DOMContentLoaded', async () => {
    startAudioDot();
    const [s1, s2, ard] = await Promise.all([
        apiCall('/1/status'),
        apiCall('/2/status'),
        apiCall('/arduino/connect', 'POST'),
    ]);
    if (s1) updateCamStatus(1, s1.connected, s1.ip, s1.type);
    if (s2) updateCamStatus(2, s2.connected, s2.ip, s2.type);
    if (ard) updateArduinoStatus(ard);
    _initGimbalUI();
    _syncTrackerLimits();
    await renderLights();
    // Auto-load per-light timelines from localStorage
    for (const light of _lightsCache) {
        _tlAutoLoad(light.id);
        tlRenderCanvas(light.id);
        _tlAutoSave(light.id);  // migrate: re-save so duration field is always present
    }
    // Turn off all lights on page load so every session starts with a clean state
    const lightsData = await fetch('/api/lights').then(r => r.json()).catch(() => []);
    await Promise.all(lightsData.map(l => fetch(`/api/lights/${l.id}/off`, { method: 'POST' })));
    mtRender();

    // Auto-bring-up: cameras can take a moment to be reachable after the page (or the
    // camera) boots. Retry the connection for both at 2s, then auto-start preview for
    // whichever came online at 5s.
    setTimeout(() => { retryConnection(1); retryConnection(2); }, 2000);
    setTimeout(() => {
        [1, 2].forEach(id => {
            if (camState[id].connected) startPreview(id);
        });
    }, 5000);
});
