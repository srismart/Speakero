import time
from typing import List, Dict, Any, Optional

FILLER_WORDS = {"ah", "um", "uh", "like", "basically", "right", "so", "hmmm", "well", "you know", "actually", "literally", "just", "kind of", "sort of", "ah"}
FILLER_PHRASES = ["you know"]

PAUSE_THRESHOLD_SECONDS = 2.0
STREAK_WINDOW_SECONDS = 10.0
STREAK_THRESHOLD = 3


class FillerDetector:
    def __init__(self):
        self.filler_count = 0
        self.pause_count = 0
        self.total_words = 0
        self.filler_breakdown: Dict[str, int] = {}
        self.session_start: Optional[float] = None
        self.last_word_end: Optional[float] = None
        self._transcript_words: List[str] = []
        self._filler_timestamps: List[float] = []
        self._word_windows: List[dict] = []

    def start_session(self):
        self.session_start = time.time()
        self.filler_count = 0
        self.pause_count = 0
        self.total_words = 0
        self.filler_breakdown = {}
        self.last_word_end = None
        self._transcript_words = []
        self._filler_timestamps = []
        self._word_windows = []

    def process_words(self, words: List[Dict[str, Any]], epoch: int = 0) -> Dict[str, Any]:
        new_fillers: List[str] = []
        new_pauses: int = 0
        chunk_words: List[str] = []
        chunk_fillers: List[str] = []
        chunk_t = time.time()

        i = 0
        while i < len(words):
            word_obj = words[i]
            # Pulse tokens can carry leading/trailing whitespace (chunk-initial
            # words arrive as e.g. " um") — strip it before punctuation.
            word = word_obj.get("word", "").strip().lower().strip(".,!?;:")
            start = word_obj.get("start", 0.0)
            end = word_obj.get("end", start)

            if self.last_word_end is not None:
                gap = start - self.last_word_end
                if gap >= PAUSE_THRESHOLD_SECONDS:
                    self.pause_count += 1
                    new_pauses += 1

            if i + 1 < len(words):
                next_word = words[i + 1].get("word", "").strip().lower().strip(".,!?;:")
                phrase = f"{word} {next_word}"
                if phrase in FILLER_PHRASES:
                    now = time.time()
                    self.filler_count += 1
                    self.filler_breakdown[phrase] = self.filler_breakdown.get(phrase, 0) + 1
                    new_fillers.append(phrase)
                    chunk_fillers.append(phrase)
                    self._filler_timestamps.append(now)
                    self.last_word_end = words[i + 1].get("end", end)
                    self.total_words += 2
                    self._transcript_words.append(phrase)
                    chunk_words.append(phrase)
                    i += 2
                    continue

            if word in FILLER_WORDS:
                now = time.time()
                self.filler_count += 1
                self.filler_breakdown[word] = self.filler_breakdown.get(word, 0) + 1
                new_fillers.append(word)
                chunk_fillers.append(word)
                self._filler_timestamps.append(now)
            elif word:
                self._transcript_words.append(word)
                chunk_words.append(word)

            if word:
                self.total_words += 1

            self.last_word_end = end
            i += 1

        # Record this chunk for highlight reel
        chunk_text = " ".join(chunk_words)
        if chunk_text.strip() or chunk_fillers:
            starts = [w.get("start", 0.0) for w in words]
            ends = [w.get("end", w.get("start", 0.0)) for w in words]
            self._word_windows.append({
                "text": chunk_text,
                "t": chunk_t,
                "fillers": list(chunk_fillers),
                "words": list(chunk_words),
                "audio_start": min(starts) if starts else 0.0,
                "audio_end": max(ends) if ends else 0.0,
                # Pulse timestamps reset to 0 on every stream (re)connect; the
                # epoch lets the client offset into its continuous PCM buffer.
                "epoch": epoch,
            })

        # Streak detection: count fillers in the last STREAK_WINDOW_SECONDS
        now = time.time()
        recent_fillers = [ts for ts in self._filler_timestamps if now - ts <= STREAK_WINDOW_SECONDS]
        streak = len(recent_fillers) >= STREAK_THRESHOLD

        return {
            "new_fillers": new_fillers,
            "new_pauses": new_pauses,
            "streak": streak,
            "stats": self.get_stats(),
        }

    def get_stats(self) -> Dict[str, Any]:
        elapsed = time.time() - self.session_start if self.session_start else 0
        wpm = int((self.total_words / elapsed) * 60) if elapsed > 5 else 0
        return {
            "fillerCount": self.filler_count,
            "pauseCount": self.pause_count,
            "wpm": wpm,
            "fillerBreakdown": self.filler_breakdown,
        }

    def get_transcript(self) -> str:
        return " ".join(self._transcript_words)

    def get_best_window(self) -> dict | None:
        """Return the best-delivery window: most words, lowest filler ratio.

        Returns {"text", "start", "end"} or None if no windows recorded.
        """
        if not self._word_windows:
            return None

        def score(w: dict) -> float:
            word_count = len(w["words"])
            filler_count = len(w["fillers"])
            if word_count == 0:
                return -1.0
            filler_ratio = filler_count / (word_count + filler_count)
            return word_count * (1.0 - filler_ratio)

        best = max(self._word_windows, key=score)
        if len(best["words"]) == 0:
            # Only all-filler windows exist — none is a real "best" delivery.
            return None
        return {"text": best["text"], "start": best["audio_start"], "end": best["audio_end"],
                "epoch": best.get("epoch", 0)}

    def get_worst_window(self) -> dict | None:
        """Return the roughest window: highest filler density.

        Requires at least 4 tokens (words + fillers) so trivial stumbles do not
        win, and at least one filler to be a meaningful "worst". Returns
        {"text", "start", "end"} or None.
        """
        eligible = [
            w for w in self._word_windows
            if (len(w["words"]) + len(w["fillers"])) >= 4 and len(w["fillers"]) > 0
        ]
        if not eligible:
            return None

        def density(w: dict) -> float:
            total = len(w["words"]) + len(w["fillers"])
            return len(w["fillers"]) / total if total else 0.0

        worst = max(eligible, key=density)
        return {"text": worst["text"], "start": worst["audio_start"], "end": worst["audio_end"],
                "epoch": worst.get("epoch", 0)}

    def get_replay_candidates(self) -> List[dict]:
        """Indexed list of replay-eligible windows (real audio span + has words),
        for Claude to choose the roughest from. Capped to bound prompt size."""
        cands = [
            {"index": i, "text": w["text"]}
            for i, w in enumerate(self._word_windows)
            if w["audio_end"] > w["audio_start"] and len(w["words"]) > 0
        ]
        return cands[:25]

    def get_window_by_index(self, index: Optional[int]) -> dict | None:
        """Resolve a window index (into the recorded windows) to a replay span,
        or None if the index is out of range or the window has no real audio."""
        if index is None or not (0 <= index < len(self._word_windows)):
            return None
        w = self._word_windows[index]
        if w["audio_end"] <= w["audio_start"] or len(w["words"]) == 0:
            return None
        return {"text": w["text"], "start": w["audio_start"], "end": w["audio_end"],
                "epoch": w.get("epoch", 0)}

    def get_replay_windows(self, roughest_index: Optional[int] = None) -> dict:
        """Return {"best": window|None, "worst": window|None} for audio replay.

        Each window is {"text", "start", "end"}. A window is only returned when it
        has a real audio span (end > start). If best and worst are the same span,
        worst is dropped.

        When `roughest_index` is given (Claude's pick), that window becomes the
        "worst"; if it can't be resolved, we fall back to the filler-density
        heuristic.
        """
        def valid(w: dict | None) -> bool:
            return w is not None and w["end"] > w["start"]

        best = self.get_best_window()
        best = best if valid(best) else None

        if roughest_index is not None:
            worst = self.get_window_by_index(roughest_index) or self.get_worst_window()
        else:
            worst = self.get_worst_window()
        worst = worst if valid(worst) else None

        if (best and worst and best["start"] == worst["start"]
                and best["end"] == worst["end"] and best.get("epoch") == worst.get("epoch")):
            worst = None

        return {"best": best, "worst": worst}
