import hashlib
import unicodedata


def normalize(value: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKC", value).casefold()
        if not c.isspace() and not unicodedata.category(c).startswith("P")
    )


def fingerprint(question: str, answer: str) -> str:
    return hashlib.sha256((normalize(question) + "\0" + normalize(answer)).encode()).hexdigest()
