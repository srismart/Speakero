import scoring


def test_clean_session_scores_100_with_positive_drivers():
    d = scoring.compute_delivery(filler_count=0, total_words=100, wpm=130,
                                 pause_count=0, duration_seconds=60)
    assert d["score"] == 100
    labels = [x["label"] for x in d["drivers"]]
    assert "low filler usage" in labels
    assert "steady pace" in labels
    assert "good pause discipline" in labels
    assert all(x["delta"] > 0 for x in d["drivers"])


def test_filler_penalty_capped_at_30():
    d = scoring.compute_delivery(10, 100, 130, 0, 60)  # 10% rate -> cap
    assert d["score"] == 70
    neg = [x for x in d["drivers"] if x["delta"] < 0]
    assert neg[0]["delta"] == -30
    assert "filler rate" in neg[0]["label"]


def test_slow_and_rushed_pace_penalties():
    slow = scoring.compute_delivery(0, 100, 80, 0, 60)   # 30 under band -> -15
    assert slow["score"] == 85
    assert any("slow pace (80 wpm)" == x["label"] for x in slow["drivers"])
    rushed = scoring.compute_delivery(0, 100, 200, 0, 60)  # 40 over -> -20
    assert rushed["score"] == 80
    assert any("rushed pace (200 wpm)" == x["label"] for x in rushed["drivers"])


def test_pause_penalty():
    d = scoring.compute_delivery(0, 100, 130, 10, 60)  # 10/min -> capped -15
    assert d["score"] == 85
    assert any(x["label"] == "frequent long pauses" and x["delta"] == -15
               for x in d["drivers"])


def test_score_clamped_at_zero_floor():
    d = scoring.compute_delivery(50, 100, 300, 30, 60)
    assert d["score"] >= 0


def test_insufficient_sample_returns_none():
    assert scoring.compute_delivery(1, 19, 130, 0, 60) is None
    assert scoring.compute_delivery(0, 0, 0, 0, 0) is None
