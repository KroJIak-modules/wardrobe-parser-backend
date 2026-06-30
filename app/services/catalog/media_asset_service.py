from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
import mimetypes
import secrets
import json
import shutil
import subprocess
import tempfile

from fastapi import UploadFile
from PIL import Image
import requests
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models import ImageAsset


@dataclass(frozen=True)
class MediaMeta:
    mime_type: str
    media_kind: str
    width_px: int | None
    height_px: int | None
    has_audio: bool = False


class MediaAssetService:
    _ROOT_DIR = Path(__file__).resolve().parents[3] / "uploads"
    _SHOWCASE_IMAGE_MIME_TYPES = {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/svg+xml",
    }
    _SHOWCASE_VIDEO_MIME_TYPES = {
        "video/mp4",
        "video/webm",
        "video/quicktime",
    }

    def __init__(self, db: Session) -> None:
        self.db = db

    def _ensure_dir(self, scope: str) -> Path:
        target = self._ROOT_DIR / scope
        target.mkdir(parents=True, exist_ok=True)
        return target

    @staticmethod
    def normalize_scope(scope: str | None) -> str:
        normalized = str(scope or "").strip()
        return normalized or "assets"

    @classmethod
    def asset_scope(cls, asset: ImageAsset | None) -> str:
        if asset is None:
            return "assets"
        explicit_scope = str(getattr(asset, "scope", "") or "").strip()
        if explicit_scope:
            return explicit_scope
        storage_key = str(getattr(asset, "storage_key", "") or "").strip()
        if "/" in storage_key:
            prefix = storage_key.split("/", 1)[0].strip()
            if prefix:
                return prefix
        return "assets"

    @staticmethod
    def _svg_meta(content: bytes, *, file_name: str = "") -> MediaMeta | None:
        normalized_name = str(file_name or "").strip().lower()
        stripped = content.lstrip()
        if normalized_name.endswith(".svg") or stripped.startswith(b"<svg") or stripped.startswith(b"<?xml"):
            head = stripped[:512].decode("utf-8", errors="ignore").lower()
            if "<svg" in head:
                return MediaMeta(
                    mime_type="image/svg+xml",
                    media_kind="image",
                    width_px=None,
                    height_px=None,
                )
        return None

    @staticmethod
    def _image_meta(content: bytes) -> MediaMeta | None:
        mime_type = "application/octet-stream"
        width_px: int | None = None
        height_px: int | None = None
        try:
            with Image.open(BytesIO(content)) as image:
                mime_type = Image.MIME.get(image.format, mime_type)
                width_px = int(image.width)
                height_px = int(image.height)
        except Exception:
            return None
        return MediaMeta(
            mime_type=mime_type,
            media_kind="image",
            width_px=width_px,
            height_px=height_px,
        )

    @classmethod
    def _ffprobe_path(cls) -> str:
        path = shutil.which("ffprobe")
        if not path:
            raise ValidationError("Сервер пока не готов принимать видео. ffprobe не установлен.")
        return path

    @classmethod
    def _mime_from_video_probe(cls, *, file_name: str, format_name: str | None) -> str:
        guessed_mime, _ = mimetypes.guess_type(str(file_name or "").strip())
        if guessed_mime in cls._SHOWCASE_VIDEO_MIME_TYPES:
            return guessed_mime
        normalized_format = str(format_name or "").strip().lower()
        if "webm" in normalized_format:
            return "video/webm"
        if "mov" in normalized_format:
            return "video/quicktime"
        return "video/mp4"

    @classmethod
    def _video_meta(cls, content: bytes, *, file_name: str = "") -> MediaMeta | None:
        suffix = Path(str(file_name or "").strip()).suffix or ".bin"
        try:
            ffprobe_path = cls._ffprobe_path()
        except ValidationError:
            guessed_mime, _ = mimetypes.guess_type(str(file_name or "").strip())
            if not guessed_mime or not guessed_mime.startswith("video/"):
                return None
            raise
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            try:
                result = subprocess.run(
                    [
                        ffprobe_path,
                        "-v",
                        "error",
                        "-print_format",
                        "json",
                        "-show_streams",
                        "-show_format",
                        temp_file.name,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError:
                return None
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams") or []
        video_stream = next((stream for stream in streams if str(stream.get("codec_type") or "") == "video"), None)
        if video_stream is None:
            return None
        format_payload = payload.get("format") or {}
        return MediaMeta(
            mime_type=cls._mime_from_video_probe(
                file_name=file_name,
                format_name=str(format_payload.get("format_name") or ""),
            ),
            media_kind="video",
            width_px=(int(video_stream.get("width")) if video_stream.get("width") is not None else None),
            height_px=(int(video_stream.get("height")) if video_stream.get("height") is not None else None),
            has_audio=any(str(stream.get("codec_type") or "") == "audio" for stream in streams),
        )

    @classmethod
    def _detect_media_meta(cls, content: bytes, *, file_name: str = "") -> MediaMeta:
        svg_meta = cls._svg_meta(content, file_name=file_name)
        if svg_meta is not None:
            return svg_meta
        image_meta = cls._image_meta(content)
        if image_meta is not None:
            return image_meta
        video_meta = cls._video_meta(content, file_name=file_name)
        if video_meta is not None:
            return video_meta
        guessed_mime, _ = mimetypes.guess_type(str(file_name or "").strip())
        mime_type = guessed_mime or "application/octet-stream"
        return MediaMeta(
            mime_type=mime_type,
            media_kind="image" if mime_type.startswith("image/") else "video" if mime_type.startswith("video/") else "unknown",
            width_px=None,
            height_px=None,
        )

    @staticmethod
    def _safe_suffix(file_name: str, mime_type: str) -> str:
        candidate = Path(str(file_name or "").strip()).suffix.lower()
        if candidate and len(candidate) <= 10:
            return candidate
        return mimetypes.guess_extension(mime_type) or ".bin"

    @classmethod
    def _validate_showcase_media(cls, meta: MediaMeta) -> None:
        if meta.media_kind == "image":
            if meta.mime_type not in cls._SHOWCASE_IMAGE_MIME_TYPES:
                raise ValidationError("Для витрины можно загружать только JPG, PNG, WebP, GIF или SVG.")
            return
        if meta.media_kind == "video":
            if meta.mime_type not in cls._SHOWCASE_VIDEO_MIME_TYPES:
                raise ValidationError("Для витрины можно загружать только MP4, WebM или MOV.")
            if meta.has_audio:
                raise ValidationError("Видео для витрины должно быть без звука.")
            return
        raise ValidationError("Формат файла не поддерживается для витрины.")

    @staticmethod
    def media_kind_for_asset(asset: ImageAsset) -> str:
        mime_type = str(getattr(asset, "mime_type", "") or "").strip().lower()
        if mime_type.startswith("video/"):
            return "video"
        return "image"

    def validate_existing_showcase_asset(self, asset: ImageAsset) -> None:
        file_path = self.resolve_file_path(asset)
        if not file_path.exists():
            raise ValidationError("Медиафайл витрины не найден на диске.")
        content = file_path.read_bytes()
        meta = self._detect_media_meta(content, file_name=file_path.name)
        self._validate_showcase_media(meta)

    def save_bytes(self, *, scope: str, file_name: str, content: bytes) -> ImageAsset:
        normalized_scope = self.normalize_scope(scope)
        meta = self._detect_media_meta(content, file_name=file_name)
        if normalized_scope == "showcase":
            self._validate_showcase_media(meta)
        suffix = self._safe_suffix(file_name, meta.mime_type)
        digest = sha256(content).hexdigest()

        existing = (
            self.db.query(ImageAsset)
            .filter(
                ImageAsset.scope == normalized_scope,
                ImageAsset.checksum_sha256 == digest,
            )
            .one_or_none()
        )
        if existing is not None:
            if normalized_scope == "showcase":
                self.validate_existing_showcase_asset(existing)
            return existing

        scope_dir = self._ensure_dir(normalized_scope)
        stored_name = f"{secrets.token_hex(16)}{suffix}"
        relative_key = f"{normalized_scope}/{stored_name}"
        target = scope_dir / stored_name
        target.write_bytes(content)

        asset = ImageAsset(
            scope=normalized_scope,
            storage_key=relative_key,
            mime_type=meta.mime_type,
            byte_size=len(content),
            width_px=meta.width_px,
            height_px=meta.height_px,
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
