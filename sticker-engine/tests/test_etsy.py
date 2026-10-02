import pytest

from src.publisher import PublishUnavailable, etsy_lister
from src.shared.compliance import AI_DISCLOSURE
from src.storage import db

LISTING = {"title": "T", "description": "D" * 200, "tags": [f"t{i}" for i in range(15)], "price_usd": 5.0}
ENV = {"ETSY_ENABLED": "1", "ETSY_API_KEY": "k", "ETSY_OAUTH_TOKEN": "tok", "ETSY_SHOP_ID": "99", "ETSY_TAXONOMY_ID": "1234"}


@pytest.fixture
def enabled(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


def test_disabled_by_default_even_with_credentials(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ETSY_ENABLED")
    assert not etsy_lister.is_enabled()
    with pytest.raises(PublishUnavailable, match="disabled"):
        etsy_lister.create_listing(LISTING, "z.zip")


def test_needs_all_credentials(monkeypatch):
    monkeypatch.setenv("ETSY_ENABLED", "1")
    assert not etsy_lister.is_enabled()


def test_payload_uses_current_required_fields(enabled):
    p = etsy_lister.build_payload(LISTING)
    assert p["when_made"] == "2020_2026" and p["is_supply"] is False and p["type"] == "download"
    assert p["taxonomy_id"] == 1234 and p["who_made"] == "i_did"
    assert "is_digital" not in p and "state" not in p
    assert p["description"].startswith(AI_DISCLOSURE)                # added even though the copy lacked it
    assert p["tags"] == ",".join(f"t{i}" for i in range(13))        # capped at 13


def test_taxonomy_id_is_never_guessed(enabled, monkeypatch):
    monkeypatch.delenv("ETSY_TAXONOMY_ID")
    with pytest.raises(PublishUnavailable, match="taxonomy"):
        etsy_lister.build_payload(LISTING)


def test_oversize_pack_is_refused_before_any_request(enabled, tmp_path, monkeypatch):
    big = tmp_path / "big.zip"
    big.write_bytes(b"0" * 21_000_000)
    monkeypatch.setattr(etsy_lister.httpx, "post", lambda *a, **k: pytest.fail("no request expected"))
    with pytest.raises(PublishUnavailable, match="20 MB"):
        etsy_lister.create_listing(LISTING, str(big))


def test_new_shop_daily_cap(enabled):
    for i in range(3):
        db.save_pack_record(i, "/z")
        db.update_pack_urls(i, f"https://etsy/{i}", "")
    with pytest.raises(PublishUnavailable, match="cap reached"):
        etsy_lister._check_rate_limit()


def test_old_shop_is_not_capped(enabled, monkeypatch):
    for i in range(5):
        db.save_pack_record(i, "/z")
        db.update_pack_urls(i, f"https://etsy/{i}", "")
    monkeypatch.setenv("ETSY_SHOP_CREATED_DATE", "2020-01-01")
    etsy_lister._check_rate_limit()


def test_create_listing_flow(enabled, tmp_path, monkeypatch):
    zp, img = tmp_path / "p.zip", tmp_path / "m.jpg"
    zp.write_bytes(b"zip")
    img.write_bytes(b"jpg")
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"listing_id": 555}

    monkeypatch.setattr(etsy_lister.httpx, "post", lambda url, **kw: calls.append((url, kw)) or Resp())
    url = etsy_lister.create_listing(LISTING, str(zp), [str(img)])
    assert url == "https://www.etsy.com/listing/555"
    assert [c[0].rsplit("/", 1)[-1] for c in calls] == ["listings", "files", "images"]
    assert calls[0][1]["headers"]["Authorization"] == "Bearer tok" and calls[0][1]["headers"]["x-api-key"] == "k"


def test_unquoted_yaml_value_is_caught(enabled, monkeypatch):
    import yaml

    assert yaml.safe_load("v: 2020_2026")["v"] == 20202026  # the trap
    real = etsy_lister.config.get
    monkeypatch.setattr(etsy_lister.config, "get", lambda k, d=None: 20202026 if k == "listings.etsy_when_made" else real(k, d))
    with pytest.raises(PublishUnavailable, match="quote it"):
        etsy_lister.build_payload(LISTING)
