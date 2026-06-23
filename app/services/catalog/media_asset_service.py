from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
import mimetypes
import secrets

from fastapi import UploadFile
from PIL import Image
import requests
from sqlalchemy.orm import Session

from app.models import ImageAsset


class MediaAssetService:
    _ROOT_DIR = Path(__file__).resolve().parents[3] / "uploads"

    def __init__(self, db: Session) -> None:
        self.db = db

    def _ensure_dir(self, scope: str) -> Path:
        target = self._ROOT_DIR / scope
        target.mkdir(parents=True, exist_ok=True)
        return target

    @staticmethod
    def _detect_image_meta(content: bytes, *, file_name: str = "") -> tuple[str, int | None, int | None]:
        normalized_name = str(file_name or "").strip().lower()
        stripped = content.lstrip()
        if normalized_name.endswith(".svg") or stripped.startswith(b"<svg") or stripped.startswith(b"<?xml"):
            head = stripped[:512].decode("utf-8", errors="ignore").lower()
            if "<svg" in head:
                return "image/svg+xml", None, None
        mime_type = "application/octet-stream"
        width_px: int | None = None
        height_px: int | None = None
        try:
            with Image.open(BytesIO(content)) as image:
                mime_type = Image.MIME.get(image.format, mime_type)
                width_px = int(image.width)
                height_px = int(image.height)
        except Exception:
            pass
        return mime_type, width_px, height_px

    @staticmethod
    def _safe_suffix(file_name: str, mime_type: str) -> str:
        candidate = Path(str(file_name or "").strip()).suffix.lower()
        if candidate and len(candidate) <= 10:
            return candidate
        return mimetypes.guess_extension(mime_type) or ".bin"

    def save_bytes(self, *, scope: str, file_name: str, content: bytes) -> ImageAsset:
        mime_type, width_px, height_px = self._detect_image_meta(content, file_name=file_name)
        suffix = self._safe_suffix(file_name, mime_type)
        digest = sha256(content).hexdigest()

        existing = self.db.query(ImageAsset).filter(ImageAsset.checksum_sha256 == digest).one_or_none()
        if existing is not None:
            return existing

        scope_dir = self._ensure_dir(scope)
        stored_name = f"{secrets.token_hex(16)}{suffix}"
        relative_key = f"{scope}/{stored_name}"
        target = scope_dir / stored_name
        target.write_bytes(content)

        asset = ImageAsset(
            storage_key=relative_key,
            mime_type=mime_type,
            byte_size=len(content),
            width_px=width_px,
            height_px=height_px,
            checksum_sha256=digest,
        )
        self.db.add(asset)
        self.db.flush()
        return asset

    def save_upload(self, *, scope: str, upload: UploadFile) -> ImageAsset:
        content = upload.file.read()
        if not content:
            raise ValueError("Файл пустой")
        return self.save_bytes(scope=scope, file_name=upload.filename or "upload.bin", content=content)

    def save_from_url(self, *, scope: str, url: str) -> ImageAsset:
        response = requests.get(str(url).strip(), timeout=(5, 30))
        response.raise_for_status()
        content = response.content
        if not content:
            raise ValueError("Пустой ответ по URL")
        path = urlparse(str(url).strip()).path or ""
        file_name = Path(path).name or "remote.bin"
        return self.save_bytes(scope=scope, file_name=file_name, content=content)

    def resolve_file_path(self, asset: ImageAsset) -> Path:
        return self._ROOT_DIR / str(asset.storage_key or "").strip()
