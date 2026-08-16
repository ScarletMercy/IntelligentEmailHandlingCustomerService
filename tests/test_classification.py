"""Tests for workflow classification helpers:

* ``_validate_classification``  -- fallback / default logic
* ``_extract_json_from_text``   -- JSON extraction from LLM text
* ``clean_html_content``        -- HTML tag stripping
"""

import json

import pytest

from ai_email.workflow import (
    _VALID_INTENTS,
    _extract_json_from_text,
    _validate_classification,
    clean_html_content,
)


# --------------------------------------------------------------------------- #
# _extract_json_from_text
# --------------------------------------------------------------------------- #
def test_extract_json_markdown_code_block():
    assert json.loads(_extract_json_from_text('```json\n{"a":1}\n```')) == {"a": 1}


def test_extract_json_markdown_no_lang():
    assert json.loads(_extract_json_from_text('```\n{"a":1}\n```')) == {"a": 1}


def test_extract_json_bare_json():
    assert json.loads(_extract_json_from_text('text {"a":1} text')) == {"a": 1}


def test_extract_json_deeply_nested():
    raw = '{"a":{"b":{"c":1}},"d":2}'
    assert json.loads(_extract_json_from_text(raw)) == {
        "a": {"b": {"c": 1}},
        "d": 2,
    }


def test_extract_json_trailing_text():
    assert json.loads(_extract_json_from_text('{"x":1}\nextra')) == {"x": 1}


def test_extract_json_no_json_raises():
    with pytest.raises(ValueError):
        _extract_json_from_text("no json here at all")


# --------------------------------------------------------------------------- #
# _validate_classification
# --------------------------------------------------------------------------- #
def test_validate_valid_input():
    r = _validate_classification(
        {"intent": "bug", "urgency": "high", "terminal": "Web", "topic": "t", "summary": "s"}
    )
    assert r.intent == "bug"
    assert r.urgency == "high"
    assert r.terminal == "Web"
    assert r.topic == "t"
    assert r.summary == "s"


def test_validate_invalid_intent_falls_back():
    r = _validate_classification(
        {"intent": "invalid", "urgency": "high", "terminal": "Web", "topic": "t", "summary": "s"}
    )
    assert r.intent == "question"


def test_validate_invalid_urgency_falls_back():
    r = _validate_classification(
        {"intent": "bug", "urgency": "extreme", "terminal": "Web", "topic": "t", "summary": "s"}
    )
    assert r.urgency == "medium"


def test_validate_invalid_terminal_falls_back():
    r = _validate_classification(
        {"intent": "bug", "urgency": "low", "terminal": "Linux", "topic": "t", "summary": "s"}
    )
    assert r.terminal == "Not provided"


def test_validate_empty_dict_all_defaults():
    r = _validate_classification({})
    assert r.intent == "question"
    assert r.urgency == "medium"
    assert r.terminal == "Not provided"
    assert r.topic == ""
    assert r.summary == ""


@pytest.mark.parametrize("intent", list(_VALID_INTENTS))
def test_validate_all_valid_intents_pass(intent):
    r = _validate_classification(
        {"intent": intent, "urgency": "low", "terminal": "Web", "topic": "", "summary": ""}
    )
    assert r.intent == intent


# --------------------------------------------------------------------------- #
# clean_html_content
# --------------------------------------------------------------------------- #
def test_clean_html_basic_tags():
    assert clean_html_content("<p>Hello <b>World</b></p>") == "Hello World"


def test_clean_html_script_removed():
    result = clean_html_content("<script>alert(1)</script><p>ok</p>")
    assert "alert" not in result
    assert "ok" in result


def test_clean_html_style_removed():
    result = clean_html_content("<style>.x{color:red}</style><p>text</p>")
    assert "color" not in result
    assert "text" in result


def test_clean_html_collapses_whitespace():
    result = clean_html_content("<p>a</p>\n\n<p>b</p>")
    assert "  " not in result
    assert result == "a b"


def test_clean_html_plain_text_unchanged():
    assert clean_html_content("just text") == "just text"


def test_clean_html_empty_string():
    assert clean_html_content("") == ""


def test_clean_html_non_str_coerced():
    # 非 str 输入先 str() 再清洗：具体断言转换结果而非仅类型
    assert clean_html_content({"k": "v"}) == "{'k': 'v'}"


def test_clean_html_none_coerced():
    assert clean_html_content(None) == "None"


# --------------------------------------------------------------------------- #
# 花括号计数必须感知字符串字面量：topic/summary 含 "}" 时旧实现深度提前归零，
# 截出非法 JSON
# --------------------------------------------------------------------------- #
def test_extract_json_brace_inside_string_value():
    from ai_email.workflow import _extract_json_from_text

    text = '{"intent": "question", "topic": "curl -d { } usage", "summary": "s"}'
    extracted = _extract_json_from_text(text)
    assert '"}' in extracted  # 完整闭合，未被字符串内的 } 截断


def test_extract_json_escaped_quote_inside_string():
    from ai_email.workflow import _extract_json_from_text

    text = '{"topic": "say \\"}\\"", "summary": "s"}'
    extracted = _extract_json_from_text(text)
    import json

    assert json.loads(extracted)["topic"] == 'say "}"'
