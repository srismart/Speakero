from filler_detector import FillerDetector


def test_best_window_returns_text_and_audio_offsets():
    d = FillerDetector()
    d.start_session()
    d.process_words([
        {"word": "the", "start": 1.0, "end": 1.2},
        {"word": "core", "start": 1.2, "end": 1.5},
        {"word": "idea", "start": 1.5, "end": 1.9},
        {"word": "matters", "start": 1.9, "end": 2.4},
    ])
    bw = d.get_best_window()
    assert bw["text"] == "the core idea matters"
    assert bw["start"] == 1.0
    assert bw["end"] == 2.4


def test_best_window_is_none_when_no_windows():
    d = FillerDetector()
    d.start_session()
    assert d.get_best_window() is None


def test_worst_window_picks_highest_filler_density():
    d = FillerDetector()
    d.start_session()
    # clean window (no fillers)
    d.process_words([
        {"word": "we", "start": 0.0, "end": 0.2},
        {"word": "build", "start": 0.2, "end": 0.6},
        {"word": "products", "start": 0.6, "end": 1.1},
        {"word": "daily", "start": 1.1, "end": 1.5},
    ])
    # filler-heavy window: um, like, basically are fillers; "stuff" is the only word
    d.process_words([
        {"word": "um", "start": 2.0, "end": 2.2},
        {"word": "like", "start": 2.2, "end": 2.4},
        {"word": "basically", "start": 2.4, "end": 2.8},
        {"word": "stuff", "start": 2.8, "end": 3.1},
    ])
    ww = d.get_worst_window()
    assert ww["start"] == 2.0
    assert ww["end"] == 3.1
    assert ww["text"] == "stuff"


def test_worst_window_ignores_trivial_and_clean_windows():
    d = FillerDetector()
    d.start_session()
    # a clean window with no fillers -> not a valid "worst"
    d.process_words([
        {"word": "hello", "start": 0.0, "end": 0.5},
        {"word": "world", "start": 0.5, "end": 1.0},
        {"word": "again", "start": 1.0, "end": 1.4},
        {"word": "now", "start": 1.4, "end": 1.8},
    ])
    assert d.get_worst_window() is None


def test_replay_windows_single_clean_window_has_no_worst():
    d = FillerDetector()
    d.start_session()
    d.process_words([
        {"word": "hello", "start": 0.0, "end": 0.5},
        {"word": "world", "start": 0.5, "end": 1.0},
        {"word": "again", "start": 1.0, "end": 1.4},
        {"word": "now", "start": 1.4, "end": 1.8},
    ])
    rw = d.get_replay_windows()
    assert rw["best"] is not None
    assert rw["worst"] is None


def test_replay_windows_gated_on_real_timestamps():
    d = FillerDetector()
    d.start_session()
    # all-zero timestamps (offline / transcript fallback) -> not eligible
    d.process_words([
        {"word": "um", "start": 0.0, "end": 0.0},
        {"word": "like", "start": 0.0, "end": 0.0},
        {"word": "basically", "start": 0.0, "end": 0.0},
        {"word": "thing", "start": 0.0, "end": 0.0},
    ])
    rw = d.get_replay_windows()
    assert rw["best"] is None
    assert rw["worst"] is None


def test_best_window_none_when_only_filler_windows():
    # An all-filler chunk produces a window with empty text but a real span.
    # It must not surface as the "best" window (review item 1).
    d = FillerDetector()
    d.start_session()
    d.process_words([
        {"word": "um", "start": 1.0, "end": 1.2},
        {"word": "uh", "start": 1.2, "end": 1.4},
    ])
    assert d.get_best_window() is None
    assert d.get_replay_windows()["best"] is None


def test_replay_candidates_indexes_eligible_windows():
    d = FillerDetector()
    d.start_session()
    d.process_words([
        {"word": "we", "start": 0.0, "end": 0.3},
        {"word": "ship", "start": 0.3, "end": 0.7},
        {"word": "fast", "start": 0.7, "end": 1.0},
        {"word": "today", "start": 1.0, "end": 1.4},
    ])
    d.process_words([
        {"word": "hey", "start": 2.0, "end": 2.3},
        {"word": "yeah", "start": 2.3, "end": 2.6},
        {"word": "my", "start": 2.6, "end": 2.8},
        {"word": "product", "start": 2.8, "end": 3.2},
    ])
    cands = d.get_replay_candidates()
    assert [c["index"] for c in cands] == [0, 1]
    assert cands[0]["text"] == "we ship fast today"


def test_window_by_index_returns_span_or_none():
    d = FillerDetector()
    d.start_session()
    d.process_words([
        {"word": "hello", "start": 0.0, "end": 0.5},
        {"word": "world", "start": 0.5, "end": 1.0},
    ])
    w = d.get_window_by_index(0)
    assert w["text"] == "hello world"
    assert w["start"] == 0.0 and w["end"] == 1.0
    assert d.get_window_by_index(5) is None
    assert d.get_window_by_index(-1) is None
    assert d.get_window_by_index(None) is None


def test_replay_windows_uses_claude_roughest_index():
    d = FillerDetector()
    d.start_session()
    d.process_words([
        {"word": "we", "start": 0.0, "end": 0.3},
        {"word": "ship", "start": 0.3, "end": 0.7},
        {"word": "fast", "start": 0.7, "end": 1.0},
        {"word": "today", "start": 1.0, "end": 1.4},
    ])
    # rambling but no filler words — heuristic would miss it, Claude picks it
    d.process_words([
        {"word": "hey", "start": 2.0, "end": 2.3},
        {"word": "yeah", "start": 2.3, "end": 2.6},
        {"word": "my", "start": 2.6, "end": 2.8},
        {"word": "product", "start": 2.8, "end": 3.2},
    ])
    rw = d.get_replay_windows(roughest_index=1)
    assert rw["worst"]["text"] == "hey yeah my product"
    assert rw["worst"]["start"] == 2.0


def test_replay_windows_invalid_index_falls_back_to_heuristic():
    d = FillerDetector()
    d.start_session()
    # clean window (becomes best)
    d.process_words([
        {"word": "we", "start": 0.0, "end": 0.3},
        {"word": "ship", "start": 0.3, "end": 0.7},
        {"word": "fast", "start": 0.7, "end": 1.0},
        {"word": "today", "start": 1.0, "end": 1.4},
    ])
    # filler-heavy window (heuristic worst)
    d.process_words([
        {"word": "um", "start": 2.0, "end": 2.2},
        {"word": "like", "start": 2.2, "end": 2.4},
        {"word": "basically", "start": 2.4, "end": 2.8},
        {"word": "stuff", "start": 2.8, "end": 3.1},
    ])
    # invalid Claude index -> fall back to filler-density heuristic
    rw = d.get_replay_windows(roughest_index=99)
    assert rw["worst"] is not None
    assert rw["worst"]["text"] == "stuff"
