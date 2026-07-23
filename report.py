import os
import json
import anthropic
from typing import Dict, Any

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
        _client = anthropic.AsyncAnthropic(api_key=api_key, timeout=30.0)
    return _client


def _str_array():
    return {"type": "array", "items": {"type": "string"}}


# filler_breakdown is intentionally absent: the caller injects the detector's
# real counts (deterministic) instead of having the LLM echo them.
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "roughest_window_index": {"type": "integer"},
        "topic_identified": {"type": "string"},
        "strengths": _str_array(),
        "improvements": _str_array(),
        "content_feedback": _str_array(),
        "summary": {"type": "string"},
        "spoken_feedback": {"type": "string"},
        "example_extract": {"type": "string"},
        "repetition_flags": _str_array(),
        "jargon_flags": _str_array(),
        "sentence_completion_rate": {"type": "string"},
        "highlight_moment": {"type": "string"},
        "roughest_moment_note": {"type": "string"},
        "content_score": {"type": "integer"},
        "verdict": {"type": "string"},
        # Direction-only drivers: an LLM judgment does not warrant fake-precision
        # numeric deltas, unlike the deterministic delivery drivers in scoring.py.
        "content_drivers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "positive": {"type": "boolean"},
                },
                "required": ["label", "positive"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "roughest_window_index", "topic_identified", "strengths", "improvements",
        "content_feedback", "summary", "spoken_feedback", "example_extract",
        "repetition_flags", "jargon_flags", "sentence_completion_rate", "highlight_moment",
        "roughest_moment_note", "content_score", "verdict", "content_drivers",
    ],
    "additionalProperties": False,
}


async def generate_report(
    transcript: str,
    stats: Dict[str, Any],
    topic: str = "",
    mode: str = "individual",
    highlight_window: str = "",
    candidate_windows: list | None = None,
    usage_sink: dict | None = None,
) -> Dict[str, Any]:
    filler_info = json.dumps(stats.get("fillerBreakdown", {}), sort_keys=True)

    if topic and topic.lower() not in ("", "self-identify"):
        topic_line = f"SPEECH TOPIC: {topic}"
        topic_instruction = (
            "Also evaluate content quality: was the speech relevant and well-structured "
            "for the stated topic? Include 2-3 bullet points in 'content_feedback'."
        )
    else:
        topic_line = "SPEECH TOPIC: (self-identify from transcript)"
        topic_instruction = (
            "Infer the likely topic from the transcript. Include 2-3 bullet points in "
            "'content_feedback' on whether the content was clear, focused, and well-structured."
        )

    # Mode-specific analysis instructions
    if mode == "panel":
        mode_line = "SESSION MODE: Panel / Q&A"
        mode_instruction = (
            "This was a panel interview or Q&A format. Evaluate: directness of answers, "
            "conciseness (were responses appropriately brief?), and responsiveness to implied questions. "
            "Flag if answers were too long or rambling."
        )
    elif mode == "pitch":
        mode_line = "SESSION MODE: Pitch presentation"
        mode_instruction = (
            "This was a pitch presentation. Evaluate: strength of the hook, clarity of the value proposition, "
            "persuasiveness of arguments, and presence/quality of a call-to-action. "
            "Note whether the pitch was compelling and memorable."
        )
    else:
        mode_line = "SESSION MODE: Individual talk / presentation"
        mode_instruction = (
            "This was an individual talk or presentation. Evaluate: narrative flow, "
            "quality of transitions between points, and strength of the conclusion."
        )

    # Highlight window section
    if highlight_window and highlight_window.strip():
        highlight_section = (
            f"\nBEST DELIVERY WINDOW:\n\"{highlight_window}\"\n"
            "Specifically call out what was good about this moment of the speech in 'highlight_moment'."
        )
    else:
        highlight_section = "\n(No highlight window available; set 'highlight_moment' to empty string.)"

    # Roughest-moment selection: Claude picks the worst-delivery window from a
    # numbered candidate list, folded into this same call to avoid an extra request.
    if candidate_windows:
        window_lines = "\n".join(f'{c["index"]}: {c["text"]}' for c in candidate_windows)
        roughest_section = (
            "\nCANDIDATE WINDOWS (numbered segments of the talk):\n"
            f"{window_lines}\n"
            "For 'roughest_window_index', pick the index of the window with the ROUGHEST "
            "delivery: most rambling, abandoned thoughts, awkward pauses, or filler. "
            "If none stands out, pick the most filler-heavy one. In 'roughest_moment_note', "
            "say in one short sentence what specifically made that window rough (e.g. "
            "'trailed off before finishing the thought', 'three filler words in a row'), "
            "quoting or referencing the window's own text."
        )
    else:
        roughest_section = (
            "\n(No candidate windows available; set 'roughest_window_index' to -1 and "
            "'roughest_moment_note' to empty string.)"
        )

    prompt = f"""You are an expert speaking coach. Analyze this practice session transcript and stats.

{topic_line}
{mode_line}

TRANSCRIPT:
{transcript or "(No transcript captured)"}

SESSION STATS:
- Total filler words: {stats.get("fillerCount", 0)}
- Pause count (>2s silences): {stats.get("pauseCount", 0)}
- Words per minute: {stats.get("wpm", 0)}
- Filler word breakdown: {filler_info}

{topic_instruction}

{mode_instruction}
{highlight_section}
{roughest_section}

Field guidance:
- spoken_feedback: the single most important coaching point as 1 natural spoken sentence (max 20 words).
- example_extract: rewrite 1-2 sentences from the transcript showing better delivery; natural and speakable.
- repetition_flags / jargon_flags: up to 3 items each; empty arrays if none.
- sentence_completion_rate: like "Good - most sentences were completed" or "Needs work - several abandoned thoughts".
- highlight_moment: comment on the best delivery window, or empty string if none was provided.
- roughest_window_index: integer index from the candidate list, or -1.
- roughest_moment_note: one sentence on what made the roughest window rough, or empty string.
- content_score: 0-100 judging CONTENT ONLY (structure, relevance, clarity of ideas,
  persuasiveness) - not delivery mechanics like fillers or pace, which are scored
  separately. Anchors: 85+ compelling and well-structured, 70-84 solid with gaps,
  50-69 unfocused or thin, below 50 hard to follow. Score the substance, not the polish.
- verdict: one plain-language sentence (max 18 words) summing up the session overall.
- content_drivers: 2-4 short factors behind the content score, each {{label (max 5
  words), positive (true if it helped, false if it hurt)}}.
Be specific, actionable, and encouraging. Base your analysis strictly on the data provided."""

    client = _get_client()
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        output_config={"format": {"type": "json_schema", "schema": REPORT_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if usage_sink is not None:
        usage_sink["input_tokens"] += message.usage.input_tokens
        usage_sink["output_tokens"] += message.usage.output_tokens
        usage_sink["llm_calls"] += 1
    report = json.loads(message.content[0].text)

    # Normalize Claude's roughest pick to a usable index or None (it may be
    # missing, -1, or a string). Callers pass it to get_replay_windows().
    idx = report.get("roughest_window_index")
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        idx = None
    report["roughest_window_index"] = idx if idx is not None and idx >= 0 else None

    # Clamp the LLM-judged content score into 0-100; None if unusable.
    score = report.get("content_score")
    try:
        score = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        score = None
    report["content_score"] = score

    return report
