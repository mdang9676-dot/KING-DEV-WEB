"""Từ điển thuật ngữ (tên nhân vật, thuật ngữ riêng) để AI dịch nhất quán. Mỗi dòng:  gốc = dịch  (cũng nhận -> , → , :)"""
import re

MAX_LINES = 100
MAX_TERM = 60
_SEP = re.compile(r"\s*(?:=>|->|→|=|：|:)\s*")


def parse_glossary(text: str | None) -> list[tuple[str, str]]:
    pairs, seen = [], set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = _SEP.split(line, maxsplit=1)
        if len(parts) != 2:
            continue
        src, dst = parts[0].strip(), parts[1].strip()
        if not src or not dst or len(src) > MAX_TERM or len(dst) > MAX_TERM or src.lower() in seen:
            continue
        seen.add(src.lower())
        pairs.append((src, dst))
        if len(pairs) >= MAX_LINES:
            break
    return pairs


def glossary_prompt(pairs: list[tuple[str, str]]) -> str:
    if not pairs:
        return ""
    body = "\n".join(f"- {s} => {d}" for s, d in pairs)
    return ("\nGlossary: whenever a term below appears in the source, you MUST use the given translation "
            "exactly (do not translate it differently):\n" + body)
