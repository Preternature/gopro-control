// GoPro Controller — Dual Camera Client

const socket = io();

// Per-camera state
const camState = {
    1: { recording: false, intervalRunning: false, hlsPlayer: null, ip: null },
    2: { recording: false, intervalRunning: false, hlsPlayer: null, ip: null },
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
        showNotification(`Cam ${camId}: Recording started`, 'success');
    }

    if (action === 'video-stop' && result.success) {
        camState[camId].recording = false;
        el(`cam${camId}-btn-rec-start`).disabled = false;
        el(`cam${camId}-btn-rec-stop`).disabled = true;
        el(`cam${camId}-rec-indicator`).classList.add('hidden');
        showNotification(`Cam ${camId}: Recording stopped`, 'info');
    }
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

// ─── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
    const [s1, s2] = await Promise.all([apiCall('/1/status'), apiCall('/2/status')]);
    if (s1) updateCamStatus(1, s1.connected, s1.ip, s1.type);
    if (s2) updateCamStatus(2, s2.connected, s2.ip, s2.type);
});
