from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageFile

from app.core.database import SessionLocal
from app.models import ImageAsset
from app.services.catalog.media_asset_service import MediaAssetService


@pytest.fixture(autouse=True)
def _isolated_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(MediaAssetService, "_ROOT_DIR", tmp_path)


def _make_png_bytes(width: int = 1200, height: int = 800) -> bytes:
    image = Image.new("RGBA", (width, height), (12, 34, 56, 255))
    for x in range(0, width, 7):
        for y in range(0, height, 7):
            image.putpixel((x, y), (200, 180, 160, 255))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _make_svg_bytes() -> bytes:
    return b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100"/></svg>'


def _create_asset(service: MediaAssetService, *, name: str, content: bytes) -> ImageAsset:
    return service.save_bytes(scope="showcase", file_name=name, content=content)


def test_render_variant_resizes_and_reencodes_png() -> None:
    db = SessionLocal()
    try:
        service = MediaAssetService(db)
        asset = _create_asset(service, name="resize-source.png", content=_make_png_bytes())
        try:
            variant = service.render_variant_file(asset, width=300, quality=80, fmt="webp")
            assert variant is not None
            variant_path, variant_mime = variant
            assert variant_mime == "image/webp"
            assert variant_path.is_file()

            with Image.open(variant_path) as image:
                assert image.size == (300, 200)
                assert image.mode in {"RGB", "RGBA"}

            # Кэш: повторный вызов отдаёт тот же файл.
            cached = service.render_variant_file(asset, width=300, quality=80, fmt="webp")
            assert cached is not None
            assert cached[0] == variant_path
        finally:
            service.resolve_file_path(asset).unlink(missing_ok=True)
            db.delete(asset)
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_render_variant_keeps_alpha_channel() -> None:
    db = SessionLocal()
    try:
        service = MediaAssetService(db)
        source = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        buffer = BytesIO()
        source.save(buffer, "PNG")
        asset = _create_asset(service, name="resize-alpha.png", content=buffer.getvalue())
        try:
            variant = service.render_variant_file(asset, width=200, quality=82, fmt="webp")
            assert variant is not None
            with Image.open(variant[0]) as image:
                assert image.mode == "RGBA"
                assert image.getchannel("A").getextrema()[0] == 0
        finally:
            service.resolve_file_path(asset).unlink(missing_ok=True)
            db.delete(asset)
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_render_variant_skips_non_raster_files() -> None:
    db = SessionLocal()
    try:
        service = MediaAssetService(db)
        asset = _create_asset(service, name="resize-vector.svg", content=_make_svg_bytes())
        try:
            assert service.render_variant_file(asset, width=100) is None
        finally:
            service.resolve_file_path(asset).unlink(missing_ok=True)
            db.delete(asset)
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_render_variant_does_not_upscale_small_source() -> None:
    db = SessionLocal()
    try:
        service = MediaAssetService(db)
        asset = _create_asset(service, name="resize-small.png", content=_make_png_bytes(120, 80))
        try:
            variant = service.render_variant_file(asset, width=1600, quality=82, fmt="webp")
            assert variant is not None
            with Image.open(variant[0]) as image:
                assert image.size == (120, 80)
        finally:
            service.resolve_file_path(asset).unlink(missing_ok=True)
            db.delete(asset)
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_render_variant_is_much_smaller_than_source_png() -> None:
    db = SessionLocal()
    try:
        service = MediaAssetService(db)
        # Шум имитирует фотографию: PNG такие картинки хранит плохо.
        content = BytesIO()
        Image.effect_noise((1600, 1000), 48).convert("RGBA").save(content, "PNG")
        asset = _create_asset(service, name="resize-weight.png", content=content.getvalue())
        try:
            variant = service.render_variant_file(asset, width=800, quality=82, fmt="webp")
            assert variant is not None
            assert variant[0].stat().st_size < len(content.getvalue()) / 4
        finally:
            service.resolve_file_path(asset).unlink(missing_ok=True)
            db.delete(asset)
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_render_variant_keeps_variants_of_other_sizes() -> None:
    db = SessionLocal()
    try:
        service = MediaAssetService(db)
        asset = _create_asset(service, name="resize-multi.png", content=_make_png_bytes())
        try:
            wide = service.render_variant_file(asset, width=600, quality=82, fmt="webp")
            narrow = service.render_variant_file(asset, width=300, quality=82, fmt="webp")
            assert wide is not None and narrow is not None
            assert wide[0] != narrow[0]
            assert wide[0].is_file()
            assert narrow[0].is_file()
        finally:
            service.resolve_file_path(asset).unlink(missing_ok=True)
            db.delete(asset)
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_render_variant_serves_cache_without_decoding_source(monkeypatch) -> None:
    db = SessionLocal()
    try:
        service = MediaAssetService(db)
        asset = _create_asset(service, name="resize-cache.png", content=_make_png_bytes())
        try:
            variant = service.render_variant_file(asset, width=300, quality=82, fmt="webp")
            assert variant is not None

            def _fail(*args, **kwargs):
                raise AssertionError("оригинал не должен декодироваться при попадании в кэш")

            monkeypatch.setattr(Image.Image, "load", _fail)
            monkeypatch.setattr(ImageFile.ImageFile, "load", _fail)
            cached = service.render_variant_file(asset, width=300, quality=82, fmt="webp")
            assert cached is not None
            assert cached[0] == variant[0]
        finally:
            monkeypatch.undo()
            service.resolve_file_path(asset).unlink(missing_ok=True)
            db.delete(asset)
            db.commit()
    finally:
        db.rollback()
        db.close()
