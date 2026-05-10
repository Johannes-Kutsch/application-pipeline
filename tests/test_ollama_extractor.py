from unittest.mock import MagicMock

import pytest

from application_pipeline import (
    Config,
    ExtractorUnreachableError,
    LLMExtractorError,
    MatchTier,
    MatchVerdict,
    RelevanceVerdict,
    SourceEntry,
)
from application_pipeline.llm import OllamaExtractor
from application_pipeline.prompts import (
    CLASSIFY_RELEVANCE_SLOTS,
    JUDGE_MATCH_SLOTS,
    PromptTemplate,
    Prompts,
)


def _config(**kwargs: object) -> Config:
    defaults: dict[str, object] = dict(
        keywords=["python"],
        skills=[],
        sources=[SourceEntry(parser_type="bundesagentur")],
        locations=["Hamburg"],
        ollama_base_url="http://localhost:11434",
        ollama_classify_model="test-model",
        ollama_judge_model="test-judge-model",
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
    judge_de: str = "DE judge: {skills} {raw_description}",
    judge_en: str = "EN judge: {skills} {raw_description}",
) -> Prompts:
    return Prompts(
        classify_relevance={
            "de": PromptTemplate(classify_de, CLASSIFY_RELEVANCE_SLOTS),
            "en": PromptTemplate(classify_en, CLASSIFY_RELEVANCE_SLOTS),
        },
        judge_match={
            "de": PromptTemplate(judge_de, JUDGE_MATCH_SLOTS),
            "en": PromptTemplate(judge_en, JUDGE_MATCH_SLOTS),
        },
    )


