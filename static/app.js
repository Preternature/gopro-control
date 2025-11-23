// GoPro Controller - Client Application

// Initialize Socket.IO connection
const socket = io();

// State
let isRecording = false;
let isIntervalRunning = false;

// DOM Elements
const elements = {
    connectionStatus: document.getElementById('connection-status'),
    statusText: document.querySelector('.status-text'),

    // Connection UI
    mainContent: document.getElementById('main-content'),
    goProNotConnected: document.getElementById('gopro-not-connected'),
    btnRetryConnection: document.getElementById('btn-retry-connection'),

    // WiFi controls
    btnWifiGopro: document.getElementById('btn-wifi-gopro'),
    btnWifiHome: document.getElementById('btn-wifi-home'),

    // Photo controls
    btnPhoto: document.getElementById('btn-photo'),
    btnPhotoTimer: document.getElementById('btn-photo-timer'),
    btnPhotoClick: document.getElementById('btn-photo-click'),
    photoDelay: document.getElementById('photo-delay'),

    // Interval controls
    btnIntervalStart: document.getElementById('btn-interval-start'),
    btnIntervalStop: document.getElementById('btn-interval-stop'),
    intervalSeconds: document.getElementById('interval-seconds'),

    // Video controls
    btnVideoStart: document.getElementById('btn-video-start'),
    btnVideoStop: document.getElementById('btn-video-stop'),
    recordingIndicator: document.getElementById('recording-indicator'),

    // Settings
    resolution: document.getElementById('resolution'),
    fps: document.getElementById('fps'),
    btnApplySettings: document.getElementById('btn-apply-settings'),

    // Preview
    btnStartPreview: document.getElementById('btn-start-preview'),
    btnStopPreview: document.getElementById('btn-stop-preview'),
    previewVideo: document.getElementById('preview-video'),
    previewPlaceholder: document.getElementById('preview-placeholder'),

    // Media
    btnRefreshMedia: document.getElementById('btn-refresh-media'),
    btnShowDownloads: document.getElementById('btn-show-downloads'),
    mediaList: document.getElementById('media-list'),

    // Playback
    playbackSection: document.getElementById('playback-section'),
    videoPlayer: document.getElementById('video-player'),
    imageViewer: document.getElementById('image-viewer'),
    btnClosePlayback: document.getElementById('btn-close-playback'),

    // Notifications
    notifications: document.getElementById('notifications')
};

// Utility Functions
function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.textContent = message;
    elements.notifications.appendChild(notification);

    setTimeout(() => {
        notification.remove();
    }, 4000);
}

async function apiCall(endpoint, method = 'GET', data = null) {
    try {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json'
            }
        };

        if (data) {
            options.body = JSON.stringify(data);
        }

        const response = await fetch(`/api${endpoint}`, options);
        return await response.json();
    } catch (error) {
        console.error('API Error:', error);
        showNotification('Connection error', 'error');
        return null;
    }
}

// Store current GoPro IP for media URLs
let currentGoProIP = '10.5.5.9';

function updateConnectionStatus(connected, connType = null, ip = null) {
    if (connected) {
        elements.connectionStatus.classList.add('connected');
        elements.connectionStatus.classList.remove('disconnected');
        let statusText = 'Connected';
        if (connType && ip) {
            statusText = `${connType.toUpperCase()} - ${ip}`;
            currentGoProIP = ip;
        }
        elements.statusText.textContent = statusText;

        // Show main content, hide not-connected message
        elements.mainContent.style.display = 'block';
        elements.goProNotConnected.style.display = 'none';
    } else {
        elements.connectionStatus.classList.remove('connected');
        elements.connectionStatus.classList.add('disconnected');
        elements.statusText.textContent = 'Disconnected';

        // Hide main content, show not-connected message
        elements.mainContent.style.display = 'none';
        elements.goProNotConnected.style.display = 'block';
    }
}

// Camera Control Functions
async function takePhoto() {
    elements.btnPhoto.disabled = true;
    const result = await apiCall('/photo', 'POST');
    elements.btnPhoto.disabled = false;

    if (result?.success) {
        showNotification('Photo captured!', 'success');
    } else {
        showNotification('Failed to take photo', 'error');
    }
}

