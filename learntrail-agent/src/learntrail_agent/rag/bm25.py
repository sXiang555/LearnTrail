"""Deterministic CJK overlapping bigrams + Latin words, and Okapi BM25.

No dictionary downloads, substring scoring, or model calls. A tokenizer version
is stored with every chunk so changes can be backfilled without re-embedding.
"""

import math
import re
import unicodedata
from collections import Counter

TOKENIZER_VERSION = "cjk-bigram-latin-v1"
PARTS = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]+|[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    result: list[str] = []
    for part in PARTS.findall(unicodedata.normalize("NFKC", text).casefold()):
        if "\u3400" <= part[0] <= "\u9fff" or "\U00020000" <= part[0] <= "\U0002fa1f":
            result.extend([part] if len(part) == 1 else [part[i:i + 2] for i in range(len(part) - 1)])
        else:
            result.append(part)
    return result


def term_counts(text: str) -> dict[str, int]:
    return dict(Counter(tokenize(text)))


def bm25_score(
    terms: dict[str, int], query: list[str], frequencies: dict[str, int],
    documents: int, average_length: float, k1: float = 1.2, b: float = 0.75,
) -> float:
    """Reference formula used by unit tests; production ranking uses the same SQL formula."""
    if documents <= 0 or average_length <= 0:
        return 0.0
    length = sum(terms.values())
    return sum(
        math.log(1 + (documents - frequencies[token] + 0.5) / (frequencies[token] + 0.5))
        * terms[token] * (k1 + 1) / (terms[token] + k1 * (1 - b + b * length / average_length))
        for token in set(query) if terms.get(token, 0) > 0 and token in frequencies
    )