_JUDGE_RESPONSE = (
    '{"tier": "green", "matched": ["python"], "missing": [], "summary": "Good match"}'
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


def test_classify_relevance_sends_format_json_string():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    extractor.classify_relevance("en", "title", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["format"] == "json"


def test_classify_relevance_sends_options_temperature_zero():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    extractor.classify_relevance("en", "title", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["options"]["temperature"] == 0.0


def test_classify_relevance_sends_options_num_ctx_4096():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    extractor.classify_relevance("en", "title", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["options"]["num_ctx"] == 4096


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
        _config(ollama_http_retries=2),
        _prompts(),
        _http_post=http_post,
        _sleep=lambda _: None,
    )

    with pytest.raises(LLMExtractorError):
        extractor.classify_relevance("en", "title", "desc")


def test_classify_relevance_retries_on_http_error():
    http_post = MagicMock(
        side_effect=[OSError("timeout"), {"response": '{"in_domain": true}'}]
    )
    extractor = OllamaExtractor(
        _config(ollama_http_retries=2),
        _prompts(),
        _http_post=http_post,
        _sleep=lambda _: None,
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


# --- judge_match: happy path ---


def test_judge_match_returns_match_verdict():
    http_post = MagicMock(return_value={"response": _JUDGE_RESPONSE})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    result = extractor.judge_match("en", "Looking for Python dev")

    assert isinstance(result, MatchVerdict)
    assert result.tier == MatchTier.green
    assert result.matched == ["python"]
    assert result.missing == []
    assert result.summary == "Good match"


def test_judge_match_uses_german_prompt_for_de():
    http_post = MagicMock(return_value={"response": _JUDGE_RESPONSE})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    extractor.judge_match("de", "Stelle")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["prompt"].startswith("DE judge:")


def test_judge_match_uses_english_prompt_for_en():
    http_post = MagicMock(return_value={"response": _JUDGE_RESPONSE})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    extractor.judge_match("en", "Job posting")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["prompt"].startswith("EN judge:")


def test_judge_match_falls_back_to_english_for_unknown():
    http_post = MagicMock(return_value={"response": _JUDGE_RESPONSE})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    extractor.judge_match("fr", "Job posting")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["prompt"].startswith("EN judge:")


def test_judge_match_renders_skills_into_prompt():
    http_post = MagicMock(return_value={"response": _JUDGE_RESPONSE})
    extractor = OllamaExtractor(
        _config(skills=["python", "docker"]),
        _prompts(judge_en="skills={skills}"),
        _http_post=http_post,
    )

    extractor.judge_match("en", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert "- python" in payload["prompt"]
    assert "- docker" in payload["prompt"]


def test_judge_match_sends_judge_model():
    http_post = MagicMock(return_value={"response": _JUDGE_RESPONSE})
    extractor = OllamaExtractor(
        _config(ollama_judge_model="qwen3:14b"), _prompts(), _http_post=http_post
    )

    extractor.judge_match("en", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["model"] == "qwen3:14b"


def test_judge_match_sends_temperature_02():
    http_post = MagicMock(return_value={"response": _JUDGE_RESPONSE})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    extractor.judge_match("en", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["options"]["temperature"] == 0.2


def test_judge_match_sends_num_ctx_4096():
    http_post = MagicMock(return_value={"response": _JUDGE_RESPONSE})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    extractor.judge_match("en", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["options"]["num_ctx"] == 4096


def test_judge_match_sends_stream_false():
    http_post = MagicMock(return_value={"response": _JUDGE_RESPONSE})
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    extractor.judge_match("en", "desc")

    (url, payload, timeout) = http_post.call_args.args
    assert payload["stream"] is False


# --- judge_match: error mapping ---


def test_judge_match_raises_llm_extractor_error_after_http_retries_exhausted():
    http_post = MagicMock(side_effect=OSError("connection refused"))
    extractor = OllamaExtractor(
        _config(ollama_http_retries=2),
        _prompts(),
        _http_post=http_post,
        _sleep=lambda _: None,
    )

    with pytest.raises(LLMExtractorError):
        extractor.judge_match("en", "desc")


def test_judge_match_retries_on_http_error():
    http_post = MagicMock(
        side_effect=[OSError("timeout"), {"response": _JUDGE_RESPONSE}]
    )
    extractor = OllamaExtractor(
        _config(ollama_http_retries=2),
        _prompts(),
        _http_post=http_post,
        _sleep=lambda _: None,
    )

    result = extractor.judge_match("en", "desc")

    assert result.tier == MatchTier.green
    assert http_post.call_count == 2


def test_judge_match_raises_llm_extractor_error_on_invalid_json():
    http_post = MagicMock(return_value={"response": "not json"})
    extractor = OllamaExtractor(
        _config(ollama_json_retries=1), _prompts(), _http_post=http_post
    )

    with pytest.raises(LLMExtractorError):
        extractor.judge_match("en", "desc")


def test_judge_match_raises_llm_extractor_error_on_missing_tier():
    http_post = MagicMock(
        return_value={"response": '{"matched": [], "missing": [], "summary": "x"}'}
    )
    extractor = OllamaExtractor(
        _config(ollama_json_retries=1), _prompts(), _http_post=http_post
    )

    with pytest.raises(LLMExtractorError):
        extractor.judge_match("en", "desc")


def test_judge_match_raises_llm_extractor_error_on_invalid_tier_value():
    http_post = MagicMock(
        return_value={
            "response": '{"tier": "invalid", "matched": [], "missing": [], "summary": "x"}'
        }
    )
    extractor = OllamaExtractor(
        _config(ollama_json_retries=1), _prompts(), _http_post=http_post
    )

    with pytest.raises(LLMExtractorError):
        extractor.judge_match("en", "desc")


def test_judge_match_retries_http_call_on_bad_json():
    http_post = MagicMock(
        side_effect=[
            {"response": "bad json"},
            {"response": _JUDGE_RESPONSE},
        ]
    )
    extractor = OllamaExtractor(
        _config(ollama_json_retries=2, ollama_http_retries=1),
        _prompts(),
        _http_post=http_post,
    )

    result = extractor.judge_match("en", "desc")

    assert result.tier == MatchTier.green
    assert http_post.call_count == 2


# --- prewarm ---


def test_prewarm_sends_two_pings_when_models_differ():
    http_post = MagicMock(return_value={})
    extractor = OllamaExtractor(
        _config(ollama_classify_model="model-a", ollama_judge_model="model-b"),
        _prompts(),
        _http_post=http_post,
    )

    extractor.prewarm()

    assert http_post.call_count == 2
    models_called = [call.args[1]["model"] for call in http_post.call_args_list]
    assert "model-a" in models_called
    assert "model-b" in models_called


def test_prewarm_sends_single_ping_when_models_equal():
    http_post = MagicMock(return_value={})
    extractor = OllamaExtractor(
        _config(ollama_classify_model="same-model", ollama_judge_model="same-model"),
        _prompts(),
        _http_post=http_post,
    )

    extractor.prewarm()

    assert http_post.call_count == 1
    (url, payload, timeout) = http_post.call_args.args
    assert payload["model"] == "same-model"


def test_prewarm_uses_correct_body():
    http_post = MagicMock(return_value={})
    extractor = OllamaExtractor(
        _config(
            ollama_keep_alive="10m", ollama_classify_model="m", ollama_judge_model="m"
        ),
        _prompts(),
        _http_post=http_post,
    )

    extractor.prewarm()

    (url, payload, timeout) = http_post.call_args.args
    assert payload["prompt"] == "ok"
    assert payload["options"]["num_predict"] == 1
    assert payload["keep_alive"] == "10m"


def test_prewarm_raises_extractor_unreachable_on_failure():
    http_post = MagicMock(side_effect=OSError("connection refused"))
    extractor = OllamaExtractor(_config(), _prompts(), _http_post=http_post)

    with pytest.raises(ExtractorUnreachableError):
        extractor.prewarm()


def test_prewarm_does_not_send_second_ping_after_first_fails():
    http_post = MagicMock(side_effect=OSError("connection refused"))
    extractor = OllamaExtractor(
        _config(ollama_classify_model="model-a", ollama_judge_model="model-b"),
        _prompts(),
        _http_post=http_post,
    )

    with pytest.raises(ExtractorUnreachableError):
        extractor.prewarm()

    assert http_post.call_count == 1


# --- drift tests ---
# PromptTemplate.render() raises PromptError on any slot mismatch, so these
# tests verify OllamaExtractor passes exactly the right slots by confirming
# no exception is raised and the rendered content appears in the HTTP payload.


def test_classify_slots_match_inventory():
    http_post = MagicMock(return_value={"response": '{"in_domain": true}'})
    extractor = OllamaExtractor(
        _config(),
        _prompts(classify_en="t={title} d={raw_description}"),
        _http_post=http_post,
    )

    extractor.classify_relevance("en", "MY_TITLE", "MY_DESC")

    (_, payload, _) = http_post.call_args.args
    assert "t=MY_TITLE" in payload["prompt"]
    assert "d=MY_DESC" in payload["prompt"]


def test_judge_slots_match_inventory():
    http_post = MagicMock(return_value={"response": _JUDGE_RESPONSE})
    extractor = OllamaExtractor(
        _config(skills=["python"]),
        _prompts(judge_en="s={skills} d={raw_description}"),
        _http_post=http_post,
    )

    extractor.judge_match("en", "MY_DESC")

    (_, payload, _) = http_post.call_args.args
    assert "d=MY_DESC" in payload["prompt"]
    assert "- python" in payload["prompt"]