async function takeTimerPhoto() {
    const delay = parseInt(elements.photoDelay.value) || 5;
    const result = await apiCall('/photo/timer', 'POST', { delay });

    if (result?.success) {
        showNotification(`Photo in ${delay} seconds...`, 'info');
    } else {
        showNotification('Failed to start timer', 'error');
    }
}

async function startInterval() {
    const interval = parseInt(elements.intervalSeconds.value) || 10;
    const result = await apiCall('/photo/interval/start', 'POST', { interval });

    if (result?.success) {
        isIntervalRunning = true;
        elements.btnIntervalStart.disabled = true;
        elements.btnIntervalStop.disabled = false;
        showNotification(`Interval started: every ${interval} seconds`, 'success');
    } else {
        showNotification('Failed to start interval', 'error');
    }
}

async function stopInterval() {
    const result = await apiCall('/photo/interval/stop', 'POST');

    if (result?.success) {
        isIntervalRunning = false;
        elements.btnIntervalStart.disabled = false;
        elements.btnIntervalStop.disabled = true;
        showNotification('Interval stopped', 'info');
    }
}

async function startVideo() {
    elements.btnVideoStart.disabled = true;
    const result = await apiCall('/video/start', 'POST');

    if (result?.success) {
        isRecording = true;
        elements.btnVideoStop.disabled = false;
        elements.recordingIndicator.classList.remove('hidden');
        showNotification('Recording started', 'success');
    } else {
        elements.btnVideoStart.disabled = false;
        showNotification('Failed to start recording', 'error');
    }
}

async function stopVideo() {
    const result = await apiCall('/video/stop', 'POST');

    if (result?.success) {
        isRecording = false;
        elements.btnVideoStart.disabled = false;
        elements.btnVideoStop.disabled = true;
        elements.recordingIndicator.classList.add('hidden');
        showNotification('Recording stopped', 'success');
    } else {
        showNotification('Failed to stop recording', 'error');
    }
}

async function applySettings() {
    const resolution = elements.resolution.value;
    const fps = parseInt(elements.fps.value);

    const resResult = await apiCall('/settings/resolution', 'POST', { resolution });
    const fpsResult = await apiCall('/settings/fps', 'POST', { fps });

    if (resResult?.success && fpsResult?.success) {
        showNotification('Settings applied', 'success');
    } else {
        showNotification('Failed to apply some settings', 'error');
    }
}

// Media Functions
async function loadMediaList() {
    elements.mediaList.innerHTML = '<p class="placeholder">Loading...</p>';

    const result = await apiCall('/media/list');

    if (result?.files && result.files.length > 0) {
        elements.mediaList.innerHTML = '';

        result.files.reverse().forEach(file => {
            const item = createMediaItem(file);
            elements.mediaList.appendChild(item);
        });
    } else {
        elements.mediaList.innerHTML = '<p class="placeholder">No media found on camera</p>';
    }
}

function createMediaItem(file) {
    const item = document.createElement('div');
    item.className = 'media-item';

    const isVideo = file.filename.toLowerCase().endsWith('.mp4');
    const thumbnailUrl = `http://${currentGoProIP}:8080/gopro/media/thumbnail?path=${file.directory}/${file.filename}`;

    item.innerHTML = `
        <img src="${thumbnailUrl}" alt="${file.filename}" onerror="this.src='/static/placeholder.png'">
        <div class="media-item-info">
            <div class="media-item-name">${file.filename}</div>
            <div class="media-item-actions">
                <button class="btn btn-secondary" onclick="downloadMedia('${file.directory}', '${file.filename}')">Download</button>
                <button class="btn btn-primary" onclick="playMedia('${file.url}', ${isVideo})">Play</button>
            </div>
        </div>
    `;

    return item;
}

async function downloadMedia(directory, filename) {
    showNotification(`Downloading ${filename}...`, 'info');

    const result = await apiCall('/media/download', 'POST', { directory, filename });

    if (result?.success) {
        showNotification(`Downloaded: ${filename}`, 'success');
    } else {
        showNotification('Download failed', 'error');
    }
}

