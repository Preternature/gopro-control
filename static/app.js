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
    try {
        const opts = { method, headers: { 'Content-Type': 'application/json' } };
        if (data) opts.body = JSON.stringify(data);
        const res = await fetch(`/api${endpoint}`, opts);
        return await res.json();
    } catch (err) {
        console.error('API error:', err);
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
    const endpoints = {
        'photo':        [`/${camId}/photo`, 'POST'],
        'video-start':  [`/${camId}/video/start`, 'POST'],
        'video-stop':   [`/${camId}/video/stop`, 'POST'],
        'stream-start': [`/${camId}/stream/start`, 'POST'],
        'stream-stop':  [`/${camId}/stream/stop`, 'POST'],
    };

    const [endpoint, method] = endpoints[action];
    const result = await apiCall(endpoint, method);

    if (!result) return;

    if (action === 'photo') {
        showNotification(result.success ? `Cam ${camId}: Photo captured!` : `Cam ${camId}: Photo failed`, result.success ? 'success' : 'error');
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

function pipFullscreen() {
    // Fullscreen whichever stream is visible (prefer cam1 if both)
    const active = [1, 2].find(id => el(`pip-stream-${id}`)?.style.display !== 'none');
    if (active) enterFullscreen(`pip-img-${active}`);
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
    // Server emits cam1/cam2 booleans on socket connect
    if (data.cam1 !== undefined) updateCamStatus(1, data.cam1);
    if (data.cam2 !== undefined) updateCamStatus(2, data.cam2);
});

socket.on('photo_taken', (data) => {
    if (data.success) showNotification(`Cam ${data.cam_id || '?'}: Photo captured!`, 'success');
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
                        onchange="tlSetDuration(${id},+this.value)">s
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
    if (!_tlBlocks[lightId])      _tlBlocks[lightId]      = [];
    if (!_tlTrUI[lightId])        _tlTrUI[lightId]        = [];
    if (!_tlLoop[lightId])        _tlLoop[lightId]        = false;
    if (_tlSelected[lightId] === undefined) _tlSelected[lightId] = null;
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
        segs.push({ start, duration: b.duration, color: rgb, type: 'block' });
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

        html += `<div class="tl-block${sel ? ' tl-block-sel' : ''}"
            style="left:${leftPct}%;width:${wPct}%;background:${dispCol}"
            onmousedown="tlBlockMouseDown(event,${lightId},${i})"
            onclick="tlSelectBlock(${lightId},${i})"
            title="${b.duration.toFixed(1)}s @ ${start.toFixed(1)}s">
            <span class="tl-block-label">${b.duration.toFixed(1)}s</span>
            <div class="tl-resize-handle" onmousedown="tlResizeMouseDown(event,${lightId},${i})"></div>
        </div>`;

        // Transition segment
        if (i < blocks.length - 1) {
            const tr = trs[i] ?? { mode: 'cut', duration: 1.0 };
            if (tr.mode !== 'cut' && tr.duration > 0) {
                const trStart = start + b.duration;
                const trLeft  = (trStart / duration * 100).toFixed(3);
                const trW     = (tr.duration / duration * 100).toFixed(3);
                const nb      = blocks[i + 1];
                const nbri    = nb.brightness / 100;
                const nr  = parseInt(nb.color.slice(1,3), 16);
                const ng  = parseInt(nb.color.slice(3,5), 16);
                const nbb = parseInt(nb.color.slice(5,7), 16);
                const nextCol = `rgb(${Math.round(nr*nbri)},${Math.round(ng*nbri)},${Math.round(nbb*nbri)})`;
                html += `<div class="tl-transition tl-tr-gradual"
                    style="left:${trLeft}%;width:${trW}%;background:linear-gradient(to right,${dispCol},${nextCol})"
                    title="${tr.mode} ${tr.duration}s">~</div>`;
            } else {
                const cutLeft = ((start + b.duration) / duration * 100).toFixed(3);
                html += `<div class="tl-cut-marker" style="left:${cutLeft}%"></div>`;
            }
        }
    }

    canvas.innerHTML = html;
    tlRenderEditor(lightId);
    mtRender();
}

// ── Drag to move / resize blocks ─────────────────────────────────────────────

function tlBlockMouseDown(e, lightId, idx) {
    if (e.target.classList.contains('tl-resize-handle')) return;
    e.preventDefault();
    const canvas = document.getElementById(`light${lightId}-tl-canvas`);
    if (!canvas) return;
    _tlDrag = {
        type:       'move',
        lightId,
        idx,
        startX:     e.clientX,
        origVal:    _tlBlocks[lightId][idx].start ?? 0,
        totalDur:   _tlDuration[lightId] || 30,
        canvasWidth: canvas.getBoundingClientRect().width,
    };
}

function tlResizeMouseDown(e, lightId, idx) {
    e.preventDefault();
    e.stopPropagation();
    const canvas = document.getElementById(`light${lightId}-tl-canvas`);
    if (!canvas) return;
    _tlDrag = {
        type:       'resize',
        lightId,
        idx,
        startX:     e.clientX,
        origVal:    _tlBlocks[lightId][idx].duration,
        totalDur:   _tlDuration[lightId] || 30,
        canvasWidth: canvas.getBoundingClientRect().width,
    };
}

document.addEventListener('mousemove', e => {
    if (!_tlDrag) return;
    const { type, lightId, idx, startX, origVal, totalDur, canvasWidth } = _tlDrag;
    const dSec = (e.clientX - startX) / canvasWidth * totalDur;
    const block = _tlBlocks[lightId]?.[idx];
    if (!block) return;
    if (type === 'move') {
        block.start = Math.max(0, Math.min(totalDur - block.duration, origVal + dSec));
        block.start = Math.round(block.start * 10) / 10;
    } else {
        block.duration = Math.max(0.1, Math.round((origVal + dSec) * 10) / 10);
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
    const b = blocks[sel];
    const tr = sel > 0 ? (_tlTrUI[lightId][sel - 1] ?? { mode: 'cut', duration: 1.0 }) : null;
    const trModeOpts = TL_TRANSITION_MODES.map(m =>
        `<option value="${m}"${tr?.mode === m ? ' selected' : ''}>${m.charAt(0).toUpperCase()+m.slice(1)}</option>`
    ).join('');
    editor.innerHTML = `
        <div class="tl-edit-row">
            <label>Color <input type="color" value="${b.color}" onchange="tlBlockProp(${lightId},${sel},'color',this.value)"></label>
            <label>Bri <input type="range" min="0" max="100" value="${b.brightness}" style="width:80px"
                oninput="tlBlockProp(${lightId},${sel},'brightness',+this.value);this.nextSibling.textContent=this.value+'%'"><span>${b.brightness}%</span></label>
            <label>Dur(s) <input type="number" min="0.1" step="0.1" value="${b.duration}" style="width:60px"
                onchange="tlBlockProp(${lightId},${sel},'duration',+this.value)"></label>
            <button class="btn btn-sm btn-danger" onclick="tlDeleteBlock(${lightId},${sel})">Delete</button>
        </div>${tr !== null ? `
        <div class="tl-edit-row tl-tr-row">
            <span class="tl-tr-label">Transition in:</span>
            <select onchange="tlTrProp(${lightId},${sel-1},'mode',this.value)">${trModeOpts}</select>
            <label>Dur(s) <input type="number" min="0.1" step="0.1" value="${tr.duration}" style="width:55px"
                onchange="tlTrProp(${lightId},${sel-1},'duration',+this.value)"></label>
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

function tlTrProp(lightId, trIdx, key, val) {
    _tlInit(lightId);
    if (!_tlTrUI[lightId][trIdx]) _tlTrUI[lightId][trIdx] = { mode: 'cut', duration: 1.0 };
    _tlTrUI[lightId][trIdx][key] = val;
    tlRenderCanvas(lightId);
    _tlAutoSave(lightId);
}

function tlSelectBlock(lightId, idx) {
    _tlSelected[lightId] = (_tlSelected[lightId] === idx) ? null : idx;
    tlRenderCanvas(lightId);
}

function tlAddBlock(lightId) {
    _tlInit(lightId);
    const blocks = _tlBlocks[lightId];
    const last = blocks[blocks.length - 1];
    let newStart = 0;
    if (last) {
        newStart = (last.start ?? 0) + last.duration;
        const lastTr = _tlTrUI[lightId][blocks.length - 1];
        if (lastTr && lastTr.mode !== 'cut') newStart += lastTr.duration;
    }
    blocks.push({ color: last?.color ?? '#0088ff', brightness: last?.brightness ?? 100, duration: 2.0, start: newStart });
    if (blocks.length > 1) {
        _tlTrUI[lightId].push({ mode: 'cut', duration: 1.0 });
    }
    _tlSelected[lightId] = blocks.length - 1;
    tlRenderCanvas(lightId);
    _tlAutoSave(lightId);
}

function tlDeleteBlock(lightId, idx) {
    _tlInit(lightId);
    const blocks = _tlBlocks[lightId];
    blocks.splice(idx, 1);
    // Remove adjacent transition
    if (_tlTrUI[lightId].length > 0) {
        const trIdx = idx > 0 ? idx - 1 : 0;
        if (_tlTrUI[lightId].length >= blocks.length) _tlTrUI[lightId].splice(trIdx, 1);
    }
    _tlSelected[lightId] = null;
    tlRenderCanvas(lightId);
    _tlAutoSave(lightId);
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
    // Sort blocks by start time before pushing to server
    const blocks = [..._tlBlocks[lightId]].sort((a, b) => (a.start ?? 0) - (b.start ?? 0));
    const transitions = _tlTrUI[lightId].map(tr => _tlTrToServer(tr.mode, tr.duration));
    await fetch(`/api/lights/${lightId}/timeline`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ blocks, transitions, loop: _tlLoop[lightId] })
    });
    const res = await fetch(`/api/lights/${lightId}/timeline/play`, { method: 'POST' }).then(r => r.json()).catch(() => null);
    if (res?.error) showNotification(res.error, 'error');
    else showNotification(`Playing ${blocks.length} blocks`, 'info');
}

async function tlStop(lightId) {
    await fetch(`/api/lights/${lightId}/timeline/stop`, { method: 'POST' });
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
        blocks: _tlBlocks[lightId],
        transitions: _tlTrUI[lightId],
        loop: _tlLoop[lightId]
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

// Track config (cameras are stubs until next session)
const MT_TRACK_DEFS = [
    { key: 'rail', label: 'Rail',     color: '#4caf50', stub: false },
    { key: 'cam1', label: 'Camera 1', color: '#0099ff', stub: true  },
    { key: 'cam2', label: 'Camera 2', color: '#ff9900', stub: true  },
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
            const left  = (b.start   / total * 100).toFixed(3);
            const width = (b.duration / total * 100).toFixed(3);
            const sel   = _mt.selected?.track === td.key && _mt.selected?.idx === i;
            const label = (b.action || b.direction || '?') + ` ${b.duration}s`;
            return `<div class="mt-block${sel ? ' mt-block-sel' : ''}"
                style="left:${left}%;width:${width}%;background:${td.color || '#666'}"
                onclick="mtSelectBlock('${td.key}',${i})" title="${label}">
                <span class="mt-block-label">${label}</span>
            </div>`;
        }).join('');
        const stubNote = td.stub ? '<span style="font-size:0.65rem;color:#555;padding-left:6px">(soon)</span>' : '';
        return `<div class="mt-row">
            <div class="mt-row-label">${td.label}${stubNote}</div>
            <div class="mt-row-canvas" id="mt-canvas-${td.key}">
                ${blocksHtml}
                ${!td.stub ? `<button class="mt-add-btn" onclick="mtAddBlock('${td.key}')">+</button>` : ''}
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
                return `<div class="mt-block"
                    style="left:${left}%;width:${width}%;background:${s.color};border-radius:3px"
                    title="${s.duration}s">
                    <span class="mt-block-label">${s.duration}s</span>
                </div>`;
            } else {
                return `<div class="mt-tl-tr"
                    style="left:${left}%;width:${width}%;background:linear-gradient(90deg,${s.fromColor},${s.toColor})"
                    title="${s.mode}"></div>`;
            }
        }).join('');
        const empty = segs.length === 0
            ? '<span style="font-size:0.7rem;color:#555;padding-left:8px;line-height:42px">Add blocks in the Lights section</span>'
            : '';
        return `<div class="mt-row mt-row-light">
            <div class="mt-row-label" style="color:#e65100">${light.name || 'Light ' + lid}</div>
            <div class="mt-row-canvas">${segsHtml}${empty}</div>
        </div>`;
    }).join('');

    tl.innerHTML = ruler + nonLightRows + lightRows;
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

function mtRenderEditor() {
    const editor = document.getElementById('mt-editor');
    if (!editor) return;
    const sel = _mt.selected;
    if (!sel) { editor.innerHTML = ''; return; }

    const isLight  = sel.track.startsWith('light_');
    const lightId  = isLight ? sel.track.replace('light_','') : null;
    const blocks   = isLight ? _mt.lightTracks[lightId] : _mt.tracks[sel.track];
    const b        = blocks?.[sel.idx];
    if (!b) { editor.innerHTML = ''; return; }

    let html = `<div class="tl-edit-row">
        <label>Start(s) <input type="number" min="0" step="0.1" value="${b.start}" style="width:65px"
            onchange="mtBlockProp('${sel.track}',${sel.idx},'start',+this.value)"></label>
        <label>Dur(s) <input type="number" min="0.1" step="0.1" value="${b.duration}" style="width:65px"
            onchange="mtBlockProp('${sel.track}',${sel.idx},'duration',+this.value)"></label>`;

    if (isLight) {
        const tr = b.transition || { mode: 'cut', duration: 1.0 };
        const trOpts = TL_TRANSITION_MODES.map(m =>
            `<option value="${m}"${tr.mode===m?' selected':''}>${m.charAt(0).toUpperCase()+m.slice(1)}</option>`
        ).join('');
        html += `
        <label>Color <input type="color" value="${b.color}" onchange="mtBlockProp('${sel.track}',${sel.idx},'color',this.value)"></label>
        <label>Bri <input type="range" min="0" max="100" value="${b.brightness}" style="width:80px"
            oninput="mtBlockProp('${sel.track}',${sel.idx},'brightness',+this.value);this.nextSibling.textContent=this.value+'%'"><span>${b.brightness}%</span></label>
        <label>Transition <select onchange="mtTrProp('${sel.track}',${sel.idx},'mode',this.value)">${trOpts}</select></label>
        <label>Tr dur <input type="number" min="0.1" step="0.1" value="${tr.duration}" style="width:55px"
            onchange="mtTrProp('${sel.track}',${sel.idx},'duration',+this.value)"></label>`;
    } else if (sel.track === 'rail') {
        const dirOpts = ['forward','backward'].map(d =>
            `<option value="${d}"${b.direction===d?' selected':''}>${d.charAt(0).toUpperCase()+d.slice(1)}</option>`
        ).join('');
        html += `
        <label>Direction <select onchange="mtBlockProp('${sel.track}',${sel.idx},'direction',this.value)">${dirOpts}</select></label>
        <label>Speed(μs) <input type="number" min="10" max="2000" value="${b.speed||91}" style="width:65px"
            onchange="mtBlockProp('${sel.track}',${sel.idx},'speed',+this.value)"></label>`;
    }

    html += `<button class="btn btn-sm btn-danger" onclick="mtDeleteBlock('${sel.track}',${sel.idx})">Delete</button></div>`;
    editor.innerHTML = html;
}

function mtBlockProp(track, idx, key, val) {
    const isLight = track.startsWith('light_');
    const lid     = isLight ? track.replace('light_','') : null;
    const blocks  = isLight ? _mt.lightTracks[lid] : _mt.tracks[track];
    if (blocks?.[idx] !== undefined) { blocks[idx][key] = val; mtRender(); }
}

function mtTrProp(track, idx, key, val) {
    const isLight = track.startsWith('light_');
    const lid     = isLight ? track.replace('light_','') : null;
    const blocks  = isLight ? _mt.lightTracks[lid] : _mt.tracks[track];
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
    }
    _mt.selected = null;
    mtRender();
}

function mtDeleteBlock(track, idx) {
    const isLight = track.startsWith('light_');
    const lid     = isLight ? track.replace('light_','') : null;
    const blocks  = isLight ? _mt.lightTracks[lid] : _mt.tracks[track];
    if (blocks) { blocks.splice(idx, 1); _mt.selected = null; mtRender(); }
}

async function mtPlay() {
    // Push current state to server
    const dur = parseFloat(document.getElementById('mt-duration')?.value || 60);
    _mt.duration = dur;
    await fetch('/api/master-timeline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tracks: _mt.tracks, light_tracks: _mt.lightTracks,
                               duration: _mt.duration, loop: _mt.loop })
    });
    await fetch('/api/master-timeline/play', { method: 'POST' });
    showNotification('Master timeline playing', 'info');
}

async function mtStop() {
    await fetch('/api/master-timeline/stop', { method: 'POST' });
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

document.addEventListener('DOMContentLoaded', async () => {
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
    }
    // Turn off all lights on page load so every session starts with a clean state
    const lightsData = await fetch('/api/lights').then(r => r.json()).catch(() => []);
    await Promise.all(lightsData.map(l => fetch(`/api/lights/${l.id}/off`, { method: 'POST' })));
    mtRender();
});
