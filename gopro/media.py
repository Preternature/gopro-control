"""
GoPro Media Management Module
Download, browse, and manage media files
"""

import os
import time
import requests
from typing import Optional, List
from .connection import GoProConnection

class GoProMedia:
    """Media management for GoPro camera"""

    def __init__(self, connection: GoProConnection, download_dir: str = "downloads"):
        self.conn = connection
        self.download_dir = download_dir
        self._ensure_download_dir()

    def _ensure_download_dir(self):
        """Create download directory if it doesn't exist"""
        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir)

    def get_media_list(self) -> List[dict]:
        """Get list of all media files on camera"""
        result = self.conn.get_media_list()
        if not result:
            return []

        media_files = []

        # Parse the media list response
        for directory in result.get("media", []):
            dir_name = directory.get("d", "")
            for file_info in directory.get("fs", []):
                media_files.append({
                    "directory": dir_name,
                    "filename": file_info.get("n", ""),
                    "size": file_info.get("s", 0),
                    "creation_time": file_info.get("cre", ""),
                    "modification_time": file_info.get("mod", ""),
                    "url": f"http://{self.conn.gopro_ip}:8080/videos/DCIM/{dir_name}/{file_info.get('n', '')}"
                })

        return media_files

    def get_latest_media(self) -> Optional[dict]:
        """Get the most recent media file"""
        media_list = self.get_media_list()
        if media_list:
            return media_list[-1]
        return None

    def download_file(self, directory: str, filename: str, progress_callback=None,
                      segment: int = 16 << 20, seg_retries: int = 6) -> Optional[str]:
        """Download a media file from the camera in fixed-size segments.

        GoPro videos can be several GB and the camera stalls if you hold one HTTP
        connection open too long over USB (it serves a burst then goes silent). So we
        pull the file in short range requests (~16 MB each) that each finish before a
        stall, retrying any segment that drops. This completes reliably where a single
        long-lived download times out. Progress is reported at whole-percent steps."""
        url = f"http://{self.conn.gopro_ip}:8080/videos/DCIM/{directory}/{filename}"
        local_path = os.path.join(self.download_dir, filename)

        # Total size via a tiny range probe (Content-Range: bytes 0-0/<total>).
        total = 0
        try:
            probe = requests.get(url, headers={'Range': 'bytes=0-0'}, timeout=(10, 15))
            cr = probe.headers.get('content-range', '')
            if '/' in cr:
                total = int(cr.rsplit('/', 1)[-1])
            else:
                total = int(probe.headers.get('content-length', 0))
            probe.close()
        except requests.exceptions.RequestException as e:
            print(f"[media] {filename}: size probe failed ({e})")
            return None
        if not total:
            print(f"[media] {filename}: could not determine size")
            return None

        last_pct = -1
        pos = 0
        try:
            with open(local_path, 'wb') as f:
                while pos < total:
                    end = min(pos + segment, total) - 1
                    data = None
                    for attempt in range(seg_retries):
                        try:
                            r = requests.get(url, headers={'Range': f'bytes={pos}-{end}'},
                                             timeout=(10, 30))
                            if r.status_code not in (200, 206):
                                time.sleep(0.5)
                                continue
                            data = r.content
                            break
                        except requests.exceptions.RequestException:
                            time.sleep(0.8)  # transient stall — retry the same segment
                    if not data:
                        print(f"[media] {filename}: segment at {pos}/{total} failed after "
                              f"{seg_retries} tries")
                        return None
                    f.write(data)
                    pos += len(data)
                    if progress_callback:
                        pct = int(pos / total * 100)
                        if pct != last_pct:
                            last_pct = pct
                            progress_callback(pct)
        except OSError as e:
            print(f"[media] {filename}: write error: {e}")
            return None

        print(f"[media] {filename}: downloaded {pos}/{total} bytes")
        return local_path

    def download_latest(self, progress_callback=None) -> Optional[str]:
        """Download the most recent media file"""
        latest = self.get_latest_media()
        if latest:
            return self.download_file(
                latest["directory"],
                latest["filename"],
                progress_callback
            )
        return None

    def get_thumbnail_url(self, directory: str, filename: str) -> str:
        """Get URL for media thumbnail"""
        return f"http://{self.conn.gopro_ip}:8080/gopro/media/thumbnail?path={directory}/{filename}"

    def get_screennail_url(self, directory: str, filename: str) -> str:
        """Get URL for larger preview image"""
        return f"http://{self.conn.gopro_ip}:8080/gopro/media/screennail?path={directory}/{filename}"

    def delete_file(self, directory: str, filename: str) -> bool:
        """Delete a media file from the camera"""
        result = self.conn.send_command(
            "/gopro/media/delete/file",
            {"path": f"{directory}/{filename}"}
        )
        return result is not None

    def delete_all(self) -> bool:
        """Delete all media from the camera"""
        result = self.conn.send_command("/gopro/media/delete/all")
        return result is not None

    def get_local_files(self) -> List[str]:
        """Get list of downloaded files"""
        if os.path.exists(self.download_dir):
            return os.listdir(self.download_dir)
        return []
