import os
import json
import anthropic
from typing import Dict, Any


async def generate_report(
    transcript: str,
    stats: Dict[str, Any],
    topic: str = "",
    mode: str = "individual",
    highlight_window: str = "",
    candidate_windows: list | None = None,
) -> Dict[str, Any]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is not set")

    client = anthropic.AsyncAnthropic(api_key=api_key)

    filler_info = json.dumps(stats.get("fillerBreakdown", {}))

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
        highlight_section = "\n(No highlight window available — set 'highlight_moment' to empty string.)"

    # Roughest-moment selection: let Claude choose which window had the worst delivery
    # (rambling, abandoned thoughts, fillers, awkward pauses) from a numbered candidate
    # list, returned as an index. Folded into this same call to avoid an extra request.
    if candidate_windows:
        window_lines = "\n".join(f'{c["index"]}: {c["text"]}' for c in candidate_windows)
        roughest_section = (
            "\nCANDIDATE WINDOWS (numbered segments of the talk):\n"
            f"{window_lines}\n"
            "From the numbered list above, pick the index of the window with the ROUGHEST "
            "delivery — most rambling, abandoned thoughts, awkward pauses, or filler. "
            "Return that integer as 'roughest_window_index'. If none clearly stands out, "
            "pick the most filler-heavy one. If the list is empty, use -1."
        )
        roughest_key = '  "roughest_window_index": <integer index from the candidate list above, or -1 if none>,\n'
    else:
        roughest_section = ""
        roughest_key = ""

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

Return ONLY a valid JSON object (no markdown, no code fences) with exactly these keys:
{{
{roughest_key}  "topic_identified": "the topic you identified or confirmed",
  "strengths": ["string", "string"],
  "improvements": ["string", "string", "string"],
  "content_feedback": ["string", "string", "string"],
  "filler_breakdown": {{"word": count}},
  "summary": "one paragraph overall assessment",
  "spoken_feedback": "REQUIRED — the single most important coaching point as 1 natural spoken sentence (max 20 words). Must never be empty.",
  "example_extract": "REQUIRED — rewrite 1-2 sentences from the transcript showing how they could be delivered better. Must be natural and speakable aloud. Never empty.",
  "repetition_flags": ["up to 3 phrases repeated too often — empty list if none"],
  "jargon_flags": ["up to 3 overly technical phrases that may confuse a general audience — empty list if none"],
  "sentence_completion_rate": "Claude's assessment as a string like 'Good — most sentences were completed' or 'Needs work — several abandoned thoughts detected'",
  "highlight_moment": "Claude's comment on the best delivery window, or empty string if no window was provided"
}}

Be specific, actionable, and encouraging. Base your analysis strictly on the data provided.
For repetition_flags and jargon_flags: return actual empty arrays [] if there are no issues, not arrays with placeholder strings."""

    report = None
    parse_error: Exception | None = None
    for _attempt in range(2):
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            report = json.loads(raw)
            break
        except json.JSONDecodeError as e:
            parse_error = e

    if report is None:
        raise parse_error

    # Normalize Claude's roughest pick to a usable index or None (it may be
    # missing, -1, or a string). Callers pass it to get_replay_windows().
    idx = report.get("roughest_window_index")
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        idx = None
    report["roughest_window_index"] = idx if idx is not None and idx >= 0 else None

    return report
