from knowledge_base.chunking import split_text


def test_split_text_returns_ordered_chunks() -> None:
    chunks = split_text("First sentence. Second sentence. Third sentence.", max_chars=24)
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.text for chunk in chunks)
    assert chunks[0].char_start == 0


def test_split_text_handles_empty_text() -> None:
    assert split_text("   ") == []
