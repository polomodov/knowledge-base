import pytest

from knowledge_base.embeddings import fixture_embedding, validate_vector


def test_fixture_embedding_is_deterministic_and_normalized() -> None:
    left = fixture_embedding("systems thinking", dimension=8)
    right = fixture_embedding("systems thinking", dimension=8)
    assert left == right
    assert len(left) == 8
    assert any(value != 0 for value in left)


def test_validate_vector_dimension() -> None:
    validate_vector([0.0] * 8, dimension=8)
    with pytest.raises(ValueError):
        validate_vector([0.0] * 7, dimension=8)
