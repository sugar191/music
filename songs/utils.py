import unicodedata


def normalize(s: str) -> str:
    if s is None:
        return ""
    return unicodedata.normalize("NFKC", s).lower().strip()
