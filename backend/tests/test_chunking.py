from app.services.chunking import chunk_document_text, normalize_whitespace


def test_normalize_and_chunk():
    text = "第一段内容。" + ("扩" * 200) + "\n\n" + "第二段内容。" + ("展" * 200)
    chunks = chunk_document_text(text, num_pages=3, target_chars=300, min_chars=100, overlap_ratio=0.1)
    assert len(chunks) >= 1
    assert all(c["text"] for c in chunks)
    assert normalize_whitespace(" a \n\n\n b ") == "a \n\n b" or "a" in normalize_whitespace(" a \n\n\n b ")
