"""Bounded character windows, preferring existing document block boundaries."""

from dataclasses import dataclass

from learntrail_agent.rag.cleaner import CleanedDocument


@dataclass(frozen=True)
class ArticleChunk:
    article_id: int
    slug: str
    title: str
    section: str
    ordinal: int
    start: int
    end: int
    text: str


def split_document(
    document: CleanedDocument, article_id: int, slug: str, size: int, overlap: int,
) -> list[ArticleChunk]:
    if not 32 <= size <= 8000 or not 0 <= overlap < size:
        raise ValueError("Invalid chunk size or overlap")
    chunks, start = [], 0
    text = document.text
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            lower = start + max(overlap + 1, size // 2)
            boundaries = [block.end for block in document.blocks if lower <= block.end <= end]
            if boundaries:
                end = max(boundaries)
            else:
                newline = text.rfind("\n", lower, end)
                if newline >= lower:
                    end = newline + 1
        body = text[start:end]
        if body.strip():
            probe = min(end - 1, start + (overlap if chunks else 0))
            section = next((block.section for block in reversed(document.blocks) if block.start <= probe), document.title)
            chunks.append(ArticleChunk(article_id, slug, document.title, section, len(chunks), start, end, body))
            if len(chunks) > 2000:
                raise ValueError("Article exceeds 2000 chunks; adjust chunk configuration")
        if end == len(text):
            break
        start = end - overlap
    if not chunks:
        raise ValueError("Article has no chunks")
    return chunks
