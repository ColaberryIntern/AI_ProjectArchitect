"""Estimate API runtime + cost for a spec-driven build.

Usage:
    python -m scripts.estimate_pipeline_runtime [N_chapters] [depth_mode] [N_must_acs]

Defaults: 10 chapters, "professional" depth, 30 must-priority ACs.

Numbers are based on:
- gpt-4o-mini pricing: $0.15 / 1M input tokens, $0.60 / 1M output tokens
- gpt-4o-mini empirical throughput: ~150 output tokens/sec sustained
- Typical TTFB (time-to-first-byte) per request: ~1.5s
- Each "word" in our chapters renders to ~1.4 tokens (markdown, code, tables)

These are first-order estimates; actual variance is +-30%. Re-run after
your first build to calibrate against real metrics emitted by the
auto_builder's BuildMetrics.
"""

from __future__ import annotations

import sys

# gpt-4o-mini pricing
INPUT_USD_PER_M = 0.15
OUTPUT_USD_PER_M = 0.60

# Throughput / latency
TOKENS_PER_SEC = 150.0
TTFB_SEC = 1.5

# Conversion
WORDS_TO_TOKENS = 1.4

# Per-chapter prompt size (input). The chapter prompt + linked
# Requirements section + cross-chapter context. Empirical bounds:
INPUT_TOKENS_BASE_CHAPTER = 1500   # original prompt template
INPUT_TOKENS_PER_LINKED_REQ = 80   # each linked Requirement adds ~80 tokens
INPUT_TOKENS_CROSS_CHAPTER_LINE = 12  # per-chapter line in the cross-chapter map

# Judge prompts
INPUT_TOKENS_AC_JUDGE_BASE = 600    # system prompt + framing
INPUT_TOKENS_AC_JUDGE_PER_AC = 60   # each AC adds ~60 tokens
OUTPUT_TOKENS_AC_JUDGE_PER_AC = 50  # judge returns one entry per AC

INPUT_TOKENS_INTERN_JUDGE_BASE = 400
INPUT_TOKENS_INTERN_JUDGE_PER_REQ = 50  # each linked req adds ~50 tokens
OUTPUT_TOKENS_INTERN_JUDGE = 200        # 4-key JSON response


# Depth mode -> output tokens per chapter (min_words * WORDS_TO_TOKENS)
DEPTH_OUTPUT_TOKENS = {
    "light":        int(800 * WORDS_TO_TOKENS),
    "standard":     int(1500 * WORDS_TO_TOKENS),
    "professional": int(5000 * WORDS_TO_TOKENS),
    "enterprise":   int(7000 * WORDS_TO_TOKENS),
}


def estimate(n_chapters: int, depth: str, n_must_acs: int) -> dict:
    out_per_chapter = DEPTH_OUTPUT_TOKENS.get(depth, DEPTH_OUTPUT_TOKENS["professional"])

    # Assumption: must-ACs are roughly evenly distributed across chapters.
    avg_acs_per_chapter = n_must_acs / max(n_chapters, 1)

    # --- Chapter generation ---
    # Average of 3 linked Requirements per chapter (an Requirement may have 1-3 ACs)
    avg_linked_per_chapter = max(1, n_must_acs / max(n_chapters, 1) / 1.5)
    cross_chapter_growth = sum(range(n_chapters))  # 0+1+2+... lines as chapters bind

    chapter_input_tokens = (
        n_chapters * INPUT_TOKENS_BASE_CHAPTER
        + n_chapters * avg_linked_per_chapter * INPUT_TOKENS_PER_LINKED_REQ
        + cross_chapter_growth * INPUT_TOKENS_CROSS_CHAPTER_LINE
    )
    chapter_output_tokens = n_chapters * out_per_chapter

    # --- AC Testability gate (one batched call per build) ---
    ac_input_tokens = INPUT_TOKENS_AC_JUDGE_BASE + n_must_acs * INPUT_TOKENS_AC_JUDGE_PER_AC
    ac_output_tokens = n_must_acs * OUTPUT_TOKENS_AC_JUDGE_PER_AC

    # --- Chapter Intern Semantic gate (one call per chapter) ---
    intern_input_tokens = (
        n_chapters * INPUT_TOKENS_INTERN_JUDGE_BASE
        + n_chapters * avg_linked_per_chapter * INPUT_TOKENS_INTERN_JUDGE_PER_REQ
        + n_chapters * out_per_chapter  # the chapter text itself goes in
    )
    intern_output_tokens = n_chapters * OUTPUT_TOKENS_INTERN_JUDGE

    # --- Totals ---
    total_input = chapter_input_tokens + ac_input_tokens + intern_input_tokens
    total_output = chapter_output_tokens + ac_output_tokens + intern_output_tokens

    cost_input = total_input / 1_000_000 * INPUT_USD_PER_M
    cost_output = total_output / 1_000_000 * OUTPUT_USD_PER_M
    cost_total = cost_input + cost_output

    # --- Time ---
    # Generation calls: n_chapters chapter calls + 1 AC judge call + n_chapters intern calls
    n_calls = n_chapters + 1 + n_chapters
    ttfb_total = n_calls * TTFB_SEC
    streaming_total = total_output / TOKENS_PER_SEC
    wall_clock_sec = ttfb_total + streaming_total

    return {
        "n_chapters": n_chapters,
        "depth": depth,
        "n_must_acs": n_must_acs,
        "tokens_input": total_input,
        "tokens_output": total_output,
        "tokens_total": total_input + total_output,
        "cost_usd": cost_total,
        "cost_breakdown": {
            "chapters_in":  chapter_input_tokens,
            "chapters_out": chapter_output_tokens,
            "ac_in":        ac_input_tokens,
            "ac_out":       ac_output_tokens,
            "intern_in":    intern_input_tokens,
            "intern_out":   intern_output_tokens,
        },
        "wall_clock_sec": wall_clock_sec,
        "wall_clock_min": wall_clock_sec / 60,
        "n_api_calls": n_calls,
    }


