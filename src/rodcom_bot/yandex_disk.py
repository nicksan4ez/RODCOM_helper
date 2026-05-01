from __future__ import annotations

import json
import posixpath
import time
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
        data = Path(local_path).read_bytes()

        def upload_once() -> None:
            request = urllib.request.Request(upload_url, data=data, method="PUT")
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.status not in {200, 201, 202}:
                    raise RuntimeError(f"Yandex Disk upload failed with status {response.status}")

        try:
            _with_locked_retry(upload_once)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(_http_error_message("Yandex Disk upload failed", exc)) from exc

    def publish_resource(self, disk_path: str) -> str:
        self._publish_resource(disk_path)
        metadata = self._get_resource_metadata(disk_path)
        public_url = metadata.get("public_url")
        if not public_url:
            raise RuntimeError("Yandex Disk did not return a public URL")
        return str(public_url)

    def _get_upload_url(self, disk_path: str, overwrite: bool) -> str:
        query = urllib.parse.urlencode({"path": disk_path, "overwrite": str(overwrite).lower()})
        url = f"https://cloud-api.yandex.net/v1/disk/resources/upload?{query}"

        def get_once() -> dict:
            request = urllib.request.Request(
                url,
                headers={"Authorization": f"OAuth {self.token}"},
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            payload = _with_locked_retry(get_once)
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
            if not self._resource_exists(current):
                self._create_dir(current)

    def _resource_exists(self, disk_path: str) -> bool:
        def exists_once() -> bool:
            try:
                self._get_resource_metadata(disk_path)
                return True
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return False
                raise

        try:
            return _with_locked_retry(exists_once)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(_http_error_message("Could not check Yandex Disk folder", exc)) from exc

    def _create_dir(self, disk_path: str) -> None:
        query = urllib.parse.urlencode({"path": disk_path})
        url = f"https://cloud-api.yandex.net/v1/disk/resources?{query}"

        def create_once() -> None:
            request = urllib.request.Request(
                url,
                headers={"Authorization": f"OAuth {self.token}"},
                method="PUT",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status not in {200, 201, 202, 409}:
                    raise RuntimeError(f"Could not create Yandex Disk folder: HTTP {response.status}")

        try:
            _with_locked_retry(create_once)
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return
            raise RuntimeError(_http_error_message("Could not create Yandex Disk folder", exc)) from exc

    def _publish_resource(self, disk_path: str) -> None:
        query = urllib.parse.urlencode({"path": disk_path})
        url = f"https://cloud-api.yandex.net/v1/disk/resources/publish?{query}"

        def publish_once() -> None:
            request = urllib.request.Request(
                url,
                headers={"Authorization": f"OAuth {self.token}"},
                method="PUT",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status not in {200, 201, 202, 409}:
                    raise RuntimeError(f"Could not publish Yandex Disk file: HTTP {response.status}")

        try:
            _with_locked_retry(publish_once)
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return
            raise RuntimeError(_http_error_message("Could not publish Yandex Disk file", exc)) from exc

    def _get_resource_metadata(self, disk_path: str) -> dict:
        query = urllib.parse.urlencode({"path": disk_path})
        url = f"https://cloud-api.yandex.net/v1/disk/resources?{query}"

        def get_once() -> dict:
            request = urllib.request.Request(
                url,
                headers={"Authorization": f"OAuth {self.token}"},
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))

        return _with_locked_retry(get_once)


def _with_locked_retry(operation, attempts: int = 5, delay_seconds: float = 2.0):
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except urllib.error.HTTPError as exc:
            if exc.code != 423 or attempt == attempts:
                raise
            time.sleep(delay_seconds * attempt)
    return None


def _http_error_message(prefix: str, exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8")
    except Exception:
        body = ""
    if not body:
        return f"{prefix}: HTTP {exc.code}"
    return f"{prefix}: HTTP {exc.code}: {body[:500]}"
