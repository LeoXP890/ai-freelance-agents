"""Fotos de produto enviadas pelo app (COM-001): reprocessadas, guardadas no Postgres e servidas por URL pública.

Toda imagem é DECODIFICADA e REGRAVADA em JPEG: some EXIF/GPS (fotos de celular carregam a localização), arquivos
"poliglotas" não sobrevivem e o tamanho fica sob controle. Endereço = SHA-256 do arquivo final (imutável, sem
duplicatas, cache eterno). Ficar no banco faz as fotos entrarem nos backups cifrados sem volume extra; canais
(WooCommerce, Mercado Livre) baixam pela URL pública `{public_base_url}/v1/media/<sha>.jpg`.
"""

from __future__ import annotations

import hashlib
import io
import re
import warnings

from sqlalchemy.orm import Session

from aicommerce.config import get_settings
from aicommerce.models import MediaBlob

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MIN_SIDE = 500  # Mercado Livre recomenda ≥ 500 px; abaixo disso a foto é recusada nos canais
MAX_SIDE = 2400  # maior lado após redução (qualidade de zoom sem arquivo gigante)
MAX_PIXELS = 50_000_000  # trava contra "bomba de descompressão"
ACCEPTED = {"JPEG", "PNG", "WEBP"}
NAME_RE = re.compile(r"^([0-9a-f]{64})\.jpg$")


class MediaError(ValueError):
    pass


def process(data: bytes) -> tuple[bytes, int, int]:
    """Valida e regrava como JPEG sem metadados. Retorna (bytes, largura, altura)."""
    from PIL import Image, ImageOps

    if not data:
        raise MediaError("arquivo vazio")
    if len(data) > MAX_UPLOAD_BYTES:
        raise MediaError(f"arquivo maior que {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            if img.format not in ACCEPTED:
                raise MediaError("formato não aceito (use JPEG, PNG ou WebP)")
            img.load()
    except MediaError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
        raise MediaError("imagem com dimensões absurdas (recusada por segurança)") from e
    except Exception as e:  # arquivo corrompido ou não é imagem
        raise MediaError("o arquivo não é uma imagem válida") from e
    img = ImageOps.exif_transpose(img)  # aplica a rotação do celular antes de descartar o EXIF
    if min(img.size) < MIN_SIDE:
        raise MediaError(f"imagem pequena demais ({img.width}×{img.height}); mínimo {MIN_SIDE} px no menor lado")
    if img.mode in ("RGBA", "LA", "P"):  # transparência vira fundo branco (padrão dos marketplaces)
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88, optimize=True, progressive=True)  # sem exif=…: nenhum metadado sai
    return out.getvalue(), img.width, img.height


def public_url(sha: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/v1/media/{sha}.jpg"


def is_own_url(url: str) -> bool:
    prefix = f"{get_settings().public_base_url.rstrip('/')}/v1/media/"
    return url.startswith(prefix) and bool(NAME_RE.match(url.removeprefix(prefix)))


def store(db: Session, data: bytes, *, actor: str) -> dict[str, object]:
    jpeg, w, h = process(data)
    sha = hashlib.sha256(jpeg).hexdigest()
    if db.get(MediaBlob, sha) is None:  # mesmo conteúdo = mesmo endereço (sem duplicata)
        db.add(
            MediaBlob(
                sha256=sha,
                content_type="image/jpeg",
                data=jpeg,
                width=w,
                height=h,
                size_bytes=len(jpeg),
                created_by=actor,
            )
        )
        db.flush()
    return {"url": public_url(sha), "sha256": sha, "width": w, "height": h, "bytes": len(jpeg)}


def load(db: Session, name: str) -> MediaBlob | None:
    m = NAME_RE.match(name)
    return db.get(MediaBlob, m.group(1)) if m else None
