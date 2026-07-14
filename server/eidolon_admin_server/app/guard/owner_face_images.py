"""Privacy-preserving normalization for retained Owner Face references."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_RAW_IMAGE_BYTES = 8 * 1024 * 1024
MAX_NORMALIZED_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_EDGE_PIXELS = 640


class OwnerFaceImageError(ValueError):
    pass


def normalize_owner_face_image(raw: bytes) -> bytes:
    """Decode once, orient pixels, resize, and emit metadata-free RGB JPEG."""
    if not raw:
        raise OwnerFaceImageError("owner face image is empty")
    if len(raw) > MAX_RAW_IMAGE_BYTES:
        raise OwnerFaceImageError("owner face image exceeds 8 MiB upload limit")
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(BytesIO(raw)) as source:
            source.verify()
        with Image.open(BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            if image.width < 160 or image.height < 160:
                raise OwnerFaceImageError("owner face image must be at least 160x160")
            image.thumbnail((MAX_EDGE_PIXELS, MAX_EDGE_PIXELS), Image.Resampling.LANCZOS)
            rgb = image.convert("RGB")
            output = BytesIO()
            rgb.save(
                output,
                format="JPEG",
                quality=90,
                optimize=True,
                progressive=False,
                exif=b"",
                icc_profile=None,
            )
            normalized = output.getvalue()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise OwnerFaceImageError("owner face upload is not a safe decodable image") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit
    if len(normalized) > MAX_NORMALIZED_IMAGE_BYTES:
        raise OwnerFaceImageError("normalized owner face image exceeds 4 MiB")
    return normalized
