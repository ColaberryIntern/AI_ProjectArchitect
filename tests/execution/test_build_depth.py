"""Tests for execution/build_depth.py — depth modes, chapter requirements, scoring thresholds."""

import pytest

from execution.build_depth import (
    BUILD_PROFILES,
    CHAPTER_REQUIREMENTS,
    CHAPTER_REQUIREMENTS_DEFAULT,
    DEFAULT_DEPTH_MODE,
    DEPTH_MODE_ALIASES,
    DEPTH_MODES,
    SCORE_THRESHOLDS,
    estimate_pages,
    get_all_depth_modes,
    get_build_profile,
    get_chapter_subsections,
    get_depth_config,
    get_scoring_thresholds,
    resolve_depth_mode,
)


# ---------------------------------------------------------------------------
# DEPTH_MODES data integrity
# ---------------------------------------------------------------------------

class TestDepthModes:
    def test_four_modes_defined(self):
        assert set(DEPTH_MODES.keys()) == {"light", "standard", "professional", "enterprise"}

    def test_default_mode_is_professional(self):
        assert DEFAULT_DEPTH_MODE == "professional"

    @pytest.mark.parametrize("mode", ["light", "standard", "professional", "enterprise"])
    def test_each_mode_has_required_keys(self, mode):
        config = DEPTH_MODES[mode]
        for key in ("label", "target_pages", "max_tokens", "min_words", "min_subsections"):
            assert key in config, f"Missing key '{key}' in mode '{mode}'"

    def test_max_tokens_increase_with_depth(self):
        tokens = [DEPTH_MODES[m]["max_tokens"] for m in ("light", "standard", "professional", "enterprise")]
        assert tokens == sorted(tokens)

    def test_min_words_increase_with_depth(self):
        words = [DEPTH_MODES[m]["min_words"] for m in ("light", "standard", "professional", "enterprise")]
        assert words == sorted(words)

    def test_min_subsections_increase_with_depth(self):
        subs = [DEPTH_MODES[m]["min_subsections"] for m in ("light", "standard", "professional", "enterprise")]
        assert subs == sorted(subs)


# ---------------------------------------------------------------------------
# resolve_depth_mode() and aliases
# ---------------------------------------------------------------------------

class TestDepthModeAliases:
    def test_lite_resolves_to_light(self):
        assert resolve_depth_mode("lite") == "light"

    def test_architect_resolves_to_enterprise(self):
        assert resolve_depth_mode("architect") == "enterprise"

    def test_canonical_names_pass_through(self):
        for mode in ("light", "standard", "professional", "enterprise"):
            assert resolve_depth_mode(mode) == mode

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid depth mode"):
            resolve_depth_mode("extreme")

    def test_aliases_map_exists(self):
        assert "lite" in DEPTH_MODE_ALIASES
        assert "architect" in DEPTH_MODE_ALIASES


# ---------------------------------------------------------------------------
# BUILD_PROFILES
# ---------------------------------------------------------------------------

class TestBuildProfiles:
    def test_all_modes_have_profiles(self):
        for mode in ("light", "standard", "professional", "enterprise"):
            assert mode in BUILD_PROFILES

    def test_section_counts_increase_monotonically(self):
        counts = [BUILD_PROFILES[m]["section_count"] for m in ("light", "standard", "professional", "enterprise")]
        assert counts == sorted(counts)

    def test_light_has_5_sections(self):
        assert BUILD_PROFILES["light"]["section_count"] == 5

    def test_enterprise_has_10_sections(self):
        assert BUILD_PROFILES["enterprise"]["section_count"] == 10

    def test_word_targets_increase(self):
        targets = [BUILD_PROFILES[m]["word_target_per_chapter"] for m in ("light", "standard", "professional", "enterprise")]
        assert targets == sorted(targets)

    def test_get_build_profile_returns_copy(self):
        profile = get_build_profile("professional")
        assert isinstance(profile, dict)
        assert "section_count" in profile
        profile["section_count"] = 999
        assert BUILD_PROFILES["professional"]["section_count"] != 999

    def test_get_build_profile_resolves_aliases(self):
        profile = get_build_profile("lite")
        assert profile == get_build_profile("light")

    def test_get_build_profile_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid depth mode"):
            get_build_profile("extreme")

    @pytest.mark.parametrize("mode", ["light", "standard", "professional", "enterprise"])
    def test_each_profile_has_required_keys(self, mode):
        profile = BUILD_PROFILES[mode]
        for key in ("section_count", "subsections_range", "word_target_per_chapter",
                     "total_page_range", "intelligence_expansion_depth", "architecture_expansion_depth"):
            assert key in profile, f"Missing key '{key}' in profile '{mode}'"


# ---------------------------------------------------------------------------
# get_depth_config()
# ---------------------------------------------------------------------------

class TestGetDepthConfig:
    def test_returns_dict_for_valid_mode(self):
        config = get_depth_config("professional")
        assert isinstance(config, dict)
        assert config["label"] == "Professional"

    def test_returns_copy_not_original(self):
        config = get_depth_config("professional")
        config["label"] = "MUTATED"
        assert DEPTH_MODES["professional"]["label"] == "Professional"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid depth mode"):
            get_depth_config("extreme")

    def test_resolves_aliases(self):
        config = get_depth_config("lite")
        assert config["label"] == "Light"


# ---------------------------------------------------------------------------
# CHAPTER_REQUIREMENTS data integrity
# ---------------------------------------------------------------------------

class TestChapterRequirements:
    """Verify the 10-section enhanced chapter requirements."""

    EXPECTED_TITLES = [
        "Executive Summary",
        "Problem & Market Context",
        "User Personas & Core Use Cases",
        "Functional Requirements",
        "AI & Intelligence Architecture",
        "Non-Functional Requirements",
        "Technical Architecture & Data Model",
        "Security & Compliance",
        "Success Metrics & KPIs",
        "Roadmap & Phased Delivery",
    ]

    def test_all_10_sections_defined(self):
        for title in self.EXPECTED_TITLES:
            assert title in CHAPTER_REQUIREMENTS, f"Missing section: {title}"

    @pytest.mark.parametrize("mode", ["light", "standard", "professional", "enterprise"])
    def test_each_section_has_all_modes(self, mode):
        for title in self.EXPECTED_TITLES:
            assert mode in CHAPTER_REQUIREMENTS[title], (
                f"Section '{title}' missing mode '{mode}'"
            )

    def test_professional_has_at_least_6_subsections(self):
        for title in self.EXPECTED_TITLES:
            subs = CHAPTER_REQUIREMENTS[title]["professional"]
            assert len(subs) >= 6, (
                f"Section '{title}' professional has only {len(subs)} subsections"
            )

    def test_light_has_fewest_subsections(self):
        for title in self.EXPECTED_TITLES:
            light = len(CHAPTER_REQUIREMENTS[title]["light"])
            professional = len(CHAPTER_REQUIREMENTS[title]["professional"])
            assert light <= professional

    def test_enterprise_has_most_subsections(self):
        for title in self.EXPECTED_TITLES:
            enterprise = len(CHAPTER_REQUIREMENTS[title]["enterprise"])
            professional = len(CHAPTER_REQUIREMENTS[title]["professional"])
            assert enterprise >= professional


class TestChapterRequirementsDefault:
    """Verify the 7-section default chapter requirements."""

    EXPECTED_TITLES = [
        "System Purpose & Context",
        "Target Users & Roles",
        "Core Capabilities",
        "Non-Goals & Explicit Exclusions",
        "High-Level Architecture",
        "Execution Phases",
        "Risks, Constraints, and Assumptions",
    ]

    def test_all_7_sections_defined(self):
        for title in self.EXPECTED_TITLES:
            assert title in CHAPTER_REQUIREMENTS_DEFAULT, f"Missing section: {title}"

    @pytest.mark.parametrize("mode", ["light", "standard", "professional", "enterprise"])
    def test_each_section_has_all_modes(self, mode):
        for title in self.EXPECTED_TITLES:
            assert mode in CHAPTER_REQUIREMENTS_DEFAULT[title], (
                f"Section '{title}' missing mode '{mode}'"
            )


# ---------------------------------------------------------------------------
# get_chapter_subsections()
# ---------------------------------------------------------------------------

