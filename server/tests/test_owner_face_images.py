from __future__ import annotations

from io import BytesIO

from PIL import Image

from eidolon_admin_server.app.guard.owner_face_images import (
    NORMALIZED_HEIGHT,
    NORMALIZED_WIDTH,
    normalize_owner_face_image,
)


def _jpeg(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), color=(40, 80, 120))
    output = BytesIO()
    image.save(output, format="JPEG", quality=95)
    return output.getvalue()


def test_normalize_owner_face_image_emits_qvga_landscape() -> None:
    normalized = normalize_owner_face_image(_jpeg(640, 360))

    with Image.open(BytesIO(normalized)) as image:
        assert image.size == (NORMALIZED_WIDTH, NORMALIZED_HEIGHT)
        assert image.mode == "RGB"


def test_normalize_owner_face_image_emits_qvga_portrait() -> None:
    normalized = normalize_owner_face_image(_jpeg(360, 640))

    with Image.open(BytesIO(normalized)) as image:
        assert image.size == (NORMALIZED_WIDTH, NORMALIZED_HEIGHT)
