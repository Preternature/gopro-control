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

    // Photo controls
    btnPhoto: document.getElementById('btn-photo'),
    btnPhotoTimer: document.getElementById('btn-photo-timer'),
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
    } else {
        elements.connectionStatus.classList.remove('connected');
        elements.connectionStatus.classList.add('disconnected');
        elements.statusText.textContent = 'Disconnected';
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

// Preview Functions
async function startPreview() {
    const result = await apiCall('/stream/start', 'POST');

    if (result?.success) {
        showNotification('Preview stream started', 'success');
        // Note: Actual video preview requires additional setup (VLC/ffplay)
    } else {
        showNotification('Failed to start preview', 'error');
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

// Event Listeners
elements.btnPhoto.addEventListener('click', takePhoto);
elements.btnPhotoTimer.addEventListener('click', takeTimerPhoto);
elements.btnIntervalStart.addEventListener('click', startInterval);
elements.btnIntervalStop.addEventListener('click', stopInterval);
elements.btnVideoStart.addEventListener('click', startVideo);
elements.btnVideoStop.addEventListener('click', stopVideo);
elements.btnApplySettings.addEventListener('click', applySettings);
elements.btnStartPreview.addEventListener('click', startPreview);
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
