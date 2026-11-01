"""CJ somente leitura: token guardado em arquivo privado, lista fechada de endpoints, ritmo de 1 chamada/s."""

import json
import stat

import pytest

from aicommerce.providers.cj import CJClient, CJError


class Fake:
    def __init__(self):
        self.calls, self.now = [], [1_000_000.0]

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), body))
        if url.endswith("/authentication/getAccessToken"):
            assert body == {"apiKey": "CJ1@api@segredo"}
            return 200, {"code": 200, "result": True, "data": {"accessToken": "tok-1", "refreshToken": "ref-1"}}
        assert headers["CJ-Access-Token"] == "tok-1"
        if "/product/listV2" in url:
            return 200, {
                "code": 200,
                "data": {"content": [{"productList": [{"id": "P1", "nameEn": "Blackout", "sellPrice": "8.5"}]}]},
            }
        if "/logistic/freightCalculate" in url:
            return 200, {"code": 200, "data": [{"logisticName": "CJ BR", "logisticPrice": 3.2, "logisticAging": "3-6"}]}
        return 404, None


def client(tmp_path, fake, sleeps=None):
    return CJClient(
        "CJ1@api@segredo",
        token_file=tmp_path / "cj" / "token.json",
        send=fake,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
        clock=lambda: fake.now[0],
    )


def test_token_cached_in_private_file_and_reused(tmp_path):
    fake = Fake()
    c = client(tmp_path, fake)
    assert c.products("cortina blackout")[0]["id"] == "P1"
    tok = tmp_path / "cj" / "token.json"
    assert stat.S_IMODE(tok.stat().st_mode) == 0o600 and json.loads(tok.read_text())["access_token"] == "tok-1"
    c2 = client(tmp_path, fake)  # novo processo: reaproveita o token do arquivo
    c2.freight("V1", zip_code="80000000")
    assert sum(1 for m, u, *_ in fake.calls if u.endswith("getAccessToken")) == 1
    listing = next(u for m, u, *_ in fake.calls if "/product/listV2" in u)
    assert "countryCode=BR" in listing and "segredo" not in listing and "tok-1" not in listing


def test_only_read_only_endpoints_and_paced(tmp_path):
    fake, sleeps = Fake(), []
    c = client(tmp_path, fake, sleeps)
    with pytest.raises(CJError, match="fora da lista"):
        c._call("POST", "/shopping/order/createOrderV2", {"x": 1})  # pedido gasta dinheiro: não existe aqui
    c.products("a")
    c.products("b")
    assert sleeps and all(s <= 1.1 for s in sleeps)  # espera para respeitar 1 chamada/s


def test_errors_never_leak_key_or_token(tmp_path):
    def broken(method, url, headers, body):
        raise RuntimeError(f"falhou com {headers} {body}")

    c = CJClient("CJ1@api@segredo", send=broken, sleep=lambda s: None)
    with pytest.raises(CJError) as e:
        c.products("x")
    assert "segredo" not in str(e.value) and "RuntimeError" in str(e.value)
    with pytest.raises(CJError, match="falta"):
        CJClient(None)
