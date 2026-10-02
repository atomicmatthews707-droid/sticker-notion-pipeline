from src.trend_scout import NicheSignal
from src.trend_scout.ranker import rank


def sig(name, source, score):
    return NicheSignal(name=name, source=source, score=score)


def test_orders_by_weighted_z_score():
    signals = [sig("a", "etsy", 10), sig("b", "etsy", 5), sig("c", "etsy", 1)]
    assert [s.name for s in rank(signals)] == ["a", "b", "c"]


def test_sources_are_normalised_independently():
    # Etsy numbers are huge, Reddit numbers tiny; z-scoring must stop Etsy swamping everything.
    signals = [sig("e1", "etsy", 9000), sig("e2", "etsy", 100), sig("r1", "reddit", 50), sig("r2", "reddit", 1)]
    top = [s.name for s in rank(signals, weights={"etsy": 1.0, "reddit": 1.0}, top_n=2)]
    assert set(top) == {"e1", "r1"}


def test_same_niche_from_two_sources_accumulates():
    signals = [sig("a", "etsy", 10), sig("b", "etsy", 9), sig("c", "etsy", 1),
               sig("a", "reddit", 10), sig("d", "reddit", 9), sig("e", "reddit", 1)]
    assert rank(signals)[0].name == "a"


def test_banned_and_recent_niches_are_dropped():
    signals = [sig("pokemon pack", "etsy", 99), sig("fresh idea", "etsy", 50), sig("old idea", "etsy", 40), sig("x", "etsy", 1)]
    names = [s.name for s in rank(signals, recent_check=lambda n: n == "old idea")]
    assert "pokemon pack" not in names and "old idea" not in names and "fresh idea" in names


def test_seaweed_is_not_banned_as_weed():
    assert [s.name for s in rank([sig("seaweed planner", "etsy", 1), sig("y", "etsy", 0)])][0] == "seaweed planner"


def test_top_n_defaults_from_config_and_empty_input():
    signals = [sig(f"n{i}", "etsy", i) for i in range(20)]
    assert len(rank(signals)) == 5  # config trend_scout.top_n
    assert rank([]) == []


def test_scores_survive_for_the_queue():
    out = rank([sig("a", "etsy", 10), sig("b", "etsy", 0)])
    assert out[0].score > out[1].score and out[0].source == "etsy"
