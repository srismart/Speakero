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

    def process_words(self, words: List[Dict[str, Any]]) -> Dict[str, Any]:
        new_fillers: List[str] = []
        new_pauses: int = 0
        chunk_words: List[str] = []
        chunk_fillers: List[str] = []
        chunk_t = time.time()

        i = 0
        while i < len(words):
            word_obj = words[i]
            word = word_obj.get("word", "").lower().strip(".,!?;:")
            start = word_obj.get("start", 0.0)
            end = word_obj.get("end", start)

            if self.last_word_end is not None:
                gap = start - self.last_word_end
                if gap >= PAUSE_THRESHOLD_SECONDS:
                    self.pause_count += 1
                    new_pauses += 1

            if i + 1 < len(words):
                next_word = words[i + 1].get("word", "").lower().strip(".,!?;:")
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
        return {"text": best["text"], "start": best["audio_start"], "end": best["audio_end"]}
