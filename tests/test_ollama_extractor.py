from unittest.mock import MagicMock

import pytest

from application_pipeline import (
    Config,
    LLMExtractorError,
    RelevanceVerdict,
    SourceEntry,
)
from application_pipeline.llm import OllamaExtractor
from application_pipeline.prompts import Prompts


def _config(**kwargs: object) -> Config:
    defaults: dict[str, object] = dict(
        keywords=["python"],
        skills=[],
        sources=[SourceEntry(parser_type="bundesagentur")],
        locations=["Hamburg"],
        ollama_base_url="http://localhost:11434",
        ollama_classify_model="test-model",
        ollama_read_timeout_seconds=30,
        ollama_json_retries=1,
        ollama_http_retries=1,
        ollama_keep_alive="5m",
    )
    defaults.update(kwargs)
    return Config(**defaults)  # type: ignore[arg-type]


def _prompts(
    classify_de: str = "DE: {title} {raw_description}",
    classify_en: str = "EN: {title} {raw_description}",
) -> Prompts:
    return Prompts(
        classify_relevance={"de": classify_de, "en": classify_en},
        judge_match={"de": "judge de", "en": "judge en"},
    )


# --- classify_relevance: happy path ---


def test_classify_relevance_returns_in_domain_true():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    result = extractor.classify_relevance("en", "Data Scientist", "ML role")

    assert isinstance(result, RelevanceVerdict)
    assert result.in_domain is True


def test_classify_relevance_returns_in_domain_false():
    http_post = MagicMock(return_value={"response": '{"in_domain": false}'})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    result = extractor.classify_relevance("en", "Nurse", "Healthcare role")

    assert result.in_domain is False


# --- language routing ---


def test_classify_relevance_uses_german_prompt_for_de():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(
        _config(),
        _prompts(
            classify_de="DE: {title} {raw_description}",
            classify_en="EN: {title} {raw_description}",
        ),
        _http_post=http_post,
    )

    extractor.classify_relevance("de", "Datenwissenschaftler", "ML Stelle")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["prompt"].startswith("DE:")


def test_classify_relevance_uses_english_prompt_for_en():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(
        _config(),
        _prompts(
            classify_de="DE: {title} {raw_description}",
            classify_en="EN: {title} {raw_description}",
        ),
        _http_post=http_post,
    )

    extractor.classify_relevance("en", "Data Scientist", "ML role")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["prompt"].startswith("EN:")


def test_classify_relevance_falls_back_to_english_for_unknown():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(
        _config(),
        _prompts(classify_en="EN: {title} {raw_description}"),
        _http_post=http_post,
    )

    extractor.classify_relevance("unknown", "title", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["prompt"].startswith("EN:")


def test_classify_relevance_falls_back_to_english_for_other():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(
        _config(),
        _prompts(classify_en="EN: {title} {raw_description}"),
        _http_post=http_post,
    )

    extractor.classify_relevance("other", "title", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["prompt"].startswith("EN:")


# --- HTTP request shape ---


def test_classify_relevance_posts_to_generate_endpoint():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(
        _config(ollama_base_url="http://pi:11434"), _prompts(), _http_post=http_post
    )

    extractor.classify_relevance("en", "title", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert url == "http://pi:11434/api/generate"


def test_classify_relevance_sends_configured_model():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(
        _config(ollama_classify_model="qwen3:8b"), _prompts(), _http_post=http_post
    )

    extractor.classify_relevance("en", "title", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["model"] == "qwen3:8b"


def test_classify_relevance_sends_keep_alive():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(
        _config(ollama_keep_alive="10m"), _prompts(), _http_post=http_post
    )

    extractor.classify_relevance("en", "title", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["keep_alive"] == "10m"


def test_classify_relevance_sends_stream_false():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    extractor.classify_relevance("en", "title", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["stream"] is False


def test_classify_relevance_formats_title_and_description_into_prompt():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(
        _config(),
        _prompts(classify_en="Title={title} Desc={raw_description}"),
        _http_post=http_post,
    )

    extractor.classify_relevance("en", "Engineer", "Build things")

    (url, payload, timeout) = http_post.call_args.args
    assert "Title=Engineer" in payload["prompt"]
    assert "Desc=Build things" in payload["prompt"]


def test_classify_relevance_passes_timeout_to_http_post():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(
        _config(ollama_read_timeout_seconds=45), _prompts(), _http_post=http_post
    )

    extractor.classify_relevance("en", "title", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert timeout == 45.0


# --- HTTP retries ---


def test_classify_relevance_raises_llm_extractor_error_after_http_retries_exhausted():
    http_post = MagicMock(side_effect=OSError("connection refused"))
    extractor = OllamaExtractor(
        _config(ollama_http_retries=2), _prompts(), _http_post=http_post
    )

    with pytest.raises(LLMExtractorError):
        extractor.classify_relevance("en", "title", "desc")


def test_classify_relevance_retries_on_http_error():
    http_post = MagicMock(
        side_effect=[OSError("timeout"), {"response": '{"in_domain": true}'}]
    )
    extractor = OllamaExtractor(
        _config(ollama_http_retries=2), _prompts(), _http_post=http_post
    )

    result = extractor.classify_relevance("en", "title", "desc")

    assert result.in_domain is True
    assert http_post.call_count == 2


# --- JSON retries ---


def test_classify_relevance_raises_llm_extractor_error_on_invalid_json():
    http_post = MagicMock(return_value={"response": "not json"})
    extractor = OllamaExtractor(
        _config(ollama_json_retries=1), _prompts(), _http_post=http_post
    )

    with pytest.raises(LLMExtractorError):
        extractor.classify_relevance("en", "title", "desc")


def test_classify_relevance_raises_llm_extractor_error_when_in_domain_key_missing():
    http_post = MagicMock(return_value={"response": '{"other": true}'})
    extractor = OllamaExtractor(
        _config(ollama_json_retries=1), _prompts(), _http_post=http_post
    )

    with pytest.raises(LLMExtractorError):
        extractor.classify_relevance("en", "title", "desc")


def test_classify_relevance_retries_http_call_on_bad_json():
    http_post = MagicMock(
        side_effect=[
            {"response": "bad json"},
            {"response": '{"in_domain": false}'},
        ]
    )
    extractor = OllamaExtractor(
        _config(ollama_json_retries=2, ollama_http_retries=1),
        _prompts(),
        _http_post=http_post,
    )

    result = extractor.classify_relevance("en", "title", "desc")

    assert result.in_domain is False
    assert http_post.call_count == 2


# --- Protocol conformance ---


def test_ollama_extractor_is_llm_extractor():
    from application_pipeline import LLMExtractor

    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    assert isinstance(extractor, LLMExtractor)
