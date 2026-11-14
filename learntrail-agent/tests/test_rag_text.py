import pytest

from learntrail_agent.rag.chunker import split_document
from learntrail_agent.rag.cleaner import clean_document


def test_html_removes_scripts_and_keeps_headings_paragraphs_code() -> None:
    document = clean_document(" 学习  指南 ", """<h2>第一章</h2><p>你好&nbsp; 世界</p>
        <script>secret_script()</script><style>secret_style</style>
        <pre><code>if x &lt; 2:\n    print("你好")</code></pre><p>结束</p>""", "html")
    assert document.title == "学习 指南"
    assert "#" in document.text and "第一章" in document.text
    assert "secret_script" not in document.text and "secret_style" not in document.text
    assert 'if x < 2:\n    print("你好")' in document.text
    assert [block.kind for block in document.blocks] == ["heading", "paragraph", "code", "paragraph"]
    assert all(block.section == "第一章" for block in document.blocks)


def test_markdown_cleans_inline_markup_but_preserves_code() -> None:
    document = clean_document("文章", "## 方法\n\n**重点** [文档](https://example.test)\n\n```python\nif a < b:\n    value = '**原样**'\n```", "markdown")
    assert "重点 文档" in document.text and "https://" not in document.text
    assert "    value = '**原样**'" in document.text
    assert document.blocks[-1].kind == "code"
    assert document.blocks[-1].section == "方法"


def test_sibling_and_nested_headings_have_correct_paths() -> None:
    document = clean_document("文章", "## A\n\n段落一\n\n### A1\n\n段落二\n\n## B\n\n段落三", "markdown")
    assert [block.section for block in document.blocks if block.kind == "paragraph"] == ["A", "A / A1", "B"]


def test_plain_text_does_not_guess_html_or_markdown_format() -> None:
    document = clean_document("标题", "<T> 是类型参数\n**这是原文**\n\n第二段", "text")
    assert "<T>" in document.text and "**这是原文**" in document.text


def test_hash_normalizes_unicode_line_endings_and_extra_prose_spaces() -> None:
    first = clean_document(" cafe\u0301 ", "第一段   文字\r\n\r\n第二段", "text")
    second = clean_document("café", "第一段 文字\n\n第二段\n\n", "text")
    assert first.content_hash == second.content_hash
    assert len(first.content_hash) == 64
    assert first.content_hash != clean_document("不同标题", second.text, "text").content_hash
    assert first.content_hash != clean_document("café", "正文更新", "text").content_hash


def test_code_indentation_is_not_removed_by_hash_normalization() -> None:
    a = clean_document("代码", "```py\n    x = 1\n```", "markdown")
    b = clean_document("代码", "```py\nx = 1\n```", "markdown")
    assert a.content_hash != b.content_hash


@pytest.mark.parametrize("overlap", [0, 7, 31])
def test_chunks_are_bounded_overlap_and_reconstruct_canonical_text(overlap: int) -> None:
    document = clean_document("指南", "## 第一节\n\n" + "内容学习，" * 40 + "\n\n## 第二节\n\n" + "继续阅读。" * 25, "markdown")
    chunks = split_document(document, 42, "article-42", 64, overlap)
    assert len(chunks) > 1
    assert all(0 < len(chunk.text) <= 64 for chunk in chunks)
    for index, chunk in enumerate(chunks):
        assert chunk.ordinal == index and chunk.article_id == 42 and chunk.slug == "article-42"
        assert chunk.title == "指南" and chunk.section
        assert chunk.text == document.text[chunk.start:chunk.end]
    for previous, current in zip(chunks, chunks[1:]):
        assert current.start == previous.end - overlap
    assert chunks[0].text + "".join(chunk.text[overlap:] for chunk in chunks[1:]) == document.text


@pytest.mark.parametrize("size,overlap", [(31, 0), (64, 64), (64, -1)])
def test_invalid_chunk_configuration_is_rejected(size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        split_document(clean_document("标题", "正文", "text"), 1, "article-1", size, overlap)


@pytest.mark.parametrize("body,kind", [(" \n\t", "text"), ("<script>private()</script>", "html")])
def test_empty_normalized_content_is_rejected(body: str, kind: str) -> None:
    with pytest.raises(ValueError):
        clean_document("标题", body, kind)