class TestGetChapterSubsections:
    def test_enhanced_section_returns_correct_list(self):
        subs = get_chapter_subsections("Executive Summary", "professional")
        assert "Vision & Strategy" in subs
        assert "Business Model" in subs
        assert len(subs) >= 6

    def test_default_section_returns_correct_list(self):
        subs = get_chapter_subsections("Core Capabilities", "standard")
        assert "Features" in subs

    def test_unknown_title_returns_generic_list(self):
        subs = get_chapter_subsections("My Custom Chapter", "professional")
        assert len(subs) >= DEPTH_MODES["professional"]["min_subsections"]
        assert "Overview" in subs

    def test_returns_copy_not_original(self):
        subs = get_chapter_subsections("Executive Summary", "professional")
        original_len = len(CHAPTER_REQUIREMENTS["Executive Summary"]["professional"])
        subs.append("MUTATED")
        assert len(CHAPTER_REQUIREMENTS["Executive Summary"]["professional"]) == original_len

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid depth mode"):
            get_chapter_subsections("Executive Summary", "extreme")

    def test_light_returns_fewer_than_professional(self):
        light = get_chapter_subsections("Executive Summary", "light")
        professional = get_chapter_subsections("Executive Summary", "professional")
        assert len(light) < len(professional)

    def test_resolves_aliases(self):
        lite = get_chapter_subsections("Executive Summary", "lite")
        light = get_chapter_subsections("Executive Summary", "light")
        assert lite == light


# ---------------------------------------------------------------------------
# get_scoring_thresholds()
# ---------------------------------------------------------------------------

class TestGetScoringThresholds:
    def test_returns_thresholds_professional(self):
        thresholds = get_scoring_thresholds("professional")
        assert thresholds["min_words"] == 5000
        assert thresholds["min_subsections"] == 6
        assert thresholds["incomplete_threshold"] == 40
        assert thresholds["complete_threshold"] == 70

    def test_enterprise_complete_threshold_is_75(self):
        thresholds = get_scoring_thresholds("enterprise")
        assert thresholds["complete_threshold"] == 75

    def test_light_has_lower_complete_threshold(self):
        light = get_scoring_thresholds("light")
        enterprise = get_scoring_thresholds("enterprise")
        assert light["complete_threshold"] < enterprise["complete_threshold"]

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid depth mode"):
            get_scoring_thresholds("extreme")

    @pytest.mark.parametrize("mode", ["light", "standard", "professional", "enterprise"])
    def test_all_modes_return_valid_thresholds(self, mode):
        t = get_scoring_thresholds(mode)
        assert t["min_words"] > 0
        assert t["min_subsections"] > 0
        assert t["incomplete_threshold"] < t["complete_threshold"]

    def test_resolves_aliases(self):
        lite = get_scoring_thresholds("lite")
        light = get_scoring_thresholds("light")
        assert lite == light


# ---------------------------------------------------------------------------
# estimate_pages()
# ---------------------------------------------------------------------------

class TestEstimatePages:
    def test_500_words_is_1_page(self):
        assert estimate_pages(500) == 1

    def test_2500_words_is_5_pages(self):
        assert estimate_pages(2500) == 5

    def test_0_words_is_0_pages(self):
        assert estimate_pages(0) == 0

    def test_negative_words_is_0_pages(self):
        assert estimate_pages(-100) == 0

    def test_499_words_is_still_1_page(self):
        assert estimate_pages(499) == 1

    def test_large_count(self):
        assert estimate_pages(50000) == 100


# ---------------------------------------------------------------------------
# get_all_depth_modes()
# ---------------------------------------------------------------------------

class TestGetAllDepthModes:
    def test_returns_all_four(self):
        modes = get_all_depth_modes()
        assert set(modes.keys()) == {"light", "standard", "professional", "enterprise"}

    def test_returns_copies(self):
        modes = get_all_depth_modes()
        modes["professional"]["label"] = "MUTATED"
        assert DEPTH_MODES["professional"]["label"] == "Professional"


# ---------------------------------------------------------------------------
# Score threshold constants (per-depth dict)
# ---------------------------------------------------------------------------

class TestScoreThresholds:
    def test_all_modes_present(self):
        for mode in ("light", "standard", "professional", "enterprise"):
            assert mode in SCORE_THRESHOLDS

    def test_incomplete_below_complete_for_each_mode(self):
        for mode in ("light", "standard", "professional", "enterprise"):
            assert SCORE_THRESHOLDS[mode]["incomplete"] < SCORE_THRESHOLDS[mode]["complete"]

    def test_enterprise_values(self):
        assert SCORE_THRESHOLDS["enterprise"]["incomplete"] == 40
        assert SCORE_THRESHOLDS["enterprise"]["complete"] == 75

    def test_light_values(self):
        assert SCORE_THRESHOLDS["light"]["incomplete"] == 35
        assert SCORE_THRESHOLDS["light"]["complete"] == 55
