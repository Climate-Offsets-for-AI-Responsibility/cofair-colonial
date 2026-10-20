import pytest
from key_validator import detect_provider, validate_key_format

TEST_ANTHROPIC = 'sk-ant-api03-' + 'A' * 93
TEST_OPENAI_PROJECT = 'sk-abc123' + 'A' * 93
TEST_OPENAI_LEGACY = 'sk-' + 'x9' * 24
TEST_GOOGLE = 'AIzaSy' + 'D' * 33

@pytest.mark.parametrize(
    "key, provider",
    [
        (TEST_ANTHROPIC, "anthropic"),
        (TEST_OPENAI_PROJECT, "openai"),
        (TEST_OPENAI_LEGACY, "openai"),
        (TEST_GOOGLE, "google"),
    ],
)
def test_valid_keys_pass(key, provider):
    assert validate_key_format(key, provider)

@pytest.mark.parametrize(
    "key, provider",
    [
        (TEST_ANTHROPIC, "openai"),   # Anthropic key claimed as OpenAI
        (TEST_OPENAI_PROJECT, "anthropic"),
        (TEST_GOOGLE, "openai"),
        (TEST_OPENAI_LEGACY, "google"),
    ],
)
def test_wrong_provider_fails(key, provider):
    assert not validate_key_format(key, provider)

@pytest.mark.parametrize(
    "key, provider",
    [
        ("", "anthropic"),
        ("sk-ant-api03-short", "anthropic"),          # too short
        ("sk-ant-admin01-" + "A" * 93, "anthropic"),  # admin key, rejected on purpose
        ("AIza" + "A" * 34, "google"),                # one char short
        ("AIza" + "A" * 36, "google"),                # one char long
        ("sk-proj-has spaces in it", "openai"),
        ("sk-proj-" + "a" * 30 + "!", "openai"),      # illegal character
        (None, "openai"),
    ],
)
def test_malformed_keys_fail(key, provider):
    assert not validate_key_format(key, provider)

def test_surrounding_whitespace_is_ignored():
    assert validate_key_format(f' {TEST_GOOGLE}\n', "google")

def test_surrounding_name_is_case_insensitive():
    assert validate_key_format(TEST_ANTHROPIC, "Anthropic")

def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        validate_key_format(TEST_GOOGLE, "mistral")
 
 
def test_error_message_never_contains_the_key():
    with pytest.raises(ValueError) as exc:
        validate_key_format(TEST_ANTHROPIC, "mistral")
    assert TEST_ANTHROPIC not in str(exc.value)
 
 
@pytest.mark.parametrize(
    "key, expected",
    [
        (TEST_ANTHROPIC, "anthropic"),
        (TEST_OPENAI_PROJECT, "openai"),
        (TEST_GOOGLE, "google"),
        ("not-a-key", None),
    ],
)
def test_detect_provider(key, expected):
    assert detect_provider(key) == expected