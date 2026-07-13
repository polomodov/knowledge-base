from knowledge_base.language import detect_language


def test_detect_language_cyrillic_is_ru() -> None:
    assert detect_language("Привет, мир и машинное обучение") == "ru"
    assert detect_language("Ёжик в тумане") == "ru"


def test_detect_language_latin_is_en() -> None:
    assert detect_language("Hello world and systems thinking") == "en"
    assert detect_language("Product Thinking notes") == "en"


def test_detect_language_empty_or_non_letters_is_unknown() -> None:
    assert detect_language("") == "unknown"
    assert detect_language("   ") == "unknown"
    assert detect_language("12345 !!!") == "unknown"


def test_detect_language_tie_is_unknown() -> None:
    # Equal Cyrillic and Latin letter counts → unknown (no majority).
    assert detect_language("abаб") == "unknown"
