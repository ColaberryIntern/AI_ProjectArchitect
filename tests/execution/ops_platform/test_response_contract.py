"""Tests for execution/ops_platform/response_contract.py"""

import json

import pytest

from execution.ops_platform.response_contract import (
    REQUIRED_FIELDS,
    ContractFailure,
    coerce_to_contract,
    contract_prompt_addendum,
    extract_json,
    parse_and_validate,
    validate,
)


def test_required_fields_count():
    assert len(REQUIRED_FIELDS) == 13


def test_valid_payload_passes(make_response):
    payload = make_response()
    assert validate(payload) == []


def test_missing_field_fails(make_response):
    payload = make_response()
    del payload["summary"]
    errors = validate(payload)
    assert any("summary" in e for e in errors)


def test_extract_pure_json():
    raw = json.dumps({"a": 1})
    assert extract_json(raw) == {"a": 1}


def test_extract_from_markdown_fence():
    raw = '```json\n{"a": 1}\n```'
    assert extract_json(raw) == {"a": 1}


def test_extract_from_prose():
    raw = 'Sure, here is the result:\n{"a": 1, "b": [1,2]}\n\nThanks!'
    assert extract_json(raw) == {"a": 1, "b": [1, 2]}


def test_extract_returns_none_on_garbage():
    assert extract_json("nothing JSON-y here") is None


def test_coerce_fills_missing_fields():
    coerced = coerce_to_contract({"summary": "hi"})
    for field in REQUIRED_FIELDS:
        assert field in coerced
    assert coerced["summary"] == "hi"
    assert coerced["files_created"] == []


def test_parse_and_validate_coerces_by_default(make_response):
    # summary must be >= 10 chars per the schema, so use a realistic one.
    partial = {"summary": "A reasonable one-paragraph summary."}
    payload = parse_and_validate(json.dumps(partial))
    assert payload["summary"] == "A reasonable one-paragraph summary."
    assert payload["files_created"] == []


def test_parse_and_validate_strict_raises_on_missing(make_response):
    partial = json.dumps({"summary": "A reasonable summary."})
    with pytest.raises(ContractFailure):
        parse_and_validate(partial, strict=True)


def test_parse_and_validate_raises_on_garbage():
    with pytest.raises(ContractFailure):
        parse_and_validate("no JSON here")


def test_contract_addendum_lists_all_fields():
    addendum = contract_prompt_addendum()
    for field in REQUIRED_FIELDS:
        assert field in addendum
    assert "RESPONSE CONTRACT" in addendum
