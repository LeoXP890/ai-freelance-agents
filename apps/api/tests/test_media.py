"""Fotos de produto enviadas pelo app (COM-001): regravadas sem metadados (GPS), limites de tamanho/dimensão,
endereço por SHA-256, URL pública que os canais baixam e uso direto como mídia do produto."""

from __future__ import annotations

import hashlib
import io

from PIL import Image

from aicommerce.commerce import media
from tests.test_suppliers import mk_product


def photo(w=800, h=600, fmt="JPEG", mode="RGB", gps=True) -> bytes:
    img = Image.new(mode, (w, h), (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30))
    buf = io.BytesIO()
    if gps and fmt == "JPEG":
        exif = Image.Exif()
        exif[0x010F] = "CameraCo"  # Make
        exif[0x0112] = 6  # Orientation: girar 90° (foto de celular em pé)
        exif.get_ifd(0x8825)[2] = (23.0, 33.0, 0.0)  # GPSLatitude
        img.save(buf, fmt, exif=exif.tobytes())
    else:
        img.save(buf, fmt)
    return buf.getvalue()


def test_reencode_strips_exif_and_gps_and_applies_rotation():
    raw = photo(800, 600)
    assert b"CameraCo" in raw
    out, w, h = media.process(raw)
    assert (w, h) == (600, 800)  # rotação do EXIF aplicada antes de descartá-lo
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG" and not img.getexif() and b"CameraCo" not in out


def test_transparent_png_gets_white_background_and_big_images_are_reduced():
    out, w, h = media.process(photo(3000, 1500, "PNG", "RGBA", gps=False))
    assert (w, h) == (2400, 1200)
    assert Image.open(io.BytesIO(out)).mode == "RGB"


def test_rejects_small_non_image_unsupported_and_oversized():
    for data, msg in (
        (photo(400, 900), "pequena"),
        (b"%PDF-1.7 nada de imagem", "não é uma imagem"),
        (b"", "vazio"),
        (b"\xff" * (media.MAX_UPLOAD_BYTES + 1), "maior"),
    ):
        try:
            media.process(data)
        except media.MediaError as e:
            assert msg in str(e)
        else:
            raise AssertionError(msg)
    gif = io.BytesIO()
    Image.new("RGB", (600, 600)).save(gif, "GIF")
    try:
        media.process(gif.getvalue())
    except media.MediaError as e:
        assert "formato" in str(e)


def test_upload_serves_public_immutable_url_and_dedupes(client, auth):
    h = auth("operator")
    raw = photo()
    r = client.post("/v1/media", content=raw, headers={**h, "Content-Type": "image/jpeg"})
    assert r.status_code == 201, r.text
    body = r.json()
    path = body["url"].split("/api", 1)[1]
    assert path == f"/v1/media/{body['sha256']}.jpg" and body["url"].startswith("http://localhost:8080/api/")
    again = client.post("/v1/media", content=raw, headers={**h, "Content-Type": "image/jpeg"}).json()
    assert again["sha256"] == body["sha256"]  # mesmo conteúdo, mesmo endereço
    got = client.get(path)  # público: sem token (os canais baixam)
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
    assert "immutable" in got.headers["cache-control"] and hashlib.sha256(got.content).hexdigest() == body["sha256"]
    assert client.get("/v1/media/" + "0" * 64 + ".jpg").status_code == 404
    assert client.get("/v1/media/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_upload_requires_catalog_permission_and_rejects_bad_files(client, auth):
    raw = photo()
    assert client.post("/v1/media", content=raw).status_code == 401
    assert client.post("/v1/media", content=raw, headers=auth("finance")).status_code == 403
    r = client.post("/v1/media", content=b"not an image", headers=auth("operator"))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_IMAGE"


def test_uploaded_photo_is_accepted_as_product_media(client, auth):
    h = auth("operator")
    url = client.post("/v1/media", content=photo(), headers=h).json()["url"]
    prod = mk_product(client, h)
    r = client.put(
        f"/v1/products/{prod['id']}",
        json={"expected_version": prod["version"], "media": [{"url": url, "alt": "Garrafa vermelha de frente"}]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["images"] == [url]
    bad = client.put(
        f"/v1/products/{prod['id']}",
        json={"expected_version": r.json()["version"], "media": [{"url": url.replace(".jpg", ".exe"), "alt": "xxx"}]},
        headers=h,
    )
    assert bad.status_code in (400, 409, 422)