def format_report(est: dict) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("API RUNTIME + COST ESTIMATE — spec-driven pipeline")
    lines.append("=" * 72)
    lines.append("")
    lines.append(f"Inputs:")
    lines.append(f"  chapters:        {est['n_chapters']}")
    lines.append(f"  depth mode:      {est['depth']}")
    lines.append(f"  must-priority ACs: {est['n_must_acs']}")
    lines.append(f"  API calls:       {est['n_api_calls']}")
    lines.append("")
    lines.append("Tokens:")
    cb = est["cost_breakdown"]
    lines.append(f"  Chapter generation (in/out):  {cb['chapters_in']:>10,} / {cb['chapters_out']:>10,}")
    lines.append(f"  AC testability (in/out):      {cb['ac_in']:>10,} / {cb['ac_out']:>10,}")
    lines.append(f"  Intern semantic (in/out):     {cb['intern_in']:>10,} / {cb['intern_out']:>10,}")
    lines.append(f"  TOTAL (in / out / sum):       {est['tokens_input']:>10,} / "
                 f"{est['tokens_output']:>10,} / {est['tokens_total']:>10,}")
    lines.append("")
    lines.append("Cost:")
    lines.append(f"  Total: ${est['cost_usd']:.3f} (gpt-4o-mini)")
    lines.append("")
    lines.append("Wall-clock time:")
    lines.append(f"  ~{est['wall_clock_sec']:.0f}s ({est['wall_clock_min']:.1f} min)")
    lines.append("")
    lines.append("Notes:")
    lines.append("  * gpt-4o-mini @ $0.15/$0.60 per 1M input/output tokens")
    lines.append("  * +-30% variance is normal — re-run after your first build to calibrate")
    lines.append("  * AC testability gate is cached on identical inputs (free re-runs)")
    lines.append("  * Set OPENAI_API_KEY=\"\" to skip semantic gates (chapter generation only)")
    lines.append("=" * 72)
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    n_chapters = int(argv[1]) if len(argv) > 1 else 10
    depth = argv[2] if len(argv) > 2 else "professional"
    n_must_acs = int(argv[3]) if len(argv) > 3 else 30

    est = estimate(n_chapters, depth, n_must_acs)
    print(format_report(est))

    # Also print a small comparison table
    print()
    print("Sensitivity (default 30 ACs, this many chapters):")
    print(f"  {'depth':<14} {'5 ch':>10} {'10 ch':>10} {'15 ch':>10} {'20 ch':>10}")
    for d in ("light", "standard", "professional", "enterprise"):
        row = [d]
        for n in (5, 10, 15, 20):
            e = estimate(n, d, 30)
            row.append(f"${e['cost_usd']:.2f}/{e['wall_clock_min']:.1f}m")
        print(f"  {row[0]:<14} {row[1]:>10} {row[2]:>10} {row[3]:>10} {row[4]:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
