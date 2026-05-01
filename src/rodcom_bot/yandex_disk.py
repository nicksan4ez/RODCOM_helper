from __future__ import annotations

import json
import posixpath
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class YandexDiskClient:
    def __init__(self, token: str):
        self.token = token

    def upload_file(self, local_path: str | Path, disk_path: str, overwrite: bool = True) -> None:
        self._ensure_parent_dirs(disk_path)
        upload_url = self._get_upload_url(disk_path, overwrite)
        request = urllib.request.Request(
            upload_url,
            data=Path(local_path).read_bytes(),
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.status not in {200, 201, 202}:
                    raise RuntimeError(f"Yandex Disk upload failed with status {response.status}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(_http_error_message("Yandex Disk upload failed", exc)) from exc

    def _get_upload_url(self, disk_path: str, overwrite: bool) -> str:
        query = urllib.parse.urlencode({"path": disk_path, "overwrite": str(overwrite).lower()})
        request = urllib.request.Request(
            f"https://cloud-api.yandex.net/v1/disk/resources/upload?{query}",
            headers={"Authorization": f"OAuth {self.token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(_http_error_message("Could not get Yandex Disk upload URL", exc)) from exc
        href = payload.get("href")
        if not href:
            raise RuntimeError("Yandex Disk did not return an upload URL")
        return str(href)

    def _ensure_parent_dirs(self, disk_path: str) -> None:
        parent = posixpath.dirname(disk_path.rstrip("/"))
        if not parent or parent == "/":
            return
        current = ""
        for part in [part for part in parent.split("/") if part]:
            current += "/" + part
            self._create_dir(current)

    def _create_dir(self, disk_path: str) -> None:
        query = urllib.parse.urlencode({"path": disk_path})
        request = urllib.request.Request(
            f"https://cloud-api.yandex.net/v1/disk/resources?{query}",
            headers={"Authorization": f"OAuth {self.token}"},
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status not in {200, 201, 202, 409}:
                    raise RuntimeError(f"Could not create Yandex Disk folder: HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return
            raise RuntimeError(_http_error_message("Could not create Yandex Disk folder", exc)) from exc


def _http_error_message(prefix: str, exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8")
    except Exception:
        body = ""
    if not body:
        return f"{prefix}: HTTP {exc.code}"
    return f"{prefix}: HTTP {exc.code}: {body[:500]}"
