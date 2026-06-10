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
