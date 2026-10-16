from __future__ import annotations

import re
from dataclasses import dataclass

MAX_TRANSCRIPT_CHARS = 150_000
# Rough word budget shown in the "too long" reply (~6 characters per word).
MAX_TRANSCRIPT_WORDS = MAX_TRANSCRIPT_CHARS // 6

# "00:00:01.000 --> 00:00:04.000" (or "0:00:01.000", comma decimals, "MM:SS.mmm").
_TS_LINE_RE = re.compile(
    r"^(?:(?P<h>\d{1,3}):)?(?P<m>\d{2}):(?P<s>\d{2})[.,]\d{1,3}\s*-->"
)
# Zoom sometimes repeats the cue time inline, e.g. "<00:00:01.000>".
_INLINE_TS_RE = re.compile(r"<\d{1,3}:\d{2}:\d{2}[.,]\d{1,3}>")
_TAG_RE = re.compile(r"<[^>]*>")
# "Name: text" — the name cannot contain sentence punctuation.
_SPEAKER_RE = re.compile(r"^(?P<name>[^:.!?]{1,50}?):\s+(?P<text>.+)$")


@dataclass
class Cue:
    start: str
    speaker: str | None
    text: str


def _format_start(hours: str | None, minutes: str, seconds: str) -> str:
    """Return mm:ss, or h:mm:ss when the cue starts at or after one hour."""
    mins = int(minutes)
    secs = int(seconds)
    if hours is None or int(hours) == 0:
        return f"{mins:02d}:{secs:02d}"
    return f"{int(hours)}:{mins:02d}:{secs:02d}"


def _looks_like_name(name: str) -> bool:
    name = name.strip()
    if not name or len(name.split()) > 4:
        return False
    return any(ch.isalpha() for ch in name)


def _split_speaker(line: str) -> tuple[str | None, str]:
    """Split a cue's first line into (speaker, text)."""
    line = _INLINE_TS_RE.sub("", line).strip()
    if line.lower().startswith("<v"):
        end = line.find(">")
        if end != -1:
            name = _TAG_RE.sub("", line[2:end]).strip()
            text = _TAG_RE.sub("", line[end + 1:]).strip()
            return (name or None), text
    match = _SPEAKER_RE.match(line)
    if match and _looks_like_name(match.group("name")):
        return match.group("name").strip(), _TAG_RE.sub("", match.group("text")).strip()
    return None, _TAG_RE.sub("", line).strip()


def _parse_cues(raw: str) -> list[Cue]:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    cues: list[Cue] = []
    for block in blocks:
        index = next(
            (i for i, line in enumerate(block) if _TS_LINE_RE.match(line.strip())),
            None,
        )
        if index is None:
            continue  # WEBVTT header, NOTE, STYLE or REGION block
        match = _TS_LINE_RE.match(block[index].strip())
        start = _format_start(
            match.group("h"), match.group("m"), match.group("s")
        )
        payload = [line for line in block[index + 1:] if line.strip()]
        if not payload:
            continue
        speaker, first = _split_speaker(payload[0])
        body = " ".join([first] + [line.strip() for line in payload[1:]]).strip()
        if not body:
            continue
        cues.append(Cue(start=start, speaker=speaker, text=body))
    return cues


def _merge_cues(cues: list[Cue]) -> list[str]:
    """Merge consecutive cues from the same speaker, keeping the first start time."""
    lines: list[str] = []
    previous: Cue | None = None
    for cue in cues:
        if previous is not None and previous.speaker == cue.speaker:
            lines[-1] = f"{lines[-1]} {cue.text}".rstrip()
        else:
            prefix = f"[{cue.start}]"
            if cue.speaker:
                prefix = f"{prefix} {cue.speaker}:"
            lines.append(f"{prefix} {cue.text}")
        previous = cue
    return lines


def clean_vtt(raw: str) -> str:
    """Clean a Zoom .vtt transcript into '[mm:ss] Name: text' lines."""
    return "\n".join(_merge_cues(_parse_cues(raw)))
