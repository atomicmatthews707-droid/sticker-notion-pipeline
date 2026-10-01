import pytest

from src.publisher import PublishUnavailable, listing_writer
from src.shared.compliance import AI_DISCLOSURE, ensure_disclosure, has_disclosure
from src.shared.gemini_client import GeminiError

GOOD = {
    "title": "Autumn Cozy Digital Stickers for Goodnotes and Planners",
    "description": "x" * 200,
    "tags": [f"autumn tag {i}" for i in range(13)],
    "gumroad_title": "Autumn Cozy Stickers",
    "gumroad_description": "Digital stickers.",
}


class FakeLLM:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def generate_json(self, prompt, system=None, **kw):
        self.prompts.append(prompt)
        return dict(self.replies.pop(0) if len(self.replies) > 1 else self.replies[0])


def test_price_tiers():
    assert [listing_writer.price_for_count(n) for n in (5, 10, 24, 25, 39, 40, 80)] == [5.0, 5.0, 5.0, 8.0, 8.0, 12.0, 12.0]


def test_tags_are_normalised_to_etsy_rules():
    tags = listing_writer.normalize_tags(["Cute Stickers!", "cute stickers", "  Goodnotes  ", "x" * 25, "", "a-b"])
    assert tags == ["cute stickers", "goodnotes", "ab"]
    assert len(listing_writer.normalize_tags([f"t{i}" for i in range(30)])) == 13


def test_valid_copy_gets_disclosure_price_and_count(tmp_path):
    out = listing_writer.write_listing("autumn", ["a"] * 5, client=FakeLLM(GOOD))
    assert out["description"].startswith(AI_DISCLOSURE) and out["price_usd"] == 5.0 and out["sticker_count"] == 5
    assert len(out["tags"]) == 13


def test_disclosure_is_not_duplicated_when_the_model_included_it():
    good = dict(GOOD, description=f"{AI_DISCLOSURE}\n\n" + "x" * 200)
    out = listing_writer.write_listing("autumn", ["a"] * 5, client=FakeLLM(good))
    assert out["description"].count(AI_DISCLOSURE) == 1


def test_bad_copy_is_sent_back_with_the_problems_listed():
    bad = dict(GOOD, title="t" * 200, tags=["one"])
    llm = FakeLLM(bad, GOOD)
    out = listing_writer.write_listing("autumn", ["a"] * 5, client=llm)
    assert out["title"] == GOOD["title"]
    assert "title must be" in llm.prompts[1] and "13 distinct tags" in llm.prompts[1]


def test_banned_terms_are_rejected_even_inside_tags():
    bad = dict(GOOD, tags=GOOD["tags"][:12] + ["pokemon"])
    with pytest.raises(GeminiError, match="banned"):
        listing_writer.write_listing("autumn", ["a"] * 5, client=FakeLLM(bad))


def test_gives_up_after_max_attempts():
    llm = FakeLLM({"title": ""})
    with pytest.raises(GeminiError, match="after 3 attempts"):
        listing_writer.write_listing("autumn", ["a"] * 5, client=llm)
    assert len(llm.prompts) == 3


def test_compliance_helpers():
    assert not has_disclosure("hello") and has_disclosure(AI_DISCLOSURE.upper())
    assert ensure_disclosure("hello").startswith(AI_DISCLOSURE)


def test_kit_contains_everything_needed_to_upload(tmp_path):
    from src.publisher.kit import write_kit

    zp = tmp_path / "p.zip"
    zp.write_bytes(b"z")
    mock = tmp_path / "mockup_1_hero.jpg"
    mock.write_bytes(b"j")
    listing = dict(GOOD, description=ensure_disclosure("x" * 200), price_usd=5.0, sticker_count=5)
    kit = write_kit("autumn", listing, str(zp), [str(mock)], tmp_path / "pack")
    files = {p.name for p in __import__("pathlib").Path(kit).rglob("*") if p.is_file()}
    assert {"listing.json", "etsy_listing.txt", "gumroad_listing.txt", "CHECKLIST.md", "sticker_pack.zip", "mockup_1_hero.jpg"} <= files
    etsy = (__import__("pathlib").Path(kit) / "etsy_listing.txt").read_text()
    assert "5.00" in etsy and AI_DISCLOSURE in etsy and "autumn tag 0" in etsy


# ── publishers ───────────────────────────────────────────────────────────────────

def test_gumroad_never_pretends():
    from src.publisher import gumroad_lister

    assert gumroad_lister.is_enabled() is False
    with pytest.raises(PublishUnavailable):
        gumroad_lister.create_product({}, "z")
