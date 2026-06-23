"""
Audio Recording Module
Captures the instrument inputs from the audio interface (Arturia MiniFuse 4)
to per-insert mono WAV files, in sync with GoPro video recording.

Uses WASAPI exclusive mode for bit-perfect capture straight off the interface's
converter. This is sonically identical to ASIO for recording (ASIO's advantage is
monitoring latency, which the MiniFuse handles in hardware).

Capture is a single SHARED session reference-counted across both cameras:
the first camera to start recording opens the stream; the last one to stop
finalizes and writes the files.

THREADING MODEL
---------------
PortAudio binds WASAPI/COM to the thread that initializes it. sounddevice does
that at import time (the main thread), but Flask serves requests on worker threads
— opening an exclusive stream from a worker thread fails with 'Invalid device'
(-9996). So ALL PortAudio operations (init, device query, stream open/close) run
on one dedicated worker thread, which rebinds PortAudio to itself at startup.
Audio callbacks still run on PortAudio's own threads; they only touch buffers and
the level meter under locks. Request threads talk to the worker via _submit().
"""

import os
import time
import math
import queue
import threading
from typing import Optional, List, Callable

import numpy as np
import sounddevice as sd
import soundfile as sf


class AudioRecorder:
    """Reference-counted WASAPI-exclusive recorder writing one mono WAV per insert."""

    TARGET_RATE = 96000          # preferred capture rate (requires interface clocked to match)
    DEVICE_MATCH = "Mic/Line/Inst"  # substring identifying the MiniFuse insert pair
    SUBTYPE = "PCM_24"           # 24-bit WAV
    CHANNELS = 2                 # inserts 1 & 2
    PRESENT_DBFS = -35.0         # peak above this = instrument feed "audible".
                                 # Idle noise floor measured ~-45 dBFS (spikes to -40),
                                 # so -35 keeps the dot dark until you actually play.
    _FULL_SCALE = 2147483648.0   # int32 full scale (2^31)

    def __init__(self, download_dir="downloads", device_match=None, target_rate=None):
        self.download_dir = download_dir
        self.device_match = device_match or self.DEVICE_MATCH
        self.target_rate = target_rate or self.TARGET_RATE
        self._lock = threading.Lock()
        self._active = set()                 # cam_ids currently recording
        self._stream: Optional[sd.InputStream] = None
        self._frames: List[np.ndarray] = []  # captured int32 blocks
        self._rate: Optional[int] = None
        self._session_name: Optional[str] = None
        self._overflows = 0
        # Live level metering (drives the "instrument audible" dot before recording)
        self._monitor_stream: Optional[sd.InputStream] = None
        self._level_lock = threading.Lock()
        self._level_peak = 0.0               # max peak (0..1) since last level() read
        self._last_monitor_attempt = 0.0     # rate-limit auto (re)start of the monitor
        # Dedicated PortAudio worker thread
        self._cmd_q: "queue.Queue" = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop,
                                        name="audio-worker", daemon=True)
        self._worker.start()

    # === PortAudio worker thread ===

    def _worker_loop(self) -> None:
        # Rebind PortAudio (WASAPI/COM) to THIS thread so every stream open succeeds.
        try:
            sd._terminate()
            sd._initialize()
        except Exception as e:
            print(f"[audio] worker PortAudio init failed: {e}")
        while True:
            job = self._cmd_q.get()
            if job is None:
                break
            fn, holder, done = job
            try:
                holder['result'] = fn()
            except Exception as e:
                holder['error'] = e
            finally:
                done.set()

    def _submit(self, fn: Callable, timeout: float = 10.0):
        """Run fn on the audio worker thread and return its result (blocks the caller)."""
        holder: dict = {}
        done = threading.Event()
        self._cmd_q.put((fn, holder, done))
        if not done.wait(timeout):
            print("[audio] worker timed out")
            return None
        if 'error' in holder:
            raise holder['error']
        return holder.get('result')

    # === device discovery (worker thread) ===

    def _find_device(self) -> Optional[int]:
        """Return the WASAPI input device index for the interface, or None."""
        try:
            wasapi = next(i for i, a in enumerate(sd.query_hostapis())
                          if a['name'] == 'Windows WASAPI')
        except StopIteration:
            return None
        for i, d in enumerate(sd.query_devices()):
            if (d['hostapi'] == wasapi and self.device_match in d['name']
                    and d['max_input_channels'] > 0):
                return i
        return None

    # === state queries ===

    def is_recording(self) -> bool:
        with self._lock:
            return self._stream is not None

    def status(self) -> dict:
        def q():
            dev = self._find_device()
            with self._lock:
                return {
                    "recording": self._stream is not None,
                    "active_cams": sorted(self._active),
                    "rate": self._rate,
                    "device_found": dev is not None,
                    "device": sd.query_devices(dev)['name'] if dev is not None else None,
                    "target_rate": self.target_rate,
                }
        return self._submit(q) or {"device_found": False, "device": None,
                                   "recording": False, "error": "audio worker timeout"}

    # === level metering ===

    def _note_level(self, indata) -> None:
        """Update the running peak from an audio block (called on audio threads)."""
        peak = float(np.max(np.abs(indata))) / self._FULL_SCALE
        with self._level_lock:
            if peak > self._level_peak:
                self._level_peak = peak

    def _monitor_callback(self, indata, frames, time_info, status):
        self._note_level(indata)

    def level(self) -> dict:
        """Current input level since the last read (drives the live audible dot)."""
        # Lazily (re)start the monitor when idle, rate-limited.
        if self._stream is None and self._monitor_stream is None:
            now = time.time()
            if now - self._last_monitor_attempt > 2.0:
                self._last_monitor_attempt = now
                self.start_monitor()
        with self._level_lock:
            peak = self._level_peak
            self._level_peak = 0.0
        dbfs = 20.0 * math.log10(peak) if peak > 0 else -120.0
        return {
            "level": round(peak, 5),
            "dbfs": round(dbfs, 1),
            "present": dbfs >= self.PRESENT_DBFS,
            "recording": self._stream is not None,
            "monitoring": self._monitor_stream is not None,
        }

    # === monitor lifecycle ===

    def start_monitor(self) -> bool:
        """Open a live level-monitoring stream (when not recording)."""
        return bool(self._submit(self._do_start_monitor))

    def _do_start_monitor(self) -> bool:
        with self._lock:
            if self._stream is not None or self._monitor_stream is not None:
                return False
            dev = self._find_device()
            if dev is None:
                return False
            try:
                rate = int(sd.query_devices(dev)['default_samplerate'])
                ms = sd.InputStream(
                    device=dev, channels=self.CHANNELS, samplerate=rate,
                    dtype='int32', blocksize=2048,
                    extra_settings=sd.WasapiSettings(exclusive=True),
                    callback=self._monitor_callback)
                ms.start()
                self._monitor_stream = ms
                return True
            except Exception as e:
                print(f"[audio] monitor start failed: {e}")
                self._monitor_stream = None
                return False

    def stop_monitor(self) -> None:
        """Close the level-monitoring stream."""
        self._submit(self._do_stop_monitor)

    def _do_stop_monitor(self) -> None:
        with self._lock:
            self._stop_monitor_locked()

    def _stop_monitor_locked(self) -> None:
        if self._monitor_stream is not None:
            try:
                self._monitor_stream.stop()
                self._monitor_stream.close()
            except Exception:
                pass
            self._monitor_stream = None

    # === recording callback ===

    def _callback(self, indata, frames, time_info, status):
        if status:
            self._overflows += 1
        self._note_level(indata)
        self._frames.append(indata.copy())

    # === session control ===

    def start_session(self, cam_id) -> dict:
        """Begin (or join) the shared capture. First caller opens the stream."""
        return self._submit(lambda: self._do_start_session(cam_id)) or {
            "started": False, "recording": False, "error": "audio worker timeout"}

    def _do_start_session(self, cam_id) -> dict:
        with self._lock:
            self._active.add(cam_id)
            if self._stream is not None:
                return {"started": False, "joined": True,
                        "rate": self._rate, "recording": True}

            # Free the device from the level monitor before opening the
            # exclusive recording stream.
            self._stop_monitor_locked()
            dev = self._find_device()
            if dev is None:
                self._active.discard(cam_id)
                return {"started": False, "recording": False,
                        "error": "Audio interface input not found (looked for "
                                 f"'{self.device_match}' on WASAPI)"}

            extra = sd.WasapiSettings(exclusive=True)
            warning = None
            # Prefer target rate; fall back to whatever the interface is clocked at.
            try:
                sd.check_input_settings(device=dev, channels=self.CHANNELS,
                                        samplerate=self.target_rate, dtype='int32',
                                        extra_settings=extra)
                rate = self.target_rate
            except Exception:
                rate = int(sd.query_devices(dev)['default_samplerate'])
                warning = (f"Interface is clocked at {rate} Hz. Set it to "
                           f"{self.target_rate} Hz in the Arturia control panel "
                           f"to capture at {self.target_rate}.")

            try:
                self._frames = []
                self._overflows = 0
                self._rate = rate
                st = sd.InputStream(
                    device=dev, channels=self.CHANNELS, samplerate=rate,
                    dtype='int32', extra_settings=extra, callback=self._callback)
                st.start()
                self._stream = st
                self._session_name = time.strftime("audio_%Y%m%d_%H%M%S")
            except Exception as e:
                self._stream = None
                self._rate = None
                self._active.discard(cam_id)
                return {"started": False, "recording": False, "error": str(e)}

            return {"started": True, "joined": False, "rate": rate,
                    "warning": warning, "recording": True}

    def stop_session(self, cam_id) -> dict:
        """Leave the shared capture. The last caller stops, writes files, resumes monitor."""
        res = self._submit(lambda: self._do_stop_session(cam_id))
        if res is None:
            return {"stopped": False, "error": "audio worker timeout"}
        if not res.get("_finalize"):
            return res  # not the last camera — recording continues

        frames = res["_frames"]
        rate = res["rate"]
        name = res["_name"]
        overflows = res["overflows"]

        # Resume live level monitoring for the dot now that recording is done.
        self.start_monitor()

        if not frames:
            return {"stopped": True, "files": [], "error": "No audio captured"}

        data = np.concatenate(frames, axis=0)  # (N, CHANNELS) int32
        os.makedirs(self.download_dir, exist_ok=True)
        files = []
        for ch in range(data.shape[1]):
            path = os.path.join(self.download_dir, f"{name}_inst{ch + 1}.wav")
            sf.write(path, data[:, ch], rate, subtype=self.SUBTYPE)
            files.append(path)

        duration = round(data.shape[0] / float(rate), 2)
        return {"stopped": True, "files": files, "rate": rate,
                "duration": duration, "overflows": overflows}

    def _do_stop_session(self, cam_id) -> dict:
        with self._lock:
            self._active.discard(cam_id)
            if self._active or self._stream is None:
                return {"stopped": False, "remaining": len(self._active),
                        "recording": self._stream is not None}
            # last camera out — take ownership of the buffer and stop the stream
            stream = self._stream
            self._stream = None
            frames = self._frames
            self._frames = []
            rate = self._rate
            name = self._session_name or time.strftime("audio_%Y%m%d_%H%M%S")
            overflows = self._overflows

        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

        return {"_finalize": True, "_frames": frames, "_name": name,
                "rate": rate, "overflows": overflows}