function playMedia(url, isVideo) {
    elements.playbackSection.classList.remove('hidden');

    if (isVideo) {
        elements.videoPlayer.classList.remove('hidden');
        elements.imageViewer.classList.add('hidden');
        elements.videoPlayer.src = url;
        elements.videoPlayer.play();
    } else {
        elements.videoPlayer.classList.add('hidden');
        elements.imageViewer.classList.remove('hidden');
        elements.imageViewer.src = url;
    }
}

function closePlayback() {
    elements.playbackSection.classList.add('hidden');
    elements.videoPlayer.pause();
    elements.videoPlayer.src = '';
    elements.imageViewer.src = '';
}

async function showDownloads() {
    const result = await apiCall('/downloads/list');

    if (result?.files && result.files.length > 0) {
        elements.mediaList.innerHTML = '';

        result.files.forEach(filename => {
            const item = document.createElement('div');
            item.className = 'media-item';

            const isVideo = filename.toLowerCase().endsWith('.mp4');

            item.innerHTML = `
                <div class="media-item-info" style="padding: 20px;">
                    <div class="media-item-name">${filename}</div>
                    <div class="media-item-actions">
                        <button class="btn btn-primary" onclick="playMedia('/downloads/${filename}', ${isVideo})">Play</button>
                    </div>
                </div>
            `;

            elements.mediaList.appendChild(item);
        });
    } else {
        elements.mediaList.innerHTML = '<p class="placeholder">No downloaded files</p>';
    }
}

// WiFi Functions
async function switchToGoPro() {
    // Check if GoPro WiFi is available first
    const check = await apiCall('/wifi/check');
    if (!check?.available) {
        showNotification(check?.message || 'GoPro WiFi not found. Use the Quik app to activate it.', 'error');
        return;
    }

    showNotification('Switching to GoPro WiFi... Page will reconnect automatically.', 'info');
    elements.btnWifiGopro.disabled = true;

    // Fire and forget - the network will drop during switch
    fetch('/api/wifi/gopro', { method: 'POST' }).catch(() => {});

    // Wait for network to settle, then try to reconnect
    setTimeout(async () => {
        let retries = 10;
        while (retries > 0) {
            try {
                const response = await fetch('/api/status');
                if (response.ok) {
                    showNotification('Connected to GoPro WiFi', 'success');
                    elements.btnWifiGopro.disabled = false;
                    return;
                }
            } catch (e) {
                // Still reconnecting
            }
            await new Promise(r => setTimeout(r, 1000));
            retries--;
        }
        showNotification('Failed to reconnect - refresh page', 'error');
        elements.btnWifiGopro.disabled = false;
    }, 3000);
}

async function switchToHome() {
    showNotification('Switching to home WiFi... Page will reconnect automatically.', 'info');
    elements.btnWifiHome.disabled = true;

    // Fire and forget
    fetch('/api/wifi/home', { method: 'POST' }).catch(() => {});

    // Wait for network to settle, then try to reconnect
    setTimeout(async () => {
        let retries = 10;
        while (retries > 0) {
            try {
                const response = await fetch('/api/status');
                if (response.ok) {
                    showNotification('Connected to home WiFi', 'success');
                    elements.btnWifiHome.disabled = false;
                    return;
                }
            } catch (e) {
                // Still reconnecting
            }
            await new Promise(r => setTimeout(r, 1000));
            retries--;
        }
        showNotification('Failed to reconnect - refresh page', 'error');
        elements.btnWifiHome.disabled = false;
    }, 3000);
}

// Preview Functions
let hlsPlayer = null;

