"""
GoPro Media Management Module
Download, browse, and manage media files
"""

import os
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

    def download_file(self, directory: str, filename: str,
                      progress_callback=None) -> Optional[str]:
        """Download a specific media file from the camera"""
        url = f"http://{self.conn.gopro_ip}:8080/videos/DCIM/{directory}/{filename}"
        local_path = os.path.join(self.download_dir, filename)

        try:
            response = requests.get(url, stream=True, timeout=30)

            if response.status_code != 200:
                print(f"Failed to download: {response.status_code}")
                return None

            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0

            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size:
                            progress = (downloaded / total_size) * 100
                            progress_callback(progress)

            return local_path

        except requests.exceptions.RequestException as e:
            print(f"Download error: {e}")
            return None

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
