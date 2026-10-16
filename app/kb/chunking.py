import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

ENDS = re.compile(r"[。！？!?…\n]")
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def split_prose(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text] if text.strip() else []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size - overlap,
        chunk_overlap=0,
        separators=["\n\n", "\n", "。", "！", "？", "!", "?", "…", " ", ""],
        keep_separator="end",
        strip_whitespace=False,
    )
    result = []
    previous = ""
    for raw in splitter.split_text(text):
        # Only overlap complete sentences: start just after a sentence boundary.
        boundaries = [0] + [m.end() for m in ENDS.finditer(previous)]
        candidates = [previous[i:] for i in boundaries if 0 < len(previous[i:]) <= overlap]
        prefix = max(candidates, key=len, default="")
        if prefix and not ENDS.search(prefix[-1:]):
            prefix = ""
        result.append((prefix + raw).strip())
        previous = raw
    return result


def split_body(body: str, size: int, overlap: int) -> list[tuple[str, str]]:
    lines = body.splitlines()
    result, prose = [], []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and "|" in lines[i] and TABLE_RULE.match(lines[i + 1]):
            result.extend(
                (p, "recursive" if len("\n".join(prose)) > size else "section")
                for p in split_prose("\n".join(prose), size, overlap)
            )
            prose = []
            header = lines[i] + "\n" + lines[i + 1]
            block = header
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                row = lines[i]
                if len(block) + len(row) + 1 > size and block != header:
                    result.append((block, "table"))
                    block = header
                block += "\n" + row
                i += 1
            result.append((block, "table"))
        else:
            prose.append(lines[i])
            i += 1
    text = "\n".join(prose)
    result.extend(
        (p, "recursive" if len(text) > size else "section")
        for p in split_prose(text, size, overlap)
    )
    return result
