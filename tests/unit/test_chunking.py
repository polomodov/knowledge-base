from knowledge_base.chunking import split_text


def test_split_text_returns_ordered_chunks() -> None:
    chunks = split_text("First sentence. Second sentence. Third sentence.", max_chars=24)
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.text for chunk in chunks)
    assert chunks[0].char_start == 0


def test_split_text_handles_empty_text() -> None:
    assert split_text("   ") == []


def test_chunk_offsets_index_into_normalized_text() -> None:
    # char_start/char_end must faithfully slice the whitespace-normalized text
    # that callers store as document.text (finding #36).
    text = "First sentence.  Second sentence here.\n\nThird and final sentence."
    normalized = " ".join(text.split())
    chunks = split_text(text, max_chars=30)
    assert chunks
    for chunk in chunks:
        assert normalized[chunk.char_start : chunk.char_end] == chunk.text
