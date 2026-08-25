"""Captions Agent — transcript + SRT/VTT from real narration timestamps (§31-32).

Responsibility: Generate transcript.txt, captions.srt, captions.vtt for YouTube.
Input: sentence/word timestamps from AudioAgent (real narration alignment)
Output: subtitles/transcript.txt, captions.srt, captions.vtt
Constraints:
  §31: Do NOT burn subtitles into video by default — deliver sidecar files.
  §32: Timestamps derive from real narration, NOT fixed 80-words/30s heuristic.
"""

from __future__ import annotations

from pathlib import Path

from src.agents.base import BaseAgent, AgentResult


class CaptionsAgent(BaseAgent):
    """Generates transcript + caption files from real narration timestamps (§31-32)."""

    # Max chars per caption line for readability (YouTube guideline ~ 40/line, 2 lines)
    MAX_LINE_CHARS = 42
    MAX_CAPTION_CHARS = 84

    def __init__(self):
        super().__init__(name="Captions")

    async def run(
        self,
        episode_id: str,
        sentence_timestamps: list[dict] | None = None,
        word_timestamps: list[dict] | None = None,
        narration: str = "",
        subtitles_dir: str = "",
        **kwargs,
    ) -> AgentResult:
        """Generate transcript + SRT + VTT (§31).

        Prefers sentence_timestamps (§32: real narration). If word_timestamps are
        available, groups them into readable caption cues.
        """
        if not sentence_timestamps and not word_timestamps:
            return AgentResult(success=False, error="No timestamps provided (§32 requires real narration timing)")

        sub_dir = Path(subtitles_dir) if subtitles_dir else None
        if sub_dir:
            sub_dir.mkdir(parents=True, exist_ok=True)

        # Build caption cues (start, end, text)
        if word_timestamps:
            cues = self._build_cues_from_words(word_timestamps)
        else:
            cues = self._build_cues_from_sentences(sentence_timestamps)

        # Transcript = plain text (no timestamps)
        transcript = narration.strip() if narration else " ".join(c["text"] for c in cues)

        srt = self._to_srt(cues)
        vtt = self._to_vtt(cues)

        files = {}
        if sub_dir:
            (sub_dir / "transcript.txt").write_text(transcript, encoding="utf-8")
            (sub_dir / "captions.srt").write_text(srt, encoding="utf-8")
            (sub_dir / "captions.vtt").write_text(vtt, encoding="utf-8")
            files = {
                "transcript": str(sub_dir / "transcript.txt"),
                "srt": str(sub_dir / "captions.srt"),
                "vtt": str(sub_dir / "captions.vtt"),
            }

        return AgentResult(
            success=True,
            data={
                "cue_count": len(cues),
                "transcript_chars": len(transcript),
                "files": files,
            },
            next_state="",  # finishing step, no state change
        )

    def _build_cues_from_sentences(self, sentence_timestamps: list[dict]) -> list[dict]:
        """Build caption cues from sentence timestamps, splitting long sentences."""
        cues = []
        for ts in sentence_timestamps:
            text = ts["text"].strip()
            start = ts["start"]
            end = ts["end"]
            if not text:
                continue
            # Split overly long sentences across the sentence's own time window
            if len(text) <= self.MAX_CAPTION_CHARS:
                cues.append({"start": start, "end": end, "text": self._wrap(text)})
            else:
                chunks = self._split_text(text, self.MAX_CAPTION_CHARS)
                dur = (end - start) / len(chunks)
                for i, chunk in enumerate(chunks):
                    cues.append({
                        "start": round(start + i * dur, 3),
                        "end": round(start + (i + 1) * dur, 3),
                        "text": self._wrap(chunk),
                    })
        return cues

    def _build_cues_from_words(self, word_timestamps: list[dict]) -> list[dict]:
        """Group word-level timestamps into readable caption cues."""
        cues = []
        current: list[dict] = []
        cur_len = 0
        for w in word_timestamps:
            word = w["word"].strip()
            if cur_len + len(word) + 1 > self.MAX_CAPTION_CHARS and current:
                cues.append(self._flush_words(current))
                current = []
                cur_len = 0
            current.append(w)
            cur_len += len(word) + 1
        if current:
            cues.append(self._flush_words(current))
        return cues

    def _flush_words(self, words: list[dict]) -> dict:
        text = " ".join(w["word"].strip() for w in words).strip()
        return {
            "start": round(words[0]["start"], 3),
            "end": round(words[-1]["end"], 3),
            "text": self._wrap(text),
        }

    def _split_text(self, text: str, max_chars: int) -> list[str]:
        """Split text into chunks not exceeding max_chars, at word boundaries."""
        words = text.split()
        chunks = []
        cur = ""
        for w in words:
            if len(cur) + len(w) + 1 > max_chars and cur:
                chunks.append(cur.strip())
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            chunks.append(cur.strip())
        return chunks

    def _wrap(self, text: str) -> str:
        """Wrap a caption into up to 2 lines of MAX_LINE_CHARS."""
        if len(text) <= self.MAX_LINE_CHARS:
            return text
        words = text.split()
        line1, line2 = "", ""
        for w in words:
            if len(line1) + len(w) + 1 <= self.MAX_LINE_CHARS or not line1:
                line1 = f"{line1} {w}".strip()
            else:
                line2 = f"{line2} {w}".strip()
        return f"{line1}\n{line2}".strip()

    @staticmethod
    def _fmt_ts(seconds: float, sep: str = ",") -> str:
        """Format seconds as HH:MM:SS,mmm (SRT) or HH:MM:SS.mmm (VTT)."""
        ms = int(round(seconds * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"

    def _to_srt(self, cues: list[dict]) -> str:
        lines = []
        for i, cue in enumerate(cues, 1):
            lines.append(str(i))
            lines.append(f"{self._fmt_ts(cue['start'], ',')} --> {self._fmt_ts(cue['end'], ',')}")
            lines.append(cue["text"])
            lines.append("")
        return "\n".join(lines)

    def _to_vtt(self, cues: list[dict]) -> str:
        lines = ["WEBVTT", ""]
        for cue in cues:
            lines.append(f"{self._fmt_ts(cue['start'], '.')} --> {self._fmt_ts(cue['end'], '.')}")
            lines.append(cue["text"])
            lines.append("")
        return "\n".join(lines)
