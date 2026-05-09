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
    showNotification(`Cam ${camId}: Preview stopped`, 'info');
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
        const panSlider = el('base-angle');
        if (panSlider) panSlider.value = 180 - status.base_angle;
        const tiltSlider = el('cam-us');
        if (tiltSlider) tiltSlider.value = status.cam_us;
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

function arduinoBaseAngle(val) {
    // Invert so slider-left = physical left
    debounceArduino('base-angle', () => arduinoPost('gimbal/base', { angle: 180 - parseInt(val) }));
}

function arduinoCamUs(val) {
    debounceArduino('cam-us', () => arduinoPost('gimbal/cam', { us: parseInt(val) }));
}

function arduinoSetBase(angle) {
    el('base-angle').value = angle;
    arduinoBaseAngle(angle);
}

function arduinoSetCamUs(us) {
    el('cam-us').value = us;
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
                <button class="btn btn-sm btn-danger" style="padding:2px 7px;font-size:0.7rem" onclick="removeLight(${id})" title="Remove">✕</button>
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
            </div>
            <div class="tl-canvas" id="light${id}-tl-canvas"></div>
            <div class="tl-editor" id="light${id}-tl-editor"></div>
        </div>
    </div>`;
}

async function renderLights() {
    const data = await fetch('/api/lights').then(r => r.json()).catch(() => []);
    const grid = document.getElementById('lights-grid');
    if (grid) grid.innerHTML = data.map(_buildLightPanel).join('');
}

async function addLight() {
    await fetch('/api/lights', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    await renderLights();
}

async function removeLight(lightId) {
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

const _tlBlocks      = {};  // lightId → [{color, brightness, duration}]
const _tlTransitions = {};  // lightId → [{color_mode, brightness_mode, duration}]
const _tlSelected    = {};  // lightId → block index or null
const _tlLoop        = {};  // lightId → bool

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
}

function tlRenderCanvas(lightId) {
    _tlInit(lightId);
    const canvas = document.getElementById(`light${lightId}-tl-canvas`);
    if (!canvas) return;
    const blocks = _tlBlocks[lightId];
    const trs    = _tlTrUI[lightId];
    if (!blocks.length) {
        canvas.innerHTML = '<span class="tl-empty">No blocks — click + Block to add</span>';
        tlRenderEditor(lightId);
        return;
    }
    const total = blocks.reduce((s, b) => s + b.duration, 0) + trs.reduce((s, t) => s + (t.mode !== 'cut' ? t.duration : 0), 0);
    let html = '';
    for (let i = 0; i < blocks.length; i++) {
        const b = blocks[i];
        const sel = _tlSelected[lightId] === i;
        const bri = b.brightness / 100;
        const r = parseInt(b.color.slice(1,3), 16), g = parseInt(b.color.slice(3,5), 16), bb = parseInt(b.color.slice(5,7), 16);
        const dispCol = `rgb(${Math.round(r*bri)},${Math.round(g*bri)},${Math.round(bb*bri)})`;
        const wPct = (b.duration / total * 100).toFixed(2);
        html += `<div class="tl-block${sel ? ' tl-block-sel' : ''}" style="width:${wPct}%;background:${dispCol}"
            onclick="tlSelectBlock(${lightId},${i})" title="${b.duration}s">
            <span class="tl-block-label">${b.duration}s</span>
        </div>`;
        if (i < blocks.length - 1) {
            const tr = trs[i] ?? { mode: 'cut', duration: 1.0 };
            const trW = tr.mode !== 'cut' ? (tr.duration / total * 100).toFixed(2) : 0;
            html += `<div class="tl-transition${trW > 0 ? ' tl-tr-gradual' : ''}" style="width:${Math.max(trW, 0.5)}%"
                onclick="tlSelectBlock(${lightId},${i})" title="Transition: ${tr.mode}">
                ${tr.mode !== 'cut' ? '~' : '|'}
            </div>`;
        }
    }
    canvas.innerHTML = html;
    tlRenderEditor(lightId);
}

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
    }
}

function tlTrProp(lightId, trIdx, key, val) {
    _tlInit(lightId);
    if (!_tlTrUI[lightId][trIdx]) _tlTrUI[lightId][trIdx] = { mode: 'cut', duration: 1.0 };
    _tlTrUI[lightId][trIdx][key] = val;
    tlRenderCanvas(lightId);
}

function tlSelectBlock(lightId, idx) {
    _tlSelected[lightId] = (_tlSelected[lightId] === idx) ? null : idx;
    tlRenderCanvas(lightId);
}

function tlAddBlock(lightId) {
    _tlInit(lightId);
    const blocks = _tlBlocks[lightId];
    const last = blocks[blocks.length - 1];
    blocks.push({ color: last?.color ?? '#0088ff', brightness: last?.brightness ?? 100, duration: 2.0 });
    if (blocks.length > 1) {
        _tlTrUI[lightId].push({ mode: 'cut', duration: 1.0 });
    }
    _tlSelected[lightId] = blocks.length - 1;
    tlRenderCanvas(lightId);
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
}

async function tlPlay(lightId) {
    _tlInit(lightId);
    // Push current state to server first
    const blocks = _tlBlocks[lightId];
    const transitions = _tlTrUI[lightId].map((tr, i) => _tlTrToServer(tr.mode, tr.duration));
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
    _tlBlocks[lightId]      = data.blocks ?? [];
    _tlTrUI[lightId]        = (data.transitions ?? []).map(t => t.mode !== undefined ? t : _tlTrFromServer(t));
    _tlLoop[lightId]        = data.loop ?? false;
    _tlSelected[lightId]    = null;
    const loopBtn = document.getElementById(`light${lightId}-tl-loop`);
    if (loopBtn) loopBtn.classList.toggle('tl-loop-on', _tlLoop[lightId]);
    tlRenderCanvas(lightId);
    showNotification(`Loaded ${_tlBlocks[lightId].length} blocks`, 'success');
    input.value = '';
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
    await renderLights();
});
