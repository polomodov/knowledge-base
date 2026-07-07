import math

import pytest

from knowledge_base.embeddings import fixture_embedding, hash_embedding, validate_vector


def test_fixture_embedding_is_deterministic_and_normalized() -> None:
    left = fixture_embedding("systems thinking", dimension=8)
    right = fixture_embedding("systems thinking", dimension=8)
    assert left == right
    assert len(left) == 8
    assert any(value != 0 for value in left)


def test_hash_embedding_handles_cyrillic() -> None:
    # The corpus is heavily Russian; the tokenizer must produce a real vector for Cyrillic
    # text, not an all-zero one (finding #44).
    left = hash_embedding("книжные заметки о системном мышлении")
    right = hash_embedding("книжные заметки о системном мышлении")
    assert left == right  # deterministic
    assert any(value != 0 for value in left)
    assert hash_embedding("машинное обучение") != hash_embedding("базы данных")


def test_hash_embedding_is_l2_normalized_for_nonempty_text() -> None:
    vector = hash_embedding("systems thinking ideas across books")
    assert math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0, abs_tol=1e-6)


def test_hash_embedding_empty_or_tokenless_text_is_zero_vector() -> None:
    assert hash_embedding("   ") == [0.0] * 8
    assert hash_embedding("!!! ??? ...") == [0.0] * 8  # no word tokens


def test_validate_vector_dimension() -> None:
    validate_vector([0.0] * 8, dimension=8)
    with pytest.raises(ValueError):
        validate_vector([0.0] * 7, dimension=8)


def test_validate_vector_rejects_non_numbers() -> None:
    with pytest.raises(ValueError):
        validate_vector([0.0, "x", 1.0], dimension=3)  # type: ignore[list-item]