async function startPreview() {
    showNotification('Starting preview stream...', 'info');
    const result = await apiCall('/stream/start', 'POST');

    if (result?.success) {
        showNotification('Preview stream started - waiting for HLS segments...', 'info');

        const video = elements.previewVideo;

        // Wait a moment for FFmpeg to create initial segments
        setTimeout(() => {
            if (Hls.isSupported()) {
                hlsPlayer = new Hls({
                    liveSyncDurationCount: 1,
                    liveMaxLatencyDurationCount: 3,
                    lowLatencyMode: true
                });
                hlsPlayer.loadSource('/static/hls/stream.m3u8');
                hlsPlayer.attachMedia(video);
                hlsPlayer.on(Hls.Events.MANIFEST_PARSED, () => {
                    video.play();
                    showNotification('Preview stream playing', 'success');
                });
                hlsPlayer.on(Hls.Events.ERROR, (event, data) => {
                    if (data.fatal) {
                        showNotification('Stream error - retrying...', 'error');
                        hlsPlayer.loadSource('/static/hls/stream.m3u8');
                    }
                });
            } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                // Safari native HLS support
                video.src = '/static/hls/stream.m3u8';
                video.play();
            }

            video.style.display = 'block';
            elements.previewPlaceholder.style.display = 'none';
            elements.btnStartPreview.style.display = 'none';
            elements.btnStopPreview.style.display = 'inline-block';
        }, 3000);
    } else {
        showNotification('Failed to start preview', 'error');
    }
}

async function stopPreview() {
    const result = await apiCall('/stream/stop', 'POST');

    if (result?.success) {
        showNotification('Preview stopped', 'info');

        if (hlsPlayer) {
            hlsPlayer.destroy();
            hlsPlayer = null;
        }

        elements.previewVideo.src = '';
        elements.previewVideo.style.display = 'none';
        elements.previewPlaceholder.style.display = 'block';
        elements.btnStartPreview.style.display = 'inline-block';
        elements.btnStopPreview.style.display = 'none';
    }
}

// Quick photo from preview
async function takePhotoClick() {
    elements.btnPhotoClick.disabled = true;
    const result = await apiCall('/photo', 'POST');
    elements.btnPhotoClick.disabled = false;

    if (result?.success) {
        showNotification('📸 Photo captured!', 'success');
    } else {
        showNotification('Failed to take photo', 'error');
    }
}

// Socket.IO Event Handlers
socket.on('connect', () => {
    console.log('Connected to server');
});

socket.on('connection_status', (data) => {
    updateConnectionStatus(data.connected, data.type, data.ip);
});

socket.on('photo_taken', (data) => {
    if (data.success) {
        showNotification('Photo captured!', 'success');
    }
});

socket.on('download_progress', (data) => {
    console.log(`Download progress: ${data.progress.toFixed(1)}%`);
});

// Keep alive timer
setInterval(() => {
    socket.emit('keep_alive');
}, 30000);

// Check connection periodically
setInterval(async () => {
    const result = await apiCall('/status');
    if (result) {
        updateConnectionStatus(result.connected, result.type, result.ip);
    }
}, 5000);

// Retry connection
async function retryConnection() {
    showNotification('Checking GoPro connection...', 'info');
    const result = await apiCall('/status');
    if (result) {
        updateConnectionStatus(result.connected, result.type, result.ip);
        if (result.connected) {
            showNotification('GoPro connected!', 'success');
        } else {
            showNotification('GoPro not found. Make sure it\'s on and WiFi is enabled.', 'error');
        }
    }
}

// Event Listeners
elements.btnRetryConnection.addEventListener('click', retryConnection);
elements.btnWifiGopro.addEventListener('click', switchToGoPro);
elements.btnWifiHome.addEventListener('click', switchToHome);
elements.btnPhoto.addEventListener('click', takePhoto);
elements.btnPhotoTimer.addEventListener('click', takeTimerPhoto);
elements.btnPhotoClick.addEventListener('click', takePhotoClick);
elements.btnIntervalStart.addEventListener('click', startInterval);
elements.btnIntervalStop.addEventListener('click', stopInterval);
elements.btnVideoStart.addEventListener('click', startVideo);
elements.btnVideoStop.addEventListener('click', stopVideo);
elements.btnApplySettings.addEventListener('click', applySettings);
elements.btnStartPreview.addEventListener('click', startPreview);
elements.btnStopPreview.addEventListener('click', stopPreview);
elements.btnRefreshMedia.addEventListener('click', loadMediaList);
elements.btnShowDownloads.addEventListener('click', showDownloads);
elements.btnClosePlayback.addEventListener('click', closePlayback);

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    const result = await apiCall('/status');
    if (result) {
        updateConnectionStatus(result.connected, result.type, result.ip);
    }
});
