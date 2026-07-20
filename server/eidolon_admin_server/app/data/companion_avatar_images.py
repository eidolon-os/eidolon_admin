"""Normalization for companion display-face (digital-human ``cond_image``) uploads.

Unlike the Owner Face references (biometric QVGA crops), a display face is a
portrait fed to the talking-head service, so we preserve aspect ratio and only
downscale oversized uploads.  We still decode defensively, drop EXIF/ICC, and
re-encode as a metadata-free RGB JPEG so no upload metadata is retained.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_RAW_IMAGE_BYTES = 8 * 1024 * 1024
MAX_NORMALIZED_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MIN_DIMENSION = 128
MAX_DIMENSION = 1024


class CompanionAvatarImageError(ValueError):
    pass


def normalize_companion_avatar_image(raw: bytes) -> tuple[bytes, int, int]:
    """Decode, orient, downscale-if-large, and emit a metadata-free RGB JPEG.

    Returns ``(jpeg_bytes, width, height)``.  Raises
    :class:`CompanionAvatarImageError` (mapped to HTTP 422 by the router) on any
    unsafe or undersized input.
    """
    if not raw:
        raise CompanionAvatarImageError("companion avatar image is empty")
    if len(raw) > MAX_RAW_IMAGE_BYTES:
        raise CompanionAvatarImageError("companion avatar image exceeds 8 MiB upload limit")
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(BytesIO(raw)) as source:
            source.verify()
        with Image.open(BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            if image.width < MIN_DIMENSION or image.height < MIN_DIMENSION:
                raise CompanionAvatarImageError(
                    f"companion avatar image must be at least {MIN_DIMENSION}x{MIN_DIMENSION}"
                )
            longest = max(image.width, image.height)
            if longest > MAX_DIMENSION:
                scale = MAX_DIMENSION / longest
                image = image.resize(
                    (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                    resample=Image.Resampling.LANCZOS,
                )
            output = BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=90,
                optimize=True,
                progressive=False,
                exif=b"",
                icc_profile=None,
            )
            normalized = output.getvalue()
            width, height = image.width, image.height
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise CompanionAvatarImageError(
            "companion avatar upload is not a safe decodable image"
        ) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit
    if len(normalized) > MAX_NORMALIZED_IMAGE_BYTES:
        raise CompanionAvatarImageError("normalized companion avatar image exceeds 4 MiB")
    return normalized, width, height
