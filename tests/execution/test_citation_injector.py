"""Tests for execution/citation_injector.py."""

import pytest

from execution.citation_injector import inject_citations


@pytest.fixture
def req_bol():
    return {
        "id": "REQ-001",
        "name": "BOL Document Ingestion",
        "actor": "broker",
        "action": "upload a BOL PDF for a delivered load",
        "value": "the system extracts metadata and unblocks invoicing",
        "problem_mapped_to": "document_driven_gating",
        "acceptance_criteria": [
            {
                "id": "AC-001-1",
                "given": "an authenticated broker with shipment SHP-1234",
                "when": "they POST a BOL PDF",
                "then": "the response is 201 within 5s",
            }
        ],
    }


@pytest.fixture
def req_invoice():
    return {
        "id": "REQ-003",
        "name": "Invoice Composition",
        "actor": "broker",
        "action": "issue an invoice for a delivered shipment",
        "value": "customer receives a payable invoice within 24h",
        "problem_mapped_to": "asymmetric_invoicing",
        "acceptance_criteria": [
            {
                "id": "AC-003-1",
                "given": "shipment SHP-1234 with line haul $1,200",
                "when": "broker issues an invoice",
                "then": "invoice INV-3001 is created with total $1,500.00",
            }
        ],
    }


class TestInjectCitations:
    def test_no_requirements_returns_unchanged(self):
        text = "Some chapter content."
        out, report = inject_citations(text, [])
        assert out == text
        assert report.injected == []
        assert report.already_cited == []

    def test_injects_after_first_name_match(self, req_bol):
        text = (
            "## Implementation\n\n"
            "The BOL Document Ingestion service accepts PDFs from carriers "
            "and validates them before invoicing.\n"
        )
        out, report = inject_citations(text, [req_bol])
        assert "[REQ-001]" in out
        assert "BOL Document Ingestion [REQ-001]" in out
        assert report.injected == ["REQ-001"]

    def test_injects_after_action_phrase(self, req_bol):
        text = (
            "## Implementation\n\n"
            "The broker can upload a BOL PDF for a delivered load via "
            "POST /api/documents.\n"
        )
        out, report = inject_citations(text, [req_bol])
        assert "[REQ-001]" in out
        assert report.injected == ["REQ-001"]

    def test_already_cited_skipped(self, req_bol):
        text = "The BOL Document Ingestion [REQ-001] service runs.\n"
        out, report = inject_citations(text, [req_bol])
        # Already cited — not duplicated
        assert out.count("[REQ-001]") == 1
        assert "REQ-001" in report.already_cited
        assert report.injected == []

    def test_unmatched_requirement_reported(self):
        req = {
            "id": "REQ-999",
            "name": "Completely Unrelated Thing",
            "action": "do something nobody mentioned",
        }
        text = "This chapter is about invoices and shipments.\n"
        out, report = inject_citations(text, [req])
        assert out == text
        assert "REQ-999" in report.unmatched

    def test_multiple_requirements_one_pass(self, req_bol, req_invoice):
        text = (
            "## Overview\n"
            "The BOL Document Ingestion step validates paperwork.\n"
            "Then Invoice Composition produces the customer invoice.\n"
        )
        out, report = inject_citations(text, [req_bol, req_invoice])
        assert "[REQ-001]" in out
        assert "[REQ-003]" in out
        assert sorted(report.injected) == ["REQ-001", "REQ-003"]

    def test_does_not_break_code_blocks(self, req_bol):
        text = (
            "## Implementation\n\n"
            "The BOL Document Ingestion service uses this helper:\n"
            "```python\n"
            "# BOL Document Ingestion code goes here\n"
            "def ingest_bol(): pass\n"
            "```\n"
            "Then the broker can upload a BOL PDF.\n"
        )
        out, report = inject_citations(text, [req_bol])
        # The code block must be untouched
        assert "# BOL Document Ingestion code goes here" in out
        assert "def ingest_bol(): pass" in out
        # Citation appears outside the code block — exactly once
        assert out.count("[REQ-001]") == 1
        assert report.injected == ["REQ-001"]

    def test_idempotent(self, req_bol, req_invoice):
        text = (
            "## Implementation\n\n"
            "The BOL Document Ingestion runs first. "
            "Invoice Composition follows.\n"
        )
        once, _ = inject_citations(text, [req_bol, req_invoice])
        twice, report = inject_citations(once, [req_bol, req_invoice])
        assert once == twice
        # Second pass: both already cited
        assert sorted(report.already_cited) == ["REQ-001", "REQ-003"]
        assert report.injected == []

    def test_ac_injection_when_anchor_present(self, req_invoice):
        text = (
            "## Implementation\n\n"
            "Invoice Composition computes the total. "
            "When all line items are present, invoice INV-3001 is created "
            "with the customer's billing address.\n"
        )
        out, report = inject_citations(text, [req_invoice])
        assert "[REQ-003]" in out
        # AC anchor is the capitalized ID INV-3001 from the AC's `then` clause
        assert "[AC-003-1]" in out
        assert "AC-003-1" in report.ac_injected

    def test_inserts_with_space_before(self, req_bol):
        text = "The BOL Document Ingestion service.\n"
        out, _ = inject_citations(text, [req_bol])
        # Citation token has a leading space, no extra whitespace after the word
        assert "Ingestion [REQ-001] service" in out

    def test_no_citation_inside_existing_bracket(self, req_bol):
        text = "[Note: BOL Document Ingestion is critical] for the system.\n"
        out, _ = inject_citations(text, [req_bol])
        # Should not inject inside the [Note: ...] bracket — fallback to
        # nothing or to a different occurrence. In this short text there
        # is no other occurrence; expect no injection.
        assert "[REQ-001]" not in out

    def test_skips_markdown_heading(self, req_bol):
        # First (and only) match is in a heading; injector should skip and
        # report unmatched rather than land inside the heading.
        text = "# Chapter 1: BOL Document Ingestion & Validation\n\nBody text.\n"
        out, report = inject_citations(text, [req_bol])
        assert "# Chapter 1: BOL Document Ingestion [REQ-001]" not in out
        assert "REQ-001" in report.unmatched

    def test_uses_body_match_when_heading_match_exists(self, req_bol):
        # Heading match exists, but the body has another match — the
        # injector should skip the heading and land in the body.
        text = (
            "# Chapter 1: BOL Document Ingestion & Validation\n\n"
            "## Implementation\n\n"
            "The BOL Document Ingestion service runs on every upload.\n"
        )
        out, report = inject_citations(text, [req_bol])
        # No injection in the heading
        assert "Chapter 1: BOL Document Ingestion [REQ-001]" not in out
        # Injection in the body
        assert "service [REQ-001]" in out or "Ingestion [REQ-001] service" in out
        assert "REQ-001" in report.injected

    def test_skips_inside_single_quoted_string(self, req_bol):
        # Match is inside a quoted string literal — must skip to preserve
        # the string. Falls back to unmatched if no other location.
        text = (
            "## Notes\n\n"
            "The status field uses values like 'BOL Document Ingestion pending'.\n"
        )
        out, report = inject_citations(text, [req_bol])
        # The quoted token must remain intact
        assert "'BOL Document Ingestion pending'" in out
        # Citation NOT inserted inside the quotes
        assert "'BOL Document Ingestion [REQ-001]" not in out
        assert "REQ-001" in report.unmatched

    def test_uses_body_match_when_quote_match_exists(self, req_bol):
        text = (
            "## Notes\n\n"
            "The status field uses values like 'BOL Document Ingestion pending'.\n"
            "The BOL Document Ingestion service emits this status.\n"
        )
        out, report = inject_citations(text, [req_bol])
        # Quote intact
        assert "'BOL Document Ingestion pending'" in out
        # Citation goes in the body sentence
        assert "[REQ-001]" in out
        assert "REQ-001" in report.injected
