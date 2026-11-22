from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlparse
import logging
import mimetypes
import secrets
import json
import shutil
import subprocess
import tempfile

from fastapi import UploadFile
from PIL import Image, ImageOps
import requests
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models import ImageAsset


LOGGER = logging.getLogger(__name__)


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

    @classmethod
    def internal_media_path(cls, path: Path) -> str | None:
        """Внутренний путь файла для отдачи через nginx.

        nginx отдаёт такой файл сам, без участия приложения. Возвращаем
        None, если файл лежит вне каталога загрузок: отдать его через
        nginx тогда нельзя.
        """
        try:
            relative = Path(path).resolve().relative_to(cls._ROOT_DIR.resolve())
        except (ValueError, OSError):
            return None
        parts = [quote(part) for part in relative.parts if part not in ("", ".")]
        if not parts:
            return None
        return "/internal/site-media/" + "/".join(parts)

    # ------------------------------------------------------------------
    # Выдача оптимизированных копий для витрины
    # ------------------------------------------------------------------

    _RESIZABLE_MIME_TYPES = {
        "image/jpeg",
        "image/png",
        "image/webp",
    }
    _VARIANT_CACHE_DIR = Path(".cache") / "site-media"
    _VARIANT_MIN_WIDTH = 64
    _VARIANT_MAX_WIDTH = 4000
    _VARIANT_MIN_QUALITY = 40
    _VARIANT_MAX_QUALITY = 100
    _VARIANT_FORMATS = {"webp", "jpeg", "jpg", "png"}

    @classmethod
    def _normalize_variant_width(cls, width: int | None) -> int | None:
        if width is None:
            return None
        return max(cls._VARIANT_MIN_WIDTH, min(cls._VARIANT_MAX_WIDTH, int(width)))

    @classmethod
    def _normalize_variant_quality(cls, quality: int | None) -> int:
        if quality is None:
            return 82
        return max(cls._VARIANT_MIN_QUALITY, min(cls._VARIANT_MAX_QUALITY, int(quality)))

    @classmethod
    def _normalize_variant_format(cls, fmt: str | None, *, has_alpha: bool) -> str:
        normalized = str(fmt or "").strip().lower()
        if normalized not in cls._VARIANT_FORMATS:
            # WebP умеет альфа-канал, поэтому подходит для всех растровых картинок.
            return "webp"
        if normalized == "jpg":
            return "jpeg"
        if normalized == "jpeg" and has_alpha:
            return "jpeg"
        return normalized

    def _variant_cache_path(
        self,
        *,
        asset: ImageAsset,
        source_path: Path,
        width: int | None,
        quality: int,
        fmt: str,
    ) -> Path:
        try:
            stat = source_path.stat()
            source_signature = f"{int(stat.st_mtime)}-{stat.st_size}"
        except OSError:
            source_signature = "missing"
        return (
            self._ROOT_DIR
            / self._VARIANT_CACHE_DIR
            / f"{int(asset.id)}-w{width or 0}-q{quality}.{fmt}-{source_signature}"
        )

    @staticmethod
    def _encode_variant(image: Image.Image, *, fmt: str, quality: int) -> bytes:
        buffer = BytesIO()
        if fmt == "webp":
            # method=2 — заметно быстрее method=4 при том же размере файла.
            image.save(buffer, "WEBP", quality=quality, method=2)
        elif fmt == "jpeg":
            if image.mode in {"RGBA", "LA", "P"}:
                background = Image.new("RGB", image.size, (255, 255, 255))
                converted = image.convert("RGBA")
                background.paste(converted, mask=converted.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            image.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True)
        else:
            image.save(buffer, "PNG", optimize=True)
        return buffer.getvalue()

    @staticmethod
    def _oriented_size(opened: Image.Image) -> tuple[int, int]:
        """Размер исходника с учётом EXIF-поворота.

        Достаточно заголовка файла, поэтому ради вычисления ключа кэша
        картинку декодировать не нужно.
        """
        width, height = opened.size
        try:
            orientation = int(opened.getexif().get(0x0112, 1) or 1)
        except Exception:  # pragma: no cover - битый EXIF
            orientation = 1
        if orientation in {5, 6, 7, 8}:
            return height, width
        return width, height

    def render_variant_file(
        self,
        asset: ImageAsset,
        *,
        width: int | None = None,
        quality: int | None = None,
        fmt: str | None = None,
    ) -> tuple[Path, str] | None:
        """Готовит оптимизированную копию файла.

        Оригинал не меняется. Возвращает (путь, mime-тип) либо None, если
        ресайз неприменим (SVG, анимированный GIF, отсутствующий файл).
        """
        mime_type = str(asset.mime_type or "").strip().lower()
        if mime_type not in self._RESIZABLE_MIME_TYPES:
            return None

        source_path = self.resolve_file_path(asset)
        if not source_path.is_file():
            return None

        try:
            with Image.open(source_path) as opened:
                if getattr(opened, "is_animated", False):
                    return None
                source_width, source_height = self._oriented_size(opened)
                target_width = self._normalize_variant_width(width)
                normalized_quality = self._normalize_variant_quality(quality)
                normalized_format = self._normalize_variant_format(fmt, has_alpha=False)
                if target_width and target_width > source_width:
                    # Увеличивать нельзя: это потеря качества, а не экономия.
                    target_width = source_width

                cache_path = self._variant_cache_path(
                    asset=asset,
                    source_path=source_path,
                    width=target_width,
                    quality=normalized_quality,
                    fmt=normalized_format,
                )
                if cache_path.is_file() and cache_path.stat().st_size > 0:
                    # Копия уже готова: декодировать и уменьшать оригинал
                    # не нужно, иначе результат всё равно будет выброшен.
                    return cache_path, self._variant_mime_type(normalized_format)

                opened.load()
                image = ImageOps.exif_transpose(opened)
                if image is None:
                    return None
                has_alpha = image.mode in {"RGBA", "LA", "P"} and (
                    image.mode == "P" or image.getchannel("A").getextrema()[0] < 255
                )
                normalized_format = self._normalize_variant_format(fmt, has_alpha=has_alpha)

                if target_width and target_width < source_width:
                    # thumbnail с reducing_gap уменьшает изображение быстрым
                    # JPEG-ресайзом, а не перебором всех пикселей LANCZOS.
                    target_height = max(1, round(source_height * target_width / source_width))
                    image.thumbnail((target_width, target_height), Image.LANCZOS, reducing_gap=2.0)

                content = self._encode_variant(image, fmt=normalized_format, quality=normalized_quality)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = cache_path.with_suffix(f"{cache_path.suffix}.tmp")
                temp_path.write_bytes(content)
                temp_path.replace(cache_path)
                self._cleanup_stale_variants(asset=asset, keep_path=cache_path)
                return cache_path, self._variant_mime_type(normalized_format)
        except (OSError, ValueError):
            LOGGER.exception("Failed to render media variant for asset %s", asset.id)
            return None

    def _cleanup_stale_variants(self, *, asset: ImageAsset, keep_path: Path) -> None:
        """Удаляет варианты ассета, устаревшие после перезаливки файла.

        Подпись файла в кэше заканчивается отпечатком исходника, поэтому
        варианты других размеров и качеств не затрагиваются.
        """
        cache_dir = self._ROOT_DIR / self._VARIANT_CACHE_DIR
        pattern = keep_path.name.rsplit("-", 1)[0] + "-*"
        try:
            candidates = list(cache_dir.glob(pattern))
        except OSError:
            return
        for candidate in candidates:
            if candidate == keep_path or not candidate.is_file():
                continue
            try:
                candidate.unlink()
            except OSError:
                continue

    @staticmethod
    def _variant_mime_type(fmt: str) -> str:
        if fmt == "webp":
            return "image/webp"
        if fmt == "jpeg":
            return "image/jpeg"
        return "image/png"
