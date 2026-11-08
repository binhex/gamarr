"""Tests for gamarr acquisition pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast
from unittest.mock import MagicMock, patch

import pytest  # noqa: TC002 -- needed at runtime for @pytest.mark.xfail decorator

from gamarr.pipeline import AcquisitionConfig, run_acquisition
from gamarr.utils import month_of, subtract_months, year_month_to_date

if TYPE_CHECKING:
    from pathlib import Path


def _passing_detail_client() -> MagicMock:
    """Return a Metacritic stand-in whose detail lookup passes verification."""
    from gamarr.metacritic import ScoreResult

    mc = MagicMock()
    mc.lookup_game.return_value = ScoreResult(
        title="Game",
        slug="game",
        metascore=88.0,
        metascore_review_count=40,
        user_score=8.4,
        user_review_count=40,
        passed=True,
    )
    return mc


class TestPageTitleHasDlcKeywords:
    """Tests for _page_title_has_dlc_keywords helper."""

    def test_has_dlc_keywords_all_dlcs(self) -> None:
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords("Total War: WARHAMMER 2 – v1.9.2 + All DLCs [FitGirl Repack]") is True

    def test_has_dlc_keywords_counted_dlcs(self) -> None:
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords("Game Name v1.0 + 15 DLCs + Bonuses [FitGirl Repack]") is True

    def test_has_dlc_keywords_no_dlcs(self) -> None:
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords("Game Name v1.0 [FitGirl Repack]") is False

    def test_has_dlc_keywords_none_title(self) -> None:
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords(None) is False

    def test_has_dlc_keywords_empty_title(self) -> None:
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords("") is False

    def test_has_dlc_keywords_partial_match_avoided(self) -> None:
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords("Game + All DLCsPECIAL Edition [FitGirl Repack]") is False

    def test_has_keywords_all_expansions(self) -> None:
        """Page title containing '+ All Expansions' returns True."""
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords("Game Name v1.0 + All Expansions [FitGirl Repack]") is True

    def test_has_keywords_counted_expansions(self) -> None:
        """Page title containing '+ 4 Expansions' returns True."""
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords("Game v2.0 + 4 Expansions [FitGirl Repack]") is True

    def test_has_keywords_singular_expansion(self) -> None:
        """Page title containing '+ 1 Expansion' (singular) returns True."""
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords("Game + 1 Expansion [FitGirl Repack]") is True


class TestArticleContainsAllDlcs:
    """Tests for _article_contains_all_dlcs helper."""

    def test_contains_all_dlcs_basic(self) -> None:
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs("This repack includes all DLCs released so far.") is True

    def test_contains_all_dlcs_available(self) -> None:
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs("All available DLCs are bundled in this release.") is True

    def test_contains_all_dlcs_existing(self) -> None:
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs("Includes all existing DLCs and updates.") is True

    def test_contains_all_dlcs_includes_all(self) -> None:
        from gamarr.pipeline import _article_contains_all_dlcs

        assert (
            _article_contains_all_dlcs("Includes all DLCs: Curse of the Vampire Coast, Rise of the Tomb Kings...")
            is True
        )

    def test_contains_all_dlcs_casing(self) -> None:
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs("This pack includes ALL DLCS ever released.") is True

    def test_contains_all_dlcs_no_match(self) -> None:
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs("This repack is based on the latest Steam release v1.9.2.") is False

    def test_contains_all_dlcs_none(self) -> None:
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs(None) is False

    def test_contains_all_dlcs_empty(self) -> None:
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs("") is False

    def test_contains_all_dlcs_partial_match_avoided(self) -> None:
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs("The installer validates all dlcschemas before extracting.") is False

    def test_contains_all_expansions_basic(self) -> None:
        """Article body with 'all expansions' returns True."""
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs("This repack includes all expansions released so far.") is True

    def test_contains_all_expansions_available(self) -> None:
        """Article body with 'all available expansions' returns True."""
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs("All available expansions are bundled here.") is True

    def test_contains_all_expansions_existing(self) -> None:
        """Article body with 'all existing expansions' returns True."""
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs("Includes all existing expansions.") is True


class TestDeepSearchDlcMatching:
    """Integration tests for DLC-aware deep search via _deep_search_article_body."""

    def test_page_title_all_dlcs(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [
                {
                    "title": "Total War Warhammer 2",
                    "url": "https://fitgirl-repacks.site/total-war-warhammer-2/",
                    "magnet": None,
                },
            ],
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=(
                "Total War: WARHAMMER 2 – v1.9.2 + All DLCs [FitGirl Repack]",
                "Some body text.",
            ),
        ):
            result = _deep_search_article_body(
                db,
                "fitgirl",
                normalise_for_compare("Total War: WARHAMMER II - Curse of the Vampire Coast"),
                pending_title="Total War: WARHAMMER II - Curse of the Vampire Coast",
            )
        db.close()
        assert len(result) == 1

    def test_page_title_counted_dlcs(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [
                {
                    "title": "Game Name Ultimate",
                    "url": "https://fitgirl-repacks.site/game-name-ultimate/",
                    "magnet": None,
                },
            ],
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=("Game Name Ultimate v1.0 + 15 DLCs + Bonuses [FitGirl Repack]", None),
        ):
            result = _deep_search_article_body(
                db,
                "fitgirl",
                normalise_for_compare("Game Name Ultimate - The Lost Chapters"),
                pending_title="Game Name Ultimate - The Lost Chapters",
            )
        db.close()
        assert len(result) == 1

    def test_article_body_all_dlcs_fallback(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [
                {
                    "title": "Base Game Edition",
                    "url": "https://fitgirl-repacks.site/base-game-edition/",
                    "magnet": None,
                },
            ],
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=(
                "Base Game Edition [FitGirl Repack]",
                "This repack includes all existing DLCs. Requires 60 GB.",
            ),
        ):
            result = _deep_search_article_body(
                db,
                "fitgirl",
                normalise_for_compare("Base Game Edition - Expansion Pack"),
                pending_title="Base Game Edition - Expansion Pack",
            )
        db.close()
        assert len(result) == 1

    def test_original_named_dlc_still_works(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [
                {"title": "Dark Souls Iii", "url": "https://fitgirl-repacks.site/dark-souls-3/", "magnet": None},
            ],
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=(
                "Dark Souls III [FitGirl Repack]",
                "Repack features: Dark Souls III - The Ringed City DLC, Ashes of Ariandel included.",
            ),
        ):
            result = _deep_search_article_body(
                db,
                "fitgirl",
                normalise_for_compare("Dark Souls III: The Ringed City"),
                pending_title="Dark Souls III: The Ringed City",
            )
        db.close()
        assert len(result) == 1

    def test_no_match_when_no_dlc_indicators(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [
                {"title": "Simple Game", "url": "https://fitgirl-repacks.site/simple-game/", "magnet": None},
            ],
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=(
                "Simple Game [FitGirl Repack]",
                "Based on Steam release v1.0. Requires 20 GB.",
            ),
        ):
            result = _deep_search_article_body(
                db,
                "fitgirl",
                normalise_for_compare("Simple Game - Unrelated Expansion"),
                pending_title="Simple Game - Unrelated Expansion",
            )
        db.close()
        assert result == []

    def test_token_overlap_with_only_generic_words_and_dlcs_boilerplate_is_rejected(self, tmp_path: Path) -> None:
        """Generic overlap + 'All DLCs' boilerplate is not evidence (real CoH2 mismatch)."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [
                {
                    "title": "Company Of Heroes 2 Master Collection",
                    "url": "https://fitgirl-repacks.site/company-of-heroes-2-master-collection/",
                    "magnet": None,
                },
            ],
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=(
                "Company of Heroes 2: Master Collection - v4.0.0.21748 + All DLCs - FitGirl Repacks",
                "Repack features: All DLCs included. Requires 30 GB.",
            ),
        ):
            result = _deep_search_article_body(
                db,
                "fitgirl",
                normalise_for_compare("Metal Gear Solid: Master Collection Vol. 2"),
                pending_title="Metal Gear Solid: Master Collection Vol. 2",
            )
        db.close()
        assert result == [], "shared generic words plus 'All DLCs' boilerplate must not match"

    def test_candidate_that_names_the_game_wins_over_url_order(self, tmp_path: Path) -> None:
        """The game-naming candidate wins even when three decoys sort first by URL.

        Only three candidate pages are ever fetched, so this only finds the right
        repack because candidates are ranked by title overlap rather than by URL.
        """
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        mgs_url = "https://fitgirl-repacks.site/metal-gear-solid-master-collection-vol-1-vol-2-bonus-content/"
        decoys = [
            (
                "Company Of Heroes 2 Master Collection",
                "https://fitgirl-repacks.site/company-of-heroes-2-master-collection/",
            ),
            ("Dragons Dogma 2 Master Collection", "https://fitgirl-repacks.site/dragons-dogma-2-master-collection/"),
            ("Elex 2 Master Collection", "https://fitgirl-repacks.site/elex-2-master-collection/"),
        ]
        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": title, "url": url, "magnet": None} for title, url in decoys]
            + [
                {
                    "title": "Metal Gear Solid Master Collection Vol 1 Vol 2 Bonus Content",
                    "url": mgs_url,
                    "magnet": None,
                },
            ],
        )

        def fake_content(url: str) -> tuple[str, str]:
            if url == mgs_url:
                return (
                    "METAL GEAR SOLID: MASTER COLLECTION Vol.1 & Vol.2 BONUS CONTENT - v1.5.1",
                    "This repack includes Metal Gear Solid: Master Collection Vol. 1 and Vol. 2 bonus content.",
                )
            return (
                "Master Collection 2 - v1.0.0 + All DLCs - FitGirl Repacks",
                "Repack features: All DLCs included. Requires 30 GB.",
            )

        with patch("gamarr.pipeline._fetch_fitgirl_page_content", side_effect=fake_content):
            result = _deep_search_article_body(
                db,
                "fitgirl",
                normalise_for_compare("Metal Gear Solid: Master Collection Vol. 2"),
                pending_title="Metal Gear Solid: Master Collection Vol. 2",
            )
        db.close()
        assert len(result) == 1, f"expected the game-naming candidate, got {result}"
        assert result[0]["url"] == mgs_url, f"expected the game-naming candidate, got {result[0]['url']}"

    def test_substring_that_runs_into_a_number_is_rejected(self, tmp_path: Path) -> None:
        """A numeral continuing into another digit is a different game (Battlefield 2 vs 2042)."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Battlefield 2", "url": "https://fitgirl-repacks.site/battlefield-2/", "magnet": None}],
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=("Battlefield 2 - v1.0 + All DLCs - FitGirl Repacks", "Repack features: All DLCs included."),
        ):
            result = _deep_search_article_body(
                db,
                "fitgirl",
                normalise_for_compare("Battlefield 2042"),
                pending_title="Battlefield 2042",
            )
        db.close()
        assert result == [], "Battlefield 2 must not satisfy Battlefield 2042"

    def test_expansion_match_without_article_evidence_is_rejected(self, tmp_path: Path) -> None:
        """A page-title DLC keyword alone must not match a token-overlap candidate."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [
                {
                    "title": "Dungeons And Dragons Neverwinter Nights 2 Enhanced Edition",
                    "url": "https://fitgirl-repacks.site/dungeons-and-dragons-neverwinter-nights-2-enhanced-edition/",
                    "magnet": None,
                },
            ],
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=(
                "Dungeons & Dragons Neverwinter Nights 2: Enhanced Edition – v1.110 + All Expansions [FitGirl Repack]",
                "This repack includes all expansions. Requires 60 GB.",
            ),
        ):
            result = _deep_search_article_body(
                db,
                "fitgirl",
                normalise_for_compare("Neverwinter Nights 2: Mask of The Betrayer"),
                pending_title="Neverwinter Nights 2: Mask of The Betrayer",
            )
        db.close()
        assert result == [], "the article must name the expansion before matching"

    def test_token_overlap_candidate_named_in_article_matches(self, tmp_path: Path) -> None:
        """A token-overlap candidate whose article names the pending game matches."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [
                {
                    "title": "Metal Gear Solid Master Collection Vol 1 Vol 2 Bonus Content",
                    "url": "https://fitgirl-repacks.site/mgs-collection-vol-1-2/",
                    "magnet": None,
                },
            ],
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=(
                "METAL GEAR SOLID: MASTER COLLECTION Vol.1 & Vol.2 - FitGirl Repacks",
                "Includes Metal Gear Solid: Master Collection Vol. 2 bonus content.",
            ),
        ):
            result = _deep_search_article_body(
                db,
                "fitgirl",
                normalise_for_compare("Metal Gear Solid: Master Collection Vol. 2"),
                pending_title="Metal Gear Solid: Master Collection Vol. 2",
            )
        db.close()
        assert len(result) == 1

    def test_token_overlap_candidate_without_article_text_is_rejected(self, tmp_path: Path) -> None:
        """No article body means no evidence, so a token-overlap candidate is rejected."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [
                {
                    "title": "Metal Gear Solid Master Collection Vol 1 Vol 2 Bonus Content",
                    "url": "https://fitgirl-repacks.site/mgs-collection-vol-1-2/",
                    "magnet": None,
                },
            ],
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=("METAL GEAR SOLID: MASTER COLLECTION - FitGirl Repacks", None),
        ):
            result = _deep_search_article_body(
                db,
                "fitgirl",
                normalise_for_compare("Metal Gear Solid: Master Collection Vol. 2"),
                pending_title="Metal Gear Solid: Master Collection Vol. 2",
            )
        db.close()
        assert result == []

    def test_substring_candidate_without_dlc_evidence_is_rejected(self, tmp_path: Path) -> None:
        """A substring candidate with no page/body DLC evidence does not match."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Dark Souls Iii", "url": "https://fitgirl-repacks.site/dark-souls-3/", "magnet": None}],
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=("Dark Souls III [FitGirl Repack]", None),
        ):
            result = _deep_search_article_body(
                db,
                "fitgirl",
                normalise_for_compare("Dark Souls III: The Ringed City"),
                pending_title="Dark Souls III: The Ringed City",
            )
        db.close()
        assert result == []

    def test_page_title_all_expansions_substring_candidate(self, tmp_path: Path) -> None:
        """When page <title> contains '+ All Expansions', a substring candidate matches.

        The page-title DLC fast path applies to substring candidates only; the
        token-overlap counterpart of this scenario is asserted to be rejected in
        test_expansion_match_without_article_evidence_is_rejected.
        """
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [
                {
                    "title": "Dungeons And Dragons Neverwinter Nights 2 Enhanced Edition",
                    "url": "https://fitgirl-repacks.site/dungeons-and-dragons-neverwinter-nights-2-enhanced-edition/",
                    "magnet": None,
                },
            ],
        )

        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=(
                "Dungeons & Dragons Neverwinter Nights 2: Enhanced Edition – v1.110 + All Expansions [FitGirl Repack]",
                "This repack includes all expansions. Requires 60 GB.",
            ),
        ):
            result = _deep_search_article_body(
                db,
                "fitgirl",
                normalise_for_compare(
                    "Dungeons And Dragons Neverwinter Nights 2: Enhanced Edition - Mask of The Betrayer"
                ),
                pending_title="Dungeons And Dragons Neverwinter Nights 2: Enhanced Edition - Mask of The Betrayer",
            )
        db.close()
        assert len(result) == 1


class TestSharedTokenCount:
    """Tests for the candidate gate: shared word tokens between two titles."""

    @staticmethod
    def _count(title_a: str, title_b: str) -> int:
        from gamarr.pipeline import _shared_token_count, _tokenize_title

        return _shared_token_count(_tokenize_title(title_a), _tokenize_title(title_b))

    def test_three_shared_tokens_meet_the_candidate_threshold(self) -> None:
        """Two titles sharing 3 word tokens qualify as candidates."""
        from gamarr.pipeline import _DEEP_SEARCH_MIN_SHARED_TOKENS

        shared = self._count(
            "Dungeons & Dragons Neverwinter Nights 2 Enhanced Edition",
            "Neverwinter Nights 2: Mask of The Betrayer",
        )
        assert shared == 3
        assert shared >= _DEEP_SEARCH_MIN_SHARED_TOKENS

    def test_roman_numeral_tokens_are_counted(self) -> None:
        from gamarr.pipeline import _DEEP_SEARCH_MIN_SHARED_TOKENS

        shared = self._count("Dark Souls III", "Dark Souls 3: The Ringed City")
        assert shared == 3, "Roman numeral conversion should make III and 3 the same token"
        assert shared >= _DEEP_SEARCH_MIN_SHARED_TOKENS

    def test_generic_words_are_counted_like_any_other(self) -> None:
        """Generic overlap IS counted; the evidence rule is what rejects it."""
        from gamarr.pipeline import _DEEP_SEARCH_MIN_SHARED_TOKENS

        shared = self._count("Company Of Heroes 2 Master Collection", "Metal Gear Solid: Master Collection Vol. 2")
        assert shared == 3
        assert shared >= _DEEP_SEARCH_MIN_SHARED_TOKENS

    def test_two_shared_tokens_fall_below_the_threshold(self) -> None:
        from gamarr.pipeline import _DEEP_SEARCH_MIN_SHARED_TOKENS

        shared = self._count("Star Wars Battlefront II", "Star Wars Jedi: Fallen Order")
        assert shared == 2
        assert shared < _DEEP_SEARCH_MIN_SHARED_TOKENS

    def test_one_shared_token_falls_below_the_threshold(self) -> None:
        from gamarr.pipeline import _DEEP_SEARCH_MIN_SHARED_TOKENS

        shared = self._count("Diablo 3", "Diablo 4: Vessel of Hatred")
        assert shared == 1
        assert shared < _DEEP_SEARCH_MIN_SHARED_TOKENS

    def test_stop_words_do_not_count(self) -> None:
        """Unrelated games sharing only stop words and a numeral do not qualify."""
        from gamarr.pipeline import _DEEP_SEARCH_MIN_SHARED_TOKENS

        shared = self._count(
            "Atelier Sophie 2 The Alchemist Of The Mysterious Dream",
            "Neverwinter Nights 2: Mask of The Betrayer",
        )
        assert shared == 1
        assert shared < _DEEP_SEARCH_MIN_SHARED_TOKENS

    def test_completely_different_titles_share_nothing(self) -> None:
        assert self._count("Minecraft", "The Witcher 3: Wild Hunt") == 0


class TestAcquisitionConfig:
    """AcquisitionConfig construction."""

    def test_defaults(self) -> None:
        cfg = AcquisitionConfig(
            min_criticscore=75,
            min_criticscore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            max_pages=12,  # ~84 days, roughly equivalent to 90 days
        )
        assert cfg.min_criticscore == 75


class TestMaxCycleWeeks:
    """Tests for max_cycle_pages integration."""

    def test_max_cycle_pages_lower_than_max_pages_uses_cycle_cutoff(self, tmp_path: Path) -> None:
        """When max_cycle_pages < max_pages, AcquisitionConfig stores both separately."""
        from gamarr.pipeline import AcquisitionConfig

        cfg = AcquisitionConfig(
            min_criticscore=75,
            min_criticscore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            max_pages=52,
            max_cycle_pages=100,
        )
        # The cycle weeks are separate
        assert cfg.max_cycle_pages == 100

    def test_max_cycle_pages_runs_with_params(self) -> None:
        """Acquisition runs without error when max_cycle_pages < max_pages."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        with (
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
        ):
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source
            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                db_path=":memory:",
                max_pages=52,
                max_cycle_pages=100,
            )
            assert isinstance(results, list)

    def test_max_cycle_pages_limits_scan_recent_games(self) -> None:
        """When max_cycle_pages is set, scan_recent_games receives it as max_pages."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        with (
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
        ):
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source
            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                db_path=":memory:",
                max_pages=280,
                max_cycle_pages=4,
            )

            # Verify scan_recent_games was called with max_cycle_pages,
            # not max_pages.  max_pages=280 should NOT appear as the
            # per-scan limit — max_cycle_pages=4 should.
            # Multiple years are scanned now; check the last call's max_pages.
            _, kwargs = mock_mc.scan_recent_games.call_args
            assert kwargs["max_pages"] == 4

    def test_max_cycle_pages_default_falls_back_to_max_pages(self) -> None:
        """When max_cycle_pages is 0 (default), max_pages is used as the limit."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        with (
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
        ):
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source
            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                db_path=":memory:",
                max_pages=500,
                max_cycle_pages=0,
            )

            # When max_cycle_pages is 0 (default/unlimited), fall back to
            # max_pages as the total ceiling.
            # Multiple years are scanned now; check the last call's max_pages.
            _, kwargs = mock_mc.scan_recent_games.call_args
            assert kwargs["max_pages"] == 500


class TestRunAcquisition:
    """End-to-end acquisition pipeline."""

    def test_no_entries_returns_empty(self) -> None:
        """run_acquisition returns [] when no browse results and no pending games."""
        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = []
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
            )
            assert results == []

    def test_qbt_not_connected_skips(self) -> None:
        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source

            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = False
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
            )
            assert results == []

    def test_run_acquisition_does_not_process_fitgirl_rss_entries(self) -> None:
        """Metacritic-first: FitGirl RSS entries must NOT drive per-entry lookups.

        Reproduces the bug where run_acquisition() iterated FitGirl RSS
        entries and looked each one up on Metacritic — the opposite of
        the intended Metacritic-first flow.

        Even when fetch_new() returns 7 new entries (matching the bug
        report), the pipeline must NOT call fetch_new() and must NOT
        call mc.lookup_game per FitGirl RSS entry. The sitemap is the
        only Metacritic-side state used for matching, and FitGirl RSS
        entries are not a discovery source.
        """
        from gamarr.models import GameEntry

        entries = [
            GameEntry(
                title=f"FitGirl Game {i}",
                source_title=f"FitGirl Game {i} [Repack]",
                source="fitgirl",
                platform="pc",
                magnet_url="",
                source_url=f"http://example.com/fitgirl-game-{i}",
            )
            for i in range(7)
        ]

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = entries
            mock_source_cls.return_value = mock_source

            # Browse Metacritic returns no games, so no pending are added
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
            )

            # The Metacritic-first flow must NOT call fetch_new() at all.
            mock_source.fetch_new.assert_not_called()
            # The Metacritic-first flow must NOT call lookup_game per
            # FitGirl RSS entry. Metacritic browse already returned [] so
            # the metacritic-first flow produces no results.
            mock_mc.lookup_game.assert_not_called()
            # No results produced (no browse results, no pending matches)
            assert results == []

    def test_run_acquisition_with_library_paths_passes_through(self) -> None:
        """run_acquisition should wire library_paths into the discovery path."""
        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.library.os.path.isdir", return_value=False),
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
                library_paths=["/games"],
            )
            # Metacritic browse should be called (multiple years now)
            assert mock_mc.scan_recent_games.called
            # Sitemap is NOT fetched when there are no pending games to match
            mock_source.fetch_sitemap.assert_not_called()

    def test_run_acquisition_skips_sitemap_when_no_verified_pending(self, tmp_path: Path) -> None:
        """No verified pending game must NOT trigger a sitemap fetch.

        The queue is verified-only, so an empty queue is the ordinary
        "nothing to match" state: a cycle with no new browse games and no
        verified pending rows never fetches a source sitemap.
        """
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database

        db_path = str(tmp_path / "test.db")
        Database(db_path).close()

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            # Browse returns no new games, so the queue stays empty.
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                platform="pc",
                db_path=db_path,
                qbt_host="localhost",
                qbt_port=8080,
                min_criticscore=75,
                min_user_score=7.5,
                min_user_reviews=10,
                min_criticscore_reviews=5,
            )

            mock_source.fetch_sitemap.assert_not_called()
            assert results == []

        verify_db = Database(db_path)
        assert verify_db.get_pending(platform="pc") == [], "the cycle must not queue anything"
        assert verify_db.has_verified_pending(platform="pc") is False
        verify_db.close()

    def test_source_error_does_not_block_other_source_matches(self, mocker: Any) -> None:
        """When one source errors during fetch_sitemap, other sources still match.

        Currently FETCH ALL → MATCH ALL. If FreeGOG crashes during fetch_sitemap,
        FitGirl's matching phase never runs. The fix: per-source index+match
        so FitGirl matches before FreeGOG gets a chance to crash.
        """
        import types

        from gamarr.pipeline import run_acquisition

        mock_db = mocker.patch("gamarr.pipeline.Database")
        mock_db_instance = mock_db.return_value
        mock_db_instance.has_verified_pending.return_value = True
        mock_db_instance.sum_scanned_pages.return_value = 0
        mock_db_instance.get_last_scanned_page.return_value = 0
        mock_db_instance.get_pending.return_value = []

        mock_qbt = mocker.patch("gamarr.pipeline.QBittorrentClient")
        mock_qbt_instance = mock_qbt.return_value
        mock_qbt_instance.is_connected.return_value = True

        mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient")
        mock_mc_instance = mock_mc.return_value
        mock_mc_instance.scan_recent_games.return_value = []

        # Track which sources were matched (never call the real function)
        matched_sources = []

        def _track_match(*args: Any, source_name: str = "", **kwargs: Any) -> list:
            matched_sources.append(source_name)
            return []

        mocker.patch("gamarr.pipeline._match_pending_games", side_effect=_track_match)

        # FitGirl: succeeds
        mock_fitgirl = MagicMock()
        mocker.patch("gamarr.pipeline.FitGirlSource", return_value=mock_fitgirl)

        # FreeGOG: raises during fetch_sitemap (simulating the slow/scraping failure)
        mock_freegog = MagicMock()
        mock_freegog.fetch_sitemap.side_effect = Exception("FreeGOG scraper failed")
        mocker.patch("gamarr.pipeline.FreeGOGSource", return_value=mock_freegog)

        fitgirl_entry = types.SimpleNamespace()
        fitgirl_entry.name = "fitgirl"
        fitgirl_entry.enabled = True
        fitgirl_entry.platform = "pc"
        fitgirl_entry.cache_hours = 6
        fitgirl_entry.reject_keywords = []
        fitgirl_entry.max_queue_days = 60

        freegog_entry = types.SimpleNamespace()
        freegog_entry.name = "freegog"
        freegog_entry.enabled = True
        freegog_entry.platform = "pc"
        freegog_entry.cache_hours = 6
        freegog_entry.reject_keywords = []
        freegog_entry.max_queue_days = 60

        with pytest.raises(Exception, match="FreeGOG scraper failed"):
            run_acquisition(
                db_path=":memory:",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
                min_criticscore=75,
                download_sites=[fitgirl_entry, freegog_entry],
            )

        # RED phase: this assertion FAILS because the old sequential code
        # crashes at FreeGOG's fetch_sitemap before _match_pending_games
        # is ever called for FitGirl.
        # GREEN phase: per-source matching runs FitGirl match before
        # FreeGOG's fetch_sitemap, so this passes.
        assert "fitgirl" in matched_sources


class TestGamePassesThresholds:
    """_game_passes_thresholds browse-phase score checks."""

    def test_browse_game_passes_thresholds_with_high_browse_scores(self) -> None:
        """Browse-page games with inflated scores should pass the threshold check."""
        from gamarr.pipeline import _game_passes_thresholds

        game = {
            "title": "Some Game",
            "slug": "some-game",
            "score": 1478.0,  # inflated browse metric
            "user_rating": 2007.0,  # inflated browse metric
        }
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        assert _game_passes_thresholds(game, thresholds) is True

    def test_browse_game_fails_when_user_score_below_threshold(self) -> None:
        """Even with missing user_review_count, a low user_score must still fail."""
        from gamarr.pipeline import _game_passes_thresholds

        game = {
            "title": "Low Score Game",
            "slug": "low-score",
            "score": 85.0,
            "critic_review_count": 20,
            "user_rating": 3.0,  # below threshold
            "user_review_count": None,
            "release_date": "2026-06-01",
        }
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        assert _game_passes_thresholds(game, thresholds) is False

    def test_browse_game_fails_when_metascore_missing(self) -> None:
        """A game with no metascore should fail the threshold check."""
        from gamarr.pipeline import _game_passes_thresholds

        game = {
            "title": "No Metascore Game",
            "slug": "no-ms",
            "score": None,
            "user_rating": 8.0,
        }
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        assert _game_passes_thresholds(game, thresholds) is False

    def test_browse_game_fails_when_browse_score_missing(self) -> None:
        """A game with no browse scores should fail the threshold check."""
        from gamarr.pipeline import _game_passes_thresholds

        game = {
            "title": "No Score Game",
            "slug": "no-score",
            "score": None,
            "user_rating": None,
        }
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        assert _game_passes_thresholds(game, thresholds) is False


class TestEscapeMarkup:
    """_escape_markup helper for log output."""

    def test_escape_markup_angle_brackets(self) -> None:
        from gamarr.pipeline import _escape_markup

        assert _escape_markup("<title>") == "\\<title\\>"
        assert _escape_markup("plain text") == "plain text"
        assert _escape_markup(42) == "42"


class TestEscapeOr:
    """_escape_or helper for conditional escaping."""

    def test_escape_or_escapes_value(self) -> None:
        from gamarr.pipeline import _escape_or

        assert _escape_or("hello", "N/A") == "hello"
        assert _escape_or("<b>bold</b>", "N/A") == "\\<b\\>bold\\</b\\>"

    def test_escape_or_none_returns_default(self) -> None:
        from gamarr.pipeline import _escape_or

        assert _escape_or(None, "?") == "?"
        assert _escape_or(None, "N/A") == "N/A"


class TestSourceDisplay:
    """_source_display helper for readable source names in log output."""

    def test_source_display_fitgirl(self) -> None:
        from gamarr.pipeline import _source_display

        assert _source_display("fitgirl") == "FitGirl"

    def test_source_display_freegog(self) -> None:
        from gamarr.pipeline import _source_display

        assert _source_display("freegog") == "FreeGOG"

    def test_source_display_unknown_falls_back_to_title(self) -> None:
        from gamarr.pipeline import _source_display

        assert _source_display("steam") == "Steam"


class TestMetacriticBrowse:
    """Metacritic browse discovery phase."""

    def test_added_pending_game_is_debug_not_info(self) -> None:
        """'Added pending game' must be at DEBUG, not INFO, to avoid console spam."""
        import io

        from loguru import logger

        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(":memory:")
        browse_games = [
            {
                "title": "Some Game",
                "slug": "some-game",
                "score": 85,
                "critic_review_count": 15,
                "user_rating": 8.5,
                "user_review_count": 50,
                "release_date": "2026-06-01",
            },
        ]
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        # Capture Loguru output to check log level
        buf = io.StringIO()
        logger_id = logger.add(buf, format="{level.name}:{message}", colorize=False)
        try:
            _process_browse_games(browse_games, "pc", db, thresholds, mc=_passing_detail_client())
        finally:
            logger.remove(logger_id)

        output = buf.getvalue()
        assert "Added pending game" in output, "The pending message should appear"
        assert output.startswith("DEBUG"), f"Should be DEBUG, not INFO; got: {output[:60]}"
        # Also verify behavior is correct (game was added)
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        db.close()

    def test_deliver_match_logs_genre(self, tmp_path: Path) -> None:
        """When a game is delivered via JIT verify, the log must include genres."""
        import datetime
        import io
        import types
        from unittest.mock import MagicMock

        from loguru import logger

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="gothic-1-remake",
            game_title="Gothic 1 Remake",
            platform="pc",
            metascore=76.0,
            metascore_reviews=12,
            user_score=8.7,
            user_reviews=308,
            release_date="2026-06-05",
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="gothic-1-remake",
            metascore=76.0,
            metascore_reviews=12,
            user_score=8.7,
            user_reviews=308,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Gothic 1 Remake", "url": "https://fitgirl-repacks.site/gothic-1-remake/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        # Mock MC lookup to return a ScoreResult with genres
        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=76.0,
            metascore_review_count=12,
            user_score=8.7,
            user_review_count=308,
            title="Gothic 1 Remake",
            slug="gothic-1-remake",
            genres=["Action RPG", "Open-World"],
            must_play=False,
            release_date="2026-06-05",
        )

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        # Capture Loguru output
        buf = io.StringIO()
        logger_id = logger.add(buf, format="{message}", colorize=False)
        try:
            matched = _match_pending_games(
                db,
                qbt=mock_qbt,
                magnet_fetcher=magnet_fetcher,
                mc=mock_mc,
                thresholds=thresholds,
            )
        finally:
            logger.remove(logger_id)

        output = buf.getvalue()
        assert len(matched) == 1
        assert matched[0]["result"] == "Passed"
        # The log must include the genre, not "N/A"
        assert "Action RPG" in output, f"Genres should appear in log: {output}"
        assert "N/A" not in output.split("Genre:")[1].split("|")[0], "Genre should not be N/A"
        db.close()

    def test_deliver_match_logs_game_details(self, tmp_path: Path) -> None:
        """When a game is delivered, the log must include metascore, user score, and release date."""
        import datetime
        import io
        from unittest.mock import MagicMock

        from loguru import logger

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=96.0,
            metascore_reviews=120,
            user_score=8.5,
            user_reviews=5000,
            release_date="2026-06-01",
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            metascore_reviews=120,
            user_score=8.5,
            user_reviews=5000,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        # Capture Loguru output
        buf = io.StringIO()
        logger_id = logger.add(buf, format="{message}", colorize=False)
        try:
            matched = _match_pending_games(db, qbt=mock_qbt, magnet_fetcher=magnet_fetcher)
        finally:
            logger.remove(logger_id)

        output = buf.getvalue()
        assert len(matched) == 1
        assert matched[0]["result"] == "Passed"
        # The log must include metascore, user score, review counts, and release date
        assert "96.0" in output, f"Metascore should appear in log: {output}"
        assert "120 reviews" in output, f"Metascore review count should appear: {output}"
        assert "8.5" in output, f"User score should appear in log: {output}"
        assert "5000 reviews" in output, f"User review count should appear: {output}"
        assert "2026-06-01" in output, f"Release date should appear in log: {output}"
        db.close()

    def test_deliver_match_escapes_title_with_angle_brackets(self, tmp_path: Path) -> None:
        """Game titles containing < or > must be escaped in the delivery log."""
        import datetime
        import io
        from unittest.mock import MagicMock

        from loguru import logger

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="game-with-angle",
            game_title="Game <Director's>",
            platform="pc",
            metascore=80.0,
            metascore_reviews=10,
            user_score=7.5,
            user_reviews=100,
            release_date="2026-06-01",
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="game-with-angle",
            metascore=80.0,
            metascore_reviews=10,
            user_score=7.5,
            user_reviews=100,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Game <Director's>", "url": "https://example.com/game"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        buf = io.StringIO()
        logger_id = logger.add(buf, format="{message}", colorize=False)
        try:
            matched = _match_pending_games(db, qbt=mock_qbt, magnet_fetcher=magnet_fetcher)
        finally:
            logger.remove(logger_id)

        output = buf.getvalue()
        assert len(matched) == 1
        assert matched[0]["result"] == "Passed", "The game must be delivered, not skipped as a duplicate"
        mock_qbt.add_torrent.assert_called_once()
        # The title should appear escaped (\< and \> — Loguru's escape syntax)
        assert r"Game \<Director's\>" in output, f"Title with < > should be escaped: {output}"
        db.close()

    def test_deliver_match_passes_must_play_and_release_to_notify(self) -> None:
        """_deliver_match should forward must_play and release_date to the notifier."""
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _deliver_match

        db = Database(":memory:")

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "tag-123"
        mock_qbt.add_paused = False
        mock_notifier = MagicMock()
        mock_magnet = MagicMock(return_value="magnet:?xt=urn:btih:abc")

        best = {"url": "https://fitgirl-repacks.site/game/", "title": "Some Game"}

        result = _deliver_match(
            db,
            qbt=mock_qbt,
            magnet_fetcher=mock_magnet,
            notifier=mock_notifier,
            best=best,
            game_slug="some-game",
            game_title="Some Game",
            game_platform="pc",
            game_metascore=80.0,
            game_user_score=8.0,
            game_metascore_reviews=10,
            game_user_reviews=50,
            game_genres=["Action"],
            game_must_play=True,
            game_release_date="2024-10-11",
        )

        assert result["result"] == "Passed"
        # Verify must_play and release_date were passed to the notifier
        call_kwargs = mock_notifier.send_download_notification.call_args[1]
        assert call_kwargs.get("must_play") is True
        assert call_kwargs.get("release_date") == "2024-10-11"
        db.close()

    def test_deliver_match_prepends_source_prefix_to_torrent_title(self, tmp_path: Path) -> None:
        """_deliver_match prepends source display name in square brackets to torrent title."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _deliver_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="test-game",
            game_title="Test Game",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="test-game", metascore=85.0, user_score=8.0)

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_notifier = MagicMock()

        best = {
            "title": "Test Game Repack",
            "url": "https://fitgirl-repacks.site/test-game/",
            "magnet": "magnet:?xt=urn:btih:abc",
        }

        _deliver_match(
            db,
            qbt=mock_qbt,
            magnet_fetcher=MagicMock(),
            notifier=mock_notifier,
            best=best,
            game_slug="test-game",
            game_title="Test Game",
            game_platform="pc",
            game_metascore=85.0,
            game_user_score=8.0,
            source_name="fitgirl",
        )

        _, kwargs = mock_qbt.add_torrent.call_args
        assert kwargs["title"] == "[FitGirl] Test Game Repack", (
            f"Expected '[FitGirl] Test Game Repack', got '{kwargs['title']}'"
        )
        db.close()

    def test_deliver_match_prepends_freegog_source_prefix(self, tmp_path: Path) -> None:
        """_deliver_match prepends [FreeGOG] prefix for freegog source."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _deliver_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="test-game2",
            game_title="Test Game 2",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="test-game2", metascore=85.0, user_score=8.0)

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_notifier = MagicMock()

        best = {
            "title": "Test Game 2",
            "url": "https://freegogpcgames.com/123/test-game-2/",
            "magnet": "magnet:?xt=urn:btih:def",
        }

        _deliver_match(
            db,
            qbt=mock_qbt,
            magnet_fetcher=MagicMock(),
            notifier=mock_notifier,
            best=best,
            game_slug="test-game2",
            game_title="Test Game 2",
            game_platform="pc",
            game_metascore=85.0,
            game_user_score=8.0,
            source_name="freegog",
        )

        _, kwargs = mock_qbt.add_torrent.call_args
        assert kwargs["title"] == "[FreeGOG] Test Game 2", f"Expected '[FreeGOG] Test Game 2', got '{kwargs['title']}'"
        db.close()

    def test_deliver_match_records_download_source_not_metacritic(self, tmp_path: Path) -> None:
        """_deliver_match should persist the download source name in the history row.

        The stored ``source`` feeds the ``{site}`` template placeholder during
        post-processing.  It must be the download source (e.g. "fitgirl"), not
        the critic site "metacritic".
        """
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _deliver_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="cyberpunk-2077",
            game_title="Cyberpunk 2077",
            platform="pc",
            metascore=90.0,
            user_score=8.5,
            expires_at=expires,
        )
        db.update_pending_scores(slug="cyberpunk-2077", metascore=90.0, user_score=8.5)

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_notifier = MagicMock()

        best = {
            "title": "Cyberpunk 2077",
            "url": "https://fitgirl-repacks.site/cyberpunk-2077/",
            "magnet": "magnet:?xt=urn:btih:xyz",
        }

        _deliver_match(
            db,
            qbt=mock_qbt,
            magnet_fetcher=MagicMock(),
            notifier=mock_notifier,
            best=best,
            game_slug="cyberpunk-2077",
            game_title="Cyberpunk 2077",
            game_platform="pc",
            game_metascore=90.0,
            game_user_score=8.5,
            source_name="fitgirl",
        )

        row = db.find_by_tag("gamarr-tag")
        assert row is not None, "Expected a history row to be recorded"
        assert row.source == "fitgirl", (
            f"Expected history source 'fitgirl' (used for {{site}} in post-processing), got '{row.source}'"
        )
        db.close()

    def test_deliver_match_adopts_present_gamarr_torrent(self) -> None:
        """An already-present torrent gamarr owns is adopted, not re-uploaded or abandoned."""
        from unittest.mock import MagicMock

        from loguru import logger as loguru_logger

        from gamarr.pipeline import _deliver_match

        db = MagicMock()
        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = True
        mock_qbt.adopt_present_torrent.return_value = "gamarr-adopted"
        magnet = "magnet:?xt=urn:btih:a53ef14444544a7ae1b133ea3ddfd63dcc11c978"
        best = {
            "title": "Marvel Tokon Fighting Souls",
            "url": "https://fitgirl-repacks.site/marvel-tokon-fighting-souls/",
            "magnet": magnet,
        }

        captured: list[str] = []
        sink_id = loguru_logger.add(
            lambda msg: captured.append(f"{msg.record['level'].name}: {msg}"),
            level="DEBUG",
            format="{message}",
        )
        try:
            result = _deliver_match(
                db,
                qbt=mock_qbt,
                magnet_fetcher=MagicMock(),
                notifier=MagicMock(),
                best=best,
                game_slug="marvel-tokon",
                game_title="Marvel Tokon: Fighting Souls",
                game_platform="pc",
                game_metascore=85.0,
                game_user_score=8.0,
                game_genres=["2D Fighting"],
                source_name="fitgirl",
            )
        finally:
            loguru_logger.remove(sink_id)

        assert result["result"] == "Passed", "An owned torrent must stay visible to post-processing"
        mock_qbt.add_torrent.assert_not_called()
        recorded = db.record_processed.call_args.kwargs
        assert recorded["torrent_tag"] == "gamarr-adopted"
        assert recorded["post_process_state"] is None, "Post-processing must still be allowed to copy it"
        assert recorded["genres"] == "2D Fighting", "Genres feed the {genre} path template"
        db.remove_pending.assert_called_once_with("marvel-tokon")
        infos = [m for m in captured if m.startswith("INFO:") and "already exists" in m]
        assert len(infos) == 1, f"One duplicate must log exactly one INFO line, got {infos}"

    def test_deliver_match_retries_when_adoption_cannot_be_confirmed(self) -> None:
        """An unconfirmable adoption is a failure: the game must stay pending."""
        from unittest.mock import MagicMock

        from gamarr.pipeline import _deliver_match

        db = MagicMock()
        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = None
        mock_qbt.adopt_present_torrent.return_value = None
        best = {
            "title": "Some Game",
            "url": "https://fitgirl-repacks.site/some-game/",
            "magnet": "magnet:?xt=urn:btih:cccccccccccccccccccccccccccccccccccccccc",
        }

        result = _deliver_match(
            db,
            qbt=mock_qbt,
            magnet_fetcher=MagicMock(),
            notifier=MagicMock(),
            best=best,
            game_slug="some-game",
            game_title="Some Game",
            game_platform="pc",
            game_metascore=85.0,
            game_user_score=8.0,
            source_name="fitgirl",
        )

        assert result["result"] == "Error"
        mock_qbt.add_torrent.assert_not_called()
        db.remove_pending.assert_not_called()

    def test_deliver_match_skips_present_foreign_torrent(self) -> None:
        """A torrent outside gamarr's category has nothing for gamarr to copy."""
        from unittest.mock import MagicMock

        from loguru import logger as loguru_logger

        from gamarr.pipeline import _deliver_match

        db = MagicMock()
        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = True
        mock_qbt.adopt_present_torrent.return_value = False
        best = {
            "title": "Some Game",
            "url": "https://fitgirl-repacks.site/some-game/",
            "magnet": "magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        }

        captured: list[str] = []
        sink_id = loguru_logger.add(
            lambda msg: captured.append(f"{msg.record['level'].name}: {msg}"),
            level="DEBUG",
            format="{message}",
        )
        try:
            result = _deliver_match(
                db,
                qbt=mock_qbt,
                magnet_fetcher=MagicMock(),
                notifier=MagicMock(),
                best=best,
                game_slug="some-game",
                game_title="Some Game",
                game_platform="pc",
                game_metascore=85.0,
                game_user_score=8.0,
                source_name="fitgirl",
            )
        finally:
            loguru_logger.remove(sink_id)

        assert result["result"] == "Skipped"
        mock_qbt.add_torrent.assert_not_called()
        recorded = db.record_processed.call_args.kwargs
        assert recorded["post_process_state"] == "skipped"
        assert recorded["torrent_tag"] is None
        db.remove_pending.assert_called_once_with("some-game")
        assert any(m.startswith("INFO:") and "outside gamarr's category" in m for m in captured)

    def test_deliver_match_uploads_when_torrent_absent(self) -> None:
        """Normal delivery is unchanged when the magnet is not already present."""
        from unittest.mock import MagicMock

        from gamarr.pipeline import _deliver_match

        db = MagicMock()
        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "gamarr-newtag"
        best = {
            "title": "Cyberpunk 2077",
            "url": "https://fitgirl-repacks.site/cyberpunk-2077/",
            "magnet": "magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        }

        result = _deliver_match(
            db,
            qbt=mock_qbt,
            magnet_fetcher=MagicMock(),
            notifier=MagicMock(),
            best=best,
            game_slug="cyberpunk-2077",
            game_title="Cyberpunk 2077",
            game_platform="pc",
            game_metascore=90.0,
            game_user_score=8.5,
            source_name="fitgirl",
        )

        assert result["result"] == "Passed"
        mock_qbt.add_torrent.assert_called_once()
        assert db.record_processed.call_args.kwargs["torrent_tag"] == "gamarr-newtag"
        assert db.record_processed.call_args.kwargs["post_process_state"] is None

    def test_deliver_match_records_freegog_source(self, tmp_path: Path) -> None:
        """_deliver_match should persist "freegog" as the history source for FreeGOG matches."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _deliver_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="hollow-knight-silksong",
            game_title="Hollow Knight Silksong",
            platform="pc",
            metascore=88.0,
            user_score=8.2,
            expires_at=expires,
        )
        db.update_pending_scores(slug="hollow-knight-silksong", metascore=88.0, user_score=8.2)

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "gamarr-tag2"
        mock_notifier = MagicMock()

        best = {
            "title": "Hollow Knight Silksong",
            "url": "https://freegogpcgames.com/42/hollow-knight-silksong/",
            "magnet": "magnet:?xt=urn:btih:abc2",
        }

        _deliver_match(
            db,
            qbt=mock_qbt,
            magnet_fetcher=MagicMock(),
            notifier=mock_notifier,
            best=best,
            game_slug="hollow-knight-silksong",
            game_title="Hollow Knight Silksong",
            game_platform="pc",
            game_metascore=88.0,
            game_user_score=8.2,
            source_name="freegog",
        )

        row = db.find_by_tag("gamarr-tag2")
        assert row is not None, "Expected a history row to be recorded"
        assert row.source == "freegog", (
            f"Expected history source 'freegog' (used for {{site}} in post-processing), got '{row.source}'"
        )
        db.close()

    def test_verify_pending_keeps_game_with_failing_real_scores_for_recheck(self, tmp_path: Path) -> None:
        """A game with failing real scores should stay pending for re-verification."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="thick-as-thieves",
            game_title="Thick as Thieves",
            platform="pc",
            metascore=1998.0,  # wrong browse score
            metascore_reviews=1999,
            user_score=2007.0,  # wrong browse score
            user_reviews=None,
            release_date="2026-05-20",
            expires_at=expires,
        )

        # Mock MC lookup returning real scores (62 metascore, 3.3 user — below thresholds)
        mock_mc = MagicMock()
        import types

        mock_result = types.SimpleNamespace(
            metascore=62.0,
            metascore_review_count=25,
            user_score=3.3,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-05-20",
            slug="thick-as-thieves",
        )
        mock_mc.lookup_game.return_value = mock_result

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 10,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        assert db.is_pending("thick-as-thieves") is True
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds)
        # Real scores (62, 3.3) fail thresholds — game should stay for re-check
        assert removed == 0, "Game with failing real scores should NOT be removed (re-check later)"
        assert db.is_pending("thick-as-thieves") is True, "Game should stay in pending queue"
        db.close()

    def test_verify_pending_keeps_game_with_passing_real_scores(self, tmp_path: Path) -> None:
        """A game with wrong browse scores but passing real scores should stay with corrected values."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="forza-horizon-6",
            game_title="Forza Horizon 6",
            platform="pc",
            metascore=1985.0,  # wrong browse score
            metascore_reviews=1986,
            user_score=1994.0,  # wrong browse score
            user_reviews=None,
            release_date="2026-05-19",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_result = types.SimpleNamespace(
            metascore=88.0,  # passes 75 threshold
            metascore_review_count=50,  # passes 10 threshold
            user_score=8.0,  # passes 7.5 threshold
            user_review_count=100,  # passes 10 threshold
            genres=["Racing"],
            must_play=True,
            release_date="2026-05-19",
            slug="forza-horizon-6",
        )
        mock_mc.lookup_game.return_value = mock_result

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 10,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        assert db.is_pending("forza-horizon-6") is True
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds)
        assert removed == 0, "Game with passing real scores should NOT be removed"
        # DB should still have the pending game with corrected scores
        assert db.is_pending("forza-horizon-6") is True
        pending_list = db.get_pending(platform="pc")
        assert len(pending_list) == 1
        assert pending_list[0].metascore == 88.0, "Score should be updated to real value"
        assert pending_list[0].user_score == 8.0, "User score should be updated to real value"
        db.close()

    def test_verify_pending_keeps_game_when_lookup_returns_none(self, tmp_path: Path) -> None:
        """When lookup_game returns None, the pending game should stay for re-check."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="not-found-game",
            game_title="Not Found Game",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None  # detail page not found

        assert db.is_pending("not-found-game") is True
        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            {
                "min_criticscore": 75,
                "min_criticscore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )
        # Game with None lookup should stay pending for re-check
        assert removed == 0, "Game with None lookup should NOT be removed (re-check later)"
        assert db.is_pending("not-found-game") is True, "Game should stay in pending queue"
        # lookup_game was called with direct_only=True (no browse fallback)
        assert mock_mc.lookup_game.call_count == 1
        _call_args, call_kwargs = mock_mc.lookup_game.call_args
        assert call_kwargs.get("direct_only") is True, "lookup_game must use direct_only=True to avoid browse fallback"
        db.close()

    def test_verify_pending_keeps_game_with_tbd_scores_for_recheck(self, tmp_path: Path) -> None:
        """A game with no real Metacritic scores yet (TBD) should stay pending
        for re-verification in a future cycle, not be permanently removed."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="unreviewed-game",
            game_title="Unreviewed Game",
            platform="pc",
            metascore=1288.0,  # wrong browse score
            user_score=1288.0,  # wrong browse score
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=None,
            metascore_review_count=None,
            user_score=0.0,
            user_review_count=0,
            genres=None,
            must_play=False,
            release_date=None,
        )

        assert db.is_pending("unreviewed-game") is True
        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            {
                "min_criticscore": 75,
                "min_criticscore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )
        # Game with TBD scores should NOT be removed — stays pending for re-check
        assert removed == 0, "Game with TBD scores should NOT be removed"
        assert db.is_pending("unreviewed-game") is True, "Game should stay in pending queue"
        # score_checks_passed should remain None (not True)
        pending = db.get_pending()
        unreviewed = next((g for g in pending if g.slug == "unreviewed-game"), None)
        assert unreviewed is not None
        assert unreviewed.score_checks_passed is None or unreviewed.score_checks_passed is False
        db.close()

    def test_verify_pending_removes_game_when_metascore_absent(self, tmp_path: Path) -> None:
        """A game with metascore=None and min_criticscore > 0 should be rejected."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="mixed-scores-game",
            game_title="Mixed Scores Game",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=None,  # no critic score yet
            metascore_review_count=None,
            user_score=8.0,  # user score passes (>= 7.5)
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )

        assert db.is_pending("mixed-scores-game") is True
        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            {
                "min_criticscore": 75,
                "min_criticscore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )
        # Game stays pending for re-verification (not permanently removed)
        assert removed == 0
        assert db.is_pending("mixed-scores-game") is True
        db.close()

    def test_verify_pending_respects_max_verify_limit(self, tmp_path: Path) -> None:
        """_verify_pending_scores must cap lookups at max_verify to prevent 2773 HTTP requests."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        # Create 20 pending games with zero-padded slugs for predictable sort order
        for i in range(20):
            db.record_pending(
                slug=f"game-{i:02d}",
                game_title=f"Game {i:02d}",
                platform="pc",
                metascore=1288.0,
                user_score=1288.0,
                expires_at=expires,
            )

        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=20,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )

        # Call with max_verify=5 — only 5 should be looked up
        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10},
            max_verify=5,
        )
        assert mock_mc.lookup_game.call_count == 5, f"Expected 5 lookups, got {mock_mc.lookup_game.call_count}"
        # Removed should be 0 (all passing scores)
        assert removed == 0, "All games pass thresholds, none removed"
        # All 20 games still pending: first 5 verified (scores updated), last 15 untouched
        remaining = db.get_pending()
        assert len(remaining) == 20, f"All 20 must remain: {len(remaining)}"
        # First 5 (game-00 to game-04) should have updated metascore (85.0),
        # last 15 (game-05 to game-19) should still have browse score (1288.0)
        for g in remaining:
            idx = int(g.slug.split("-")[1])
            if idx < 5:
                assert g.metascore == 85.0, f"{g.slug} should be verified, got {g.metascore}"
            else:
                assert g.metascore == 1288.0, f"{g.slug} should be unverified (browse score), got {g.metascore}"
        db.close()

    def test_verify_pending_uses_active_sort_order(self, tmp_path: Path) -> None:
        """Verification must consume the pending queue in the active sort order."""
        import datetime
        import types
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="old-game",
            game_title="Old Game",
            platform="pc",
            release_date="2018-01-01",
            expires_at=expires,
        )
        db.record_pending(
            slug="new-game",
            game_title="New Game",
            platform="pc",
            release_date="2026-01-01",
            expires_at=expires,
        )
        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=20,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-01-01",
        )

        _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10},
            max_verify=1,
            sort_order="new",
        )

        mock_mc.lookup_game.assert_called_once_with(
            "New Game",
            platform="pc",
            slug="new-game",
            cache_details_days=7,
            direct_only=True,
        )
        db.close()

    def test_match_pending_skips_unverified_game_with_browse_scores(self, tmp_path: Path) -> None:
        """A matched pending game with unverified browse scores must NOT be delivered.

        This test reproduces the bug where games with browse scores (1478.0)
        are delivered to qBittorrent because _match_pending_games doesn't
        verify scores before delivery.
        """
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="unverified-game",
            game_title="Unverified Game",
            platform="pc",
            metascore=1478.0,  # WRONG browse score (not 0-100)
            user_score=1478.0,
            expires_at=expires,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Unverified Game", "url": "https://example.com/game"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.adopt_present_torrent.return_value = None
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_mc = MagicMock()
        # Mock lookup returns None — no detail page scores available
        mock_mc.lookup_game.return_value = None
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            mc=mock_mc,
            thresholds=thresholds,
        )

        # No matches should be returned — game has unverified browse scores
        assert len(matched) == 0, "Unverified game should NOT be delivered"
        # qBittorrent should NOT be called
        mock_qbt.add_torrent.assert_not_called()
        # MC should NOT be called (no JIT verification for unverified games)
        mock_mc.lookup_game.assert_not_called()
        # Magnet should NOT be fetched
        magnet_fetcher.assert_not_called()
        # Game should remain in queue until next score-check cycle
        assert db.is_pending("unverified-game"), "Unverified game should stay pending until score-checked"
        db.close()

    def test_match_pending_delivers_verified_game(self, tmp_path: Path) -> None:
        """A matched pending game with verified real scores should be delivered."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="verified-game",
            game_title="Verified Game",
            platform="pc",
            metascore=85.0,  # REAL score (0-100 range)
            metascore_reviews=20,
            user_score=8.0,
            user_reviews=100,
            release_date="2026-06-01",
            expires_at=expires,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Verified Game", "url": "https://example.com/game"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        # Mark the game as score-checked so it passes the gate in _match_pending_games
        db.update_pending_scores(
            slug="verified-game",
            metascore=85.0,
            metascore_reviews=20,
            user_score=8.0,
            user_reviews=100,
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=20,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            mc=mock_mc,
            thresholds=thresholds,
        )

        # Game should be delivered
        assert len(matched) == 1
        assert matched[0]["result"] == "Passed"
        mock_qbt.add_torrent.assert_called_once()
        db.close()

    def test_match_pending_skips_fitgirl_excluded_keyword(self, tmp_path: Path) -> None:
        """A matched game should be skipped when its FitGirl title contains an excluded keyword."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="007-first-light",
            game_title="007 First Light",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="007-first-light", metascore=85.0, user_score=8.0)
        # FitGirl title contains "HV"
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "007 First Light [FitGirl HV Repack]", "url": "https://example.com/007"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.adopt_present_torrent.return_value = None
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            reject_keywords=["HV"],
        )
        # Game should NOT be delivered — FitGirl title was excluded by keyword
        assert len(matched) == 0
        mock_qbt.add_torrent.assert_not_called()
        # Game should still be pending (not removed, not expired)
        assert db.is_pending("007-first-light"), "Game should remain pending when match is skipped"
        db.close()

    def test_browse_qualifying_games_inserts_pending(self, tmp_path: Path) -> None:
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "Elden Ring",
                "slug": "elden-ring",
                "score": 96,
                "critic_review_count": 120,
                "user_rating": 8.5,
                "user_review_count": 5000,
            },
        ]
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        _process_browse_games(browse_games, "pc", db, thresholds, max_queue_days=30, mc=_passing_detail_client())
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        assert pending[0].slug == "elden-ring"
        db.close()

    def test_reject_keywords_fails_with_clean_sitemap_title(self, tmp_path: Path) -> None:
        """reject_keywords must also check the HTML page title, not just the sitemap title.

        The sitemap title is URL-derived (clean — never contains [FitGirl HV Repack]).
        The HTML <title> tag on the FitGirl repack page DOES contain the full
        title including repack metadata. The keyword check must inspect the
        fetched HTML title, not just the sitemap entry.
        """
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import _process_single_pending_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="crimson-desert",
            game_title="Crimson Desert",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="crimson-desert", metascore=85.0, user_score=8.0)
        # Sitemap title is URL-derived and CLEAN — "HV" is NOT in it
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Crimson Desert", "url": "https://fitgirl-repacks.site/crimson-desert/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.adopt_present_torrent.return_value = None
        mock_qbt.add_torrent.return_value = "gamarr-tag"

        # The HTML page has a full title with HV, but the sitemap entry doesn't
        html_with_hv = (
            "<html><head><title>Crimson Desert [FitGirl HV Repack]</title></head>"
            '<body><a href="magnet:?xt=urn:btih:abc">magnet</a></body></html>'
        )

        with patch("gamarr.pipeline.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html_with_hv
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            result = _process_single_pending_match(
                db,
                mc=None,
                thresholds=None,
                qbt=mock_qbt,
                magnet_fetcher=None,
                notifier=None,
                library=None,
                can_deliver=True,
                game_title="Crimson Desert",
                game_slug="crimson-desert",
                game_platform="pc",
                game_metascore=85.0,
                game_metascore_reviews=100,
                game_user_score=8.0,
                game_user_reviews=50,
                game_release_date=None,
                reject_keywords=["HV"],
            )
            # SHOULD be rejected because HTML title contains "HV"
            assert result is None, "Game should be rejected — FitGirl page title contains HV"
            mock_get.assert_called_once()
            # The game should remain pending (not removed)
            assert db.is_pending("crimson-desert")
            db.close()

    def test_reject_keywords_clean_page_title_passes(self, tmp_path: Path) -> None:
        """When HTML page title does NOT contain keyword, the game should proceed."""
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import _process_single_pending_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="crimson-desert",
            game_title="Crimson Desert",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="crimson-desert", metascore=85.0, user_score=8.0)
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Crimson Desert", "url": "https://fitgirl-repacks.site/crimson-desert/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.adopt_present_torrent.return_value = None
        mock_qbt.add_torrent.return_value = "gamarr-tag"

        # Page title is clean — no HV
        html_clean = (
            "<html><head><title>Crimson Desert [FitGirl Repack]</title></head>"
            '<body><a href="magnet:?xt=urn:btih:abc">magnet</a></body></html>'
        )

        with patch("gamarr.pipeline.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html_clean
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            result = _process_single_pending_match(
                db,
                mc=None,
                thresholds=None,
                qbt=mock_qbt,
                magnet_fetcher=MagicMock(return_value="magnet:?xt=urn:btih:abc"),
                notifier=None,
                library=None,
                can_deliver=True,
                game_title="Crimson Desert",
                game_slug="crimson-desert",
                game_platform="pc",
                game_metascore=85.0,
                game_metascore_reviews=100,
                game_user_score=8.0,
                game_user_reviews=50,
                game_release_date=None,
                reject_keywords=["HV"],
            )
            # Should proceed — page title doesn't contain HV
            assert result is not None, "Game should proceed — page title does not contain HV"
            db.close()

    def test_reject_keywords_keeps_pending_when_fetch_fails(self, tmp_path: Path) -> None:
        """When the FitGirl page cannot be fetched, keep the game pending
        even when the sitemap title contains a reject keyword.
        The page-fetch failure itself is enough to keep the game pending."""
        import datetime
        from unittest.mock import MagicMock, patch

        import requests

        from gamarr.database import Database
        from gamarr.pipeline import _process_single_pending_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="hv-game",
            game_title="HV Game",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="hv-game", metascore=85.0, user_score=8.0)
        # Sitemap title DOES contain HV
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "HV Game [FitGirl HV Repack]", "url": "https://example.com/hv-game"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.adopt_present_torrent.return_value = None
        mock_qbt.add_torrent.return_value = "gamarr-tag"

        with patch("gamarr.pipeline.requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("Connection error")

            result = _process_single_pending_match(
                db,
                mc=None,
                thresholds=None,
                qbt=mock_qbt,
                magnet_fetcher=None,
                notifier=None,
                library=None,
                can_deliver=True,
                game_title="HV Game",
                game_slug="hv-game",
                game_platform="pc",
                game_metascore=85.0,
                game_metascore_reviews=100,
                game_user_score=8.0,
                game_user_reviews=50,
                game_release_date=None,
                reject_keywords=["HV"],
            )
            # Should stay pending — page could not be fetched, cannot verify keywords
            assert result is None, "Should reject — page could not be fetched"
            assert db.is_pending("hv-game"), "Should remain pending"
            db.close()

    def test_reject_keywords_article_body_contains_keyword(self, tmp_path: Path) -> None:
        """When <title> is clean but <article> body contains reject keyword, reject the game."""
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import _process_single_pending_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="game-with-body-keyword",
            game_title="Game With Body Keyword",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="game-with-body-keyword", metascore=85.0, user_score=8.0)
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Game With Body Keyword", "url": "https://fitgirl-repacks.site/game-with-body-keyword/"}],
        )

        # Title is clean, but the Repack Features block contains "Hypervisor"
        html = (
            "<html><head><title>Game With Body Keyword [FitGirl Repack]</title></head>"
            "<body>"
            "<article>"
            "Repack Features Hypervisor Bypass, Lossless Repack Game With Body Keyword"
            "</article>"
            "</body></html>"
        )

        with patch("gamarr.pipeline.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            result = _process_single_pending_match(
                db,
                mc=None,
                thresholds=None,
                qbt=None,
                magnet_fetcher=None,
                notifier=None,
                library=None,
                can_deliver=False,
                game_title="Game With Body Keyword",
                game_slug="game-with-body-keyword",
                game_platform="pc",
                game_metascore=85.0,
                game_metascore_reviews=100,
                game_user_score=8.0,
                game_user_reviews=50,
                game_release_date=None,
                reject_keywords=["Hypervisor"],
            )
            # SHOULD be rejected because the Repack Features block contains "Hypervisor"
            assert result is None, "Game should be rejected \u2014 Repack Features contains Hypervisor"
            assert db.is_pending("game-with-body-keyword"), "Should remain pending"
            db.close()

    def test_reject_keywords_ignores_download_link_anchor_text(self, tmp_path: Path) -> None:
        """When reject keyword only appears inside a download link's anchor text (e.g. "Hypervisor" in
        a filename like "Persona.5.Royal.v1.04.Hypervisor.Bypass-DenuvOwO.rar"), do NOT reject.

        Download link filenames contain structural metadata about the crack method used by
        the Denuvo community — they do not describe the game's requirements.
        """
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import _process_single_pending_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="game-with-link-only-keyword",
            game_title="Game With Link Only Keyword",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="game-with-link-only-keyword", metascore=85.0, user_score=8.0)
        db.rebuild_source_titles(
            "fitgirl",
            [
                {
                    "title": "Game With Link Only Keyword",
                    "url": "https://fitgirl-repacks.site/game-with-link-only-keyword/",
                }
            ],
        )

        # Article body text is clean (no keywords), but the download link anchor text
        # contains "Hypervisor" in the filename — the exact false positive scenario.
        html = (
            "<html><head><title>Game With Link Only Keyword [FitGirl Repack]</title></head>"
            "<body>"
            "<article>"
            "<p>Lossless repack of Game With Link Only Keyword</p>"
            '<p><a href="https://example.com/dl">Persona.5.Royal.v1.04.Hypervisor.Bypass-DenuvOwO.rar</a></p>'
            "</article>"
            "</body></html>"
        )

        with patch("gamarr.pipeline.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            result = _process_single_pending_match(
                db,
                mc=None,
                thresholds=None,
                qbt=None,
                magnet_fetcher=None,
                notifier=None,
                library=None,
                can_deliver=False,
                game_title="Game With Link Only Keyword",
                game_slug="game-with-link-only-keyword",
                game_platform="pc",
                game_metascore=85.0,
                game_metascore_reviews=100,
                game_user_score=8.0,
                game_user_reviews=50,
                game_release_date=None,
                reject_keywords=["Hypervisor"],
            )
            # The game should NOT be rejected — keyword only appears in download link text
            assert result is not None, "Game should NOT be rejected — keyword only in download link"
            db.close()

    def test_reject_keywords_ignores_comments_section(self, tmp_path: Path) -> None:
        """When keyword only appears in comments (outside <article>), do NOT reject."""
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import _process_single_pending_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="clean-game",
            game_title="Clean Game",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="clean-game", metascore=85.0, user_score=8.0)
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Clean Game", "url": "https://fitgirl-repacks.site/clean-game/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.adopt_present_torrent.return_value = None
        mock_qbt.add_torrent.return_value = "gamarr-tag"

        # Title is clean, article is clean, but COMMENTS after </article> contain "hypervisor"
        html = (
            "<html><head><title>Clean Game [FitGirl Repack]</title></head>"
            "<body>"
            "<article>Lossless Repack Clean Game</article>"
            '<div id="comments" class="comments-area">'
            '<div class="comment">User said: this game needs hypervisor bypass</div>'
            "</div>"
            "</body></html>"
        )

        with patch("gamarr.pipeline.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            result = _process_single_pending_match(
                db,
                mc=None,
                thresholds=None,
                qbt=mock_qbt,
                magnet_fetcher=MagicMock(return_value="magnet:?xt=urn:btih:abc"),
                notifier=None,
                library=None,
                can_deliver=True,
                game_title="Clean Game",
                game_slug="clean-game",
                game_platform="pc",
                game_metascore=85.0,
                game_metascore_reviews=100,
                game_user_score=8.0,
                game_user_reviews=50,
                game_release_date=None,
                reject_keywords=["hypervisor"],
            )
            # Should NOT be rejected \u2014 keyword only in comments, outside <article>
            assert result is not None, "Game should proceed \u2014 keyword only in comments section"
            db.close()

    def test_reject_keywords_ignores_boilerplate_above_features(self, tmp_path: Path) -> None:
        """Boilerplate above the Repack Features block must not reject a non-HV repack."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _check_reject_keywords

        db = Database(str(tmp_path / "keywords.db"))
        db.record_pending(slug="forza-horizon-6", game_title="Forza Horizon 6", platform="pc")
        article = (
            "Download Mirrors Only updated files for a new non-HV repack. "
            "convert your INSTALLED HV version of the game "
            "Repack Features Nothing ripped, 100% lossless, custom launcher added "
            "Screenshots gallery"
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=("Forza Horizon 6", article),
        ):
            skipped = _check_reject_keywords(
                db,
                {"url": "https://fitgirl-repacks.site/forza-horizon-6/"},
                "Forza Horizon 6",
                "forza-horizon-6",
                ["hv", "hypervisor", "emulator"],
            )

        assert skipped is False, "boilerplate mentions of HV must not reject a non-HV repack"
        db.close()

    def test_reject_keywords_rejects_a_real_hv_repack(self, tmp_path: Path) -> None:
        """A repack that states a hypervisor bypass in its features is still rejected."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _check_reject_keywords

        db = Database(str(tmp_path / "keywords.db"))
        db.record_pending(slug="black-myth-wukong", game_title="Black Myth: Wukong", platform="pc")
        article = (
            "Download Mirrors direct links "
            "Repack Features Based on P2P Steam BuildID 21393610 release: 139.7 GB "
            "Hypervisor Bypass (INTEL & AMD) by DenuvOwO + Goldberg emu added "
            "Screenshots gallery"
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=("Black Myth: Wukong - Digital Deluxe Edition", article),
        ):
            skipped = _check_reject_keywords(
                db,
                {"url": "https://fitgirl-repacks.site/black-myth-wukong/"},
                "Black Myth: Wukong",
                "black-myth-wukong",
                ["hv", "hypervisor", "emulator"],
            )

        assert skipped is True, "a genuine HV repack must still be rejected"
        db.close()

    def test_reject_keywords_fallback_sitemap_rejects_when_fetch_fails(self, tmp_path: Path) -> None:
        """When the FitGirl page cannot be fetched, the game should stay pending
        even if the sitemap title is clean.  The sitemap title never contains
        repack metadata like "hypervisor" or "hv", so the fallback check is
        unreliable — keeping the game pending lets it retry in the next cycle."""
        import datetime
        from unittest.mock import patch

        import requests

        from gamarr.database import Database
        from gamarr.pipeline import _process_single_pending_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="reject-fetch-fail",
            game_title="Clean Game Fallback",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="reject-fetch-fail", metascore=85.0, user_score=8.0)
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Clean Game Fallback", "url": "https://example.com/clean-game"}],
        )

        with patch("gamarr.pipeline.requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("Connection error")
            result = _process_single_pending_match(
                db,
                mc=None,
                thresholds=None,
                qbt=None,
                magnet_fetcher=None,
                notifier=None,
                library=None,
                can_deliver=False,
                game_title="Clean Game Fallback",
                game_slug="reject-fetch-fail",
                game_platform="pc",
                game_metascore=85.0,
                game_metascore_reviews=100,
                game_user_score=8.0,
                game_user_reviews=50,
                game_release_date=None,
                reject_keywords=["hv", "hypervisor"],
            )
            assert result is None, "Game should stay pending — page could not be fetched"
            assert db.is_pending("reject-fetch-fail"), "Game must remain pending"
            db.close()

    def test_match_pending_against_source(self, tmp_path: Path) -> None:
        import datetime

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))

        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=96.0,
            user_score=8.5,
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )

        db.rebuild_source_titles(
            "fitgirl",
            [
                {"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"},
            ],
        )

        matched = _match_pending_games(db)
        assert len(matched) == 1
        assert matched[0]["slug"] == "elden-ring"
        assert db.is_pending("elden-ring") is False
        db.close()

    def test_match_pending_delivers_magnet(self, tmp_path: Path) -> None:
        """When qbt and magnet_fetcher are provided, matched games should be delivered."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=96.0,
            user_score=8.5,
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        matched = _match_pending_games(db, qbt=mock_qbt, magnet_fetcher=magnet_fetcher)

        # Game should be matched and delivered
        assert len(matched) == 1
        assert matched[0]["result"] == "Passed"
        # Magnet should have been fetched and torrent should have been added
        magnet_fetcher.assert_called_once_with("https://fitgirl-repacks.site/elden-ring/")
        # Title should come from the FitGirl page title cache
        mock_qbt.add_torrent.assert_called_once()
        args, kwargs = mock_qbt.add_torrent.call_args
        assert kwargs["magnet_url"] == "magnet:?xt=urn:btih:test"
        assert kwargs["title"] == "[FitGirl] Elden Ring", "Falls back to sitemap/game title when no page cached"
        assert db.is_pending("elden-ring") is False
        db.close()

    def test_match_pending_delivers_magnet_fetch_fails(self, tmp_path: Path) -> None:
        """When magnet fetcher returns None, the match should record as Error."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=96.0,
            user_score=8.5,
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.adopt_present_torrent.return_value = None
        magnet_fetcher = MagicMock(return_value=None)  # No magnet found

        matched = _match_pending_games(db, qbt=mock_qbt, magnet_fetcher=magnet_fetcher)

        assert len(matched) == 1
        assert matched[0]["result"] == "Error"
        magnet_fetcher.assert_called_once()
        mock_qbt.add_torrent.assert_not_called()
        # Delivery failures keep games pending for retry on next cycle.
        assert db.is_pending("elden-ring") is True
        db.close()

    def test_match_pending_delivers_magnet_qbt_fails(self, tmp_path: Path) -> None:
        """When qBittorrent add_torrent fails, the match should record as Error."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=96.0,
            user_score=8.5,
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = False  # qBittorrent failure
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        matched = _match_pending_games(db, qbt=mock_qbt, magnet_fetcher=magnet_fetcher)

        assert len(matched) == 1
        assert matched[0]["result"] == "Error"
        magnet_fetcher.assert_called_once()
        mock_qbt.add_torrent.assert_called_once()
        # Delivery failures keep games pending for retry on next cycle.
        assert db.is_pending("elden-ring") is True
        db.close()

    def test_expired_game_is_moved_to_history(self, tmp_path: Path) -> None:
        """Expiry runs before the match-phase gate, so a lapsed row is always swept."""
        import datetime

        from gamarr.database import Database
        from gamarr.pipeline import _process_expired_games

        db = Database(str(tmp_path / "test.db"))
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=1)).isoformat()
        db.record_pending(
            slug="old-game",
            game_title="Old Game",
            platform="pc",
            expires_at=past,
        )
        matched = _process_expired_games(db)
        # Expired game should be returned with result "Expired"
        assert len(matched) == 1
        assert matched[0]["result"] == "Expired"
        assert db.is_pending("old-game") is False
        db.close()

    def test_match_pending_sends_download_notification(self, tmp_path: Path) -> None:
        """On successful delivery, notifier.send_download_notification should be called."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=96.0,
            user_score=8.5,
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_paused = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_notifier = MagicMock()
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            notifier=mock_notifier,
        )
        assert len(matched) == 1
        mock_notifier.send_download_notification.assert_called_once_with(
            title="Elden Ring",
            platform="pc",
            metascore=96.0,
            metascore_reviews=None,
            user_score=8.5,
            user_reviews=None,
            slug="elden-ring",
            genres=None,
            add_paused=False,
            must_play=None,
            release_date=None,
            source_name="fitgirl",
            source_url="https://fitgirl-repacks.site/elden-ring/",
        )
        db.close()

    def test_match_pending_sends_failure_notification_on_qbt_failure(self, tmp_path: Path) -> None:
        """When qBittorrent rejects, the game stays pending for retry — no notification spam."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=96.0,
            user_score=8.5,
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = False
        mock_notifier = MagicMock()
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            notifier=mock_notifier,
        )
        assert len(matched) == 1
        assert matched[0]["result"] == "Error", "A rejected upload must be reported as an error, not skipped"
        mock_qbt.add_torrent.assert_called_once()
        # Delivery failures are transient — game stays pending for retry.
        # Failure notifications are NOT sent to avoid spam on persistent failures.
        mock_notifier.send_failure_notification.assert_not_called()
        assert db.is_pending("elden-ring"), "Failed delivery must stay pending for retry"
        db.close()

    def test_match_pending_sends_failure_notification_on_magnet_failure(self, tmp_path: Path) -> None:
        """When the magnet fetch fails, the game stays pending for retry — no notification spam."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=96.0,
            user_score=8.5,
            expires_at=expires,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.adopt_present_torrent.return_value = None
        mock_notifier = MagicMock()
        # Magnet fetcher returns None (failure)
        magnet_fetcher = MagicMock(return_value=None)

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            notifier=mock_notifier,
        )
        assert len(matched) == 1
        # Delivery failures are transient — game stays pending for retry.
        mock_notifier.send_failure_notification.assert_not_called()
        # qbt.add_torrent must NOT be called when magnet fetch fails
        mock_qbt.add_torrent.assert_not_called()
        db.close()

    def test_match_pending_notification_dispatched_after_db_update(self, tmp_path: Path) -> None:
        """Notifications must be dispatched AFTER DB recording.

        If notifier raises, the DB must already reflect the result so
        the next cycle does not re-match and re-download.
        """
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=96.0,
            user_score=8.5,
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_paused = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_notifier = MagicMock()
        mock_notifier.send_download_notification.side_effect = RuntimeError("apprise down")
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        # Notification raises — the result must still be recorded and the
        # pending row still removed (so no duplicate next cycle).
        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            notifier=mock_notifier,
        )
        assert len(matched) == 1
        assert matched[0]["result"] == "Passed"
        # Notifier was actually called (and raised) — proves the
        # notification path runs AFTER the DB update, not skipped.
        mock_notifier.send_download_notification.assert_called_once()
        # Pending row should be removed despite notification failure
        assert db.is_pending("elden-ring") is False
        db.close()

    def test_match_pending_skips_when_in_library(self, tmp_path: Path) -> None:
        """A matched game that is already in the library should be skipped."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=96.0,
            user_score=8.5,
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")
        mock_library = MagicMock()
        mock_library.check_game.return_value = MagicMock(
            matched_path="/games/Elden Ring",
            matched_name="Elden Ring",
        )

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            library=mock_library,
        )
        assert len(matched) == 1
        assert matched[0]["result"] == "Already owned"
        # Torrent should NOT be added because game is in library
        mock_qbt.add_torrent.assert_not_called()
        # Magnet should not even be fetched
        magnet_fetcher.assert_not_called()
        db.close()

    def test_match_pending_touches_when_no_source_match(self, tmp_path: Path) -> None:
        """When no source matches a pending game, the pending row is touched (last_checked_at updated)."""
        import datetime

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="unmatched-game",
            game_title="Unmatched Game",
            platform="pc",
            expires_at=expires,
        )
        # No source titles indexed, so no match will be found

        matched = _match_pending_games(db)
        # No matches, no results
        assert matched == []
        # Pending should still be in DB (not removed)
        assert db.is_pending("unmatched-game") is True
        db.close()

    def test_default_magnet_fetcher_handles_request_failure(self) -> None:
        """_default_magnet_fetcher returns None when the HTTP request fails."""
        from unittest.mock import patch

        import requests

        from gamarr.pipeline import _default_magnet_fetcher

        with patch("gamarr.pipeline.requests.get", side_effect=requests.exceptions.ConnectionError("nope")):
            result = _default_magnet_fetcher("http://example.com/page")
        assert result is None

    def test_default_magnet_fetcher_rejects_non_https(self) -> None:
        """_default_magnet_fetcher returns None for non-HTTPS URLs."""
        from unittest.mock import patch

        from gamarr.pipeline import _default_magnet_fetcher

        with patch("gamarr.pipeline.logger") as mock_logger:
            result = _default_magnet_fetcher("http://example.com/page")
        assert result is None
        mock_logger.warning.assert_called_once()
        assert "Skipping" in mock_logger.warning.call_args[0][0]

    def test_default_magnet_fetcher_returns_magnet_on_success(self) -> None:
        """_default_magnet_fetcher returns magnet when HTTP and extraction succeed."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import _default_magnet_fetcher, _fitgirl_page_title_cache

        _fitgirl_page_title_cache.clear()

        mock_resp = MagicMock()
        mock_resp.text = "<html><title>Crimson Desert [FitGirl HV Repack]</title>"

        with (
            patch("gamarr.pipeline.requests.get", return_value=mock_resp),
            patch(
                "gamarr.pipeline._extract_magnet_from_html",
                return_value="magnet:?xt=urn:btih:abc",
            ),
        ):
            result = _default_magnet_fetcher("https://fitgirl-repacks.site/game")

        assert result == "magnet:?xt=urn:btih:abc"
        assert (
            _fitgirl_page_title_cache.get("https://fitgirl-repacks.site/game") == "Crimson Desert [FitGirl HV Repack]"
        )
        _fitgirl_page_title_cache.clear()

    def test_default_magnet_fetcher_caches_none_when_no_title(self) -> None:
        """_default_magnet_fetcher caches None when page has no <title> tag."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import _default_magnet_fetcher, _fitgirl_page_title_cache

        _fitgirl_page_title_cache.clear()

        mock_resp = MagicMock()
        mock_resp.text = "<html><body>No title</body>"

        with (
            patch("gamarr.pipeline.requests.get", return_value=mock_resp),
            patch(
                "gamarr.pipeline._extract_magnet_from_html",
                return_value="magnet:?xt=urn:btih:abc",
            ),
        ):
            result = _default_magnet_fetcher("https://fitgirl-repacks.site/no-title")

        assert result == "magnet:?xt=urn:btih:abc"
        assert _fitgirl_page_title_cache.get("https://fitgirl-repacks.site/no-title") is None
        _fitgirl_page_title_cache.clear()

    def test_default_magnet_fetcher_returns_none_when_no_magnet(self) -> None:
        """_default_magnet_fetcher returns None when no magnet is found in HTML."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import _default_magnet_fetcher

        mock_resp = MagicMock()
        mock_resp.text = "<html><title>Game</title><p>no magnet here</p></html>"

        with patch("gamarr.pipeline.requests.get", return_value=mock_resp):
            result = _default_magnet_fetcher("https://fitgirl-repacks.site/no-magnet")

        assert result is None

    def test_default_magnet_fetcher_passes_verify_false(self) -> None:
        """_default_magnet_fetcher must pass verify=False to bypass self-signed cert."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import _default_magnet_fetcher

        mock_resp = MagicMock()
        mock_resp.text = "<html><title>Game</title>"

        with (
            patch("gamarr.pipeline.requests.get", return_value=mock_resp) as mock_get,
            patch("gamarr.pipeline._extract_magnet_from_html", return_value=None),
        ):
            _default_magnet_fetcher("https://fitgirl-repacks.site/game")

        assert mock_get.call_args[1].get("verify") is False, f"Expected verify=False, got {mock_get.call_args[1]}"

    def test_fetch_fitgirl_page_title_rejects_non_https(self) -> None:
        """_fetch_fitgirl_page_title returns None for non-HTTPS URLs."""
        from gamarr.pipeline import _fetch_fitgirl_page_title

        result = _fetch_fitgirl_page_title("http://example.com/game")
        assert result is None

    def test_fetch_fitgirl_page_title_returns_none_on_request_failure(self) -> None:
        """_fetch_fitgirl_page_title returns None when the HTTP request fails."""
        from unittest.mock import patch

        import requests

        from gamarr.pipeline import _fetch_fitgirl_page_title

        with patch("gamarr.pipeline.requests.get", side_effect=requests.exceptions.ConnectionError("nope")):
            result = _fetch_fitgirl_page_title("https://fitgirl-repacks.site/game")
        assert result is None

    def test_fetch_fitgirl_page_title_returns_title(self) -> None:
        """_fetch_fitgirl_page_title extracts the <title> tag content."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import _fetch_fitgirl_page_title

        mock_resp = MagicMock()
        mock_resp.text = "<html><title>Elden Ring [FitGirl HV Repack]</title><body>...</body></html>"

        with patch("gamarr.pipeline.requests.get", return_value=mock_resp):
            result = _fetch_fitgirl_page_title("https://fitgirl-repacks.site/elden-ring")

        assert result == "Elden Ring [FitGirl HV Repack]"

    def test_fetch_fitgirl_page_title_returns_none_when_no_title_tag(self) -> None:
        """_fetch_fitgirl_page_title returns None when page has no <title> tag."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import _fetch_fitgirl_page_title

        mock_resp = MagicMock()
        mock_resp.text = "<html><body>No title here</body></html>"

        with patch("gamarr.pipeline.requests.get", return_value=mock_resp):
            result = _fetch_fitgirl_page_title("https://fitgirl-repacks.site/no-title")

        assert result is None

    def test_default_magnet_fetcher_unescapes_html_entities(self) -> None:
        """_default_magnet_fetcher must unescape HTML entities in the page title
        so the qBittorrent torrent name shows clean characters like ' and " instead
        of &#039; and &quot;."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import _default_magnet_fetcher, _fitgirl_page_title_cache

        _fitgirl_page_title_cache.clear()

        mock_resp = MagicMock()
        mock_resp.text = (
            "<html><title>Baldur&#039;s Gate 3: Digital Deluxe Edition - v4.1.1.6758295 "
            "(Patch 8, &quot;The Final&quot; Patch) + DLC - FitGirl Repacks</title>"
        )
        with (
            patch("gamarr.pipeline.requests.get", return_value=mock_resp),
            patch("gamarr.pipeline._extract_magnet_from_html", return_value="magnet:?xt=urn:btih:abc"),
        ):
            _default_magnet_fetcher("https://fitgirl-repacks.site/baldurs-gate-3/")

        cached = _fitgirl_page_title_cache.get("https://fitgirl-repacks.site/baldurs-gate-3/")
        assert cached is not None
        # Verify HTML entities are unescaped
        assert "&#039;" not in cached, f"Title still contains HTML entities: {cached}"
        assert "'" in cached, f"Title missing expected apostrophe: {cached}"
        assert "&quot;" not in cached, f"Title still contains HTML quotes: {cached}"
        assert '"' in cached, f"Title missing expected quote: {cached}"
        assert "Baldur's Gate 3" in cached, f"Unexpected title: {cached}"
        _fitgirl_page_title_cache.clear()

    def test_fetch_fitgirl_page_title_unescapes_html_entities(self) -> None:
        """_fetch_fitgirl_page_title must unescape HTML entities in the <title> tag."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import _fetch_fitgirl_page_title

        mock_resp = MagicMock()
        mock_resp.text = (
            "<html><title>Baldur&#039;s Gate 3: Digital Deluxe Edition - v4.1.1.6758295 "
            "(Patch 8, &quot;The Final&quot; Patch) + DLC - FitGirl Repacks</title>"
        )

        with patch("gamarr.pipeline.requests.get", return_value=mock_resp):
            result = _fetch_fitgirl_page_title("https://fitgirl-repacks.site/baldurs-gate-3/")

        assert result is not None
        assert "&#039;" not in result, f"Title still contains HTML entities: {result}"
        assert "'" in result, f"Title missing expected apostrophe: {result}"
        assert "&quot;" not in result, f"Title still contains HTML quotes: {result}"
        assert '"' in result, f"Title missing expected quote: {result}"
        assert "Baldur's Gate 3" in result, f"Unexpected title: {result}"

    def test_fetch_fitgirl_page_title_passes_verify_false(self) -> None:
        """_fetch_fitgirl_page_title must pass verify=False to bypass self-signed cert."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import _fetch_fitgirl_page_title

        mock_resp = MagicMock()
        mock_resp.text = "<html><title>Game</title>"

        with patch("gamarr.pipeline.requests.get", return_value=mock_resp) as mock_get:
            _fetch_fitgirl_page_title("https://fitgirl-repacks.site/game")

        assert mock_get.call_args[1].get("verify") is False, f"Expected verify=False, got {mock_get.call_args[1]}"

    def test_browse_skips_below_threshold(self, tmp_path: Path) -> None:
        """Games below score thresholds should NOT be inserted as pending."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        games = [
            {
                "title": "Low Score Game",
                "slug": "low-score",
                "score": 30,
                "critic_review_count": 2,
                "user_rating": 4.0,
                "user_review_count": 1,
            },
        ]
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        new_count = _process_browse_games(games, "pc", db, thresholds, max_queue_days=30)
        assert new_count == 0
        pending = db.get_pending()
        assert len(pending) == 0
        db.close()

    def test_browse_adds_games_below_threshold(self, tmp_path: Path) -> None:
        """Browse games with permissive thresholds should all be added."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))

        games = [
            {
                "title": "Old Game",
                "slug": "old-game",
                "score": 90,
                "user_rating": 8.0,
            },
            {
                "title": "Recent Game",
                "slug": "recent-game",
                "score": 85,
                "user_rating": 7.5,
            },
        ]
        thresholds = {
            "min_criticscore": 0,
            "min_criticscore_reviews": 0,
            "min_user_score": 0.0,
            "min_user_reviews": 0,
        }
        new_count = _process_browse_games(games, "pc", db, thresholds, max_queue_days=30, mc=_passing_detail_client())
        assert new_count == 2, f"Expected 2 pending games, got {new_count}"
        pending = db.get_pending()
        assert len(pending) == 2
        db.close()

    def test_browse_skips_keyword_excluded_games(self, tmp_path: Path) -> None:
        """Games with titles matching reject_title should not be added."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        games = [
            {
                "title": "Real Game",
                "slug": "real-game",
                "score": 85,
                "user_rating": 8.0,
            },
            {
                "title": "Real Game DLC",
                "slug": "real-game-dlc",
                "score": 85,
                "user_rating": 8.0,
            },
            {
                "title": "Game Soundtrack",
                "slug": "game-soundtrack",
                "score": 85,
                "user_rating": 8.0,
            },
            {
                "title": "Bundle of Games",
                "slug": "bundle-of-games",
                "score": 85,
                "user_rating": 8.0,
            },
        ]
        thresholds = {
            "min_criticscore": 0,
            "min_criticscore_reviews": 0,
            "min_user_score": 0.0,
            "min_user_reviews": 0,
        }
        new_count = _process_browse_games(
            games,
            "pc",
            db,
            thresholds,
            max_queue_days=30,
            reject_title=["DLC", "Soundtrack", "Bundle"],
            mc=_passing_detail_client(),
        )
        assert new_count == 1, "Only the non-excluded game should be added"
        pending = db.get_pending()
        assert pending[0].slug == "real-game"
        db.close()

    def test_reject_keywords_skipped_for_freegog(self, tmp_path: Path) -> None:
        """_check_reject_keywords is not called for FreeGOG sources."""
        import datetime
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _process_single_pending_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="freegog-game",
            game_title="FreeGOG Game",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="freegog-game", metascore=85.0, user_score=8.0)
        db.rebuild_source_titles(
            "freegog",
            [{"title": "FreeGOG Game", "url": "https://freegogpcgames.com/123/freegog-game/"}],
        )

        with patch("gamarr.pipeline._check_reject_keywords") as mock_check:
            _process_single_pending_match(
                db,
                mc=None,
                thresholds=None,
                qbt=None,
                magnet_fetcher=None,
                notifier=None,
                library=None,
                can_deliver=False,
                game_title="FreeGOG Game",
                game_slug="freegog-game",
                game_platform="pc",
                game_metascore=85.0,
                game_metascore_reviews=100,
                game_user_score=8.0,
                game_user_reviews=50,
                game_release_date=None,
                reject_keywords=["hv"],
                source_name="freegog",
            )
            # The reject-keyword check is FitGirl-specific (article body scanning
            # for HV/hypervisor).  For FreeGOG sources it should be skipped.
            mock_check.assert_not_called()


class TestRunAcquisitionMetacritic:
    """Full acquisition cycle with Metacritic browse enabled."""

    def test_acquisition_browse_and_match(self, tmp_path: Path) -> None:
        """End-to-end: browse inserts pending, sitemap matching moves to history."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        sitemap_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://fitgirl-repacks.site/elden-ring/</loc></url>
</urlset>"""

        with (
            patch("gamarr.sources.fitgirl.requests.get") as mock_get,
            patch("gamarr.pipeline.MetacriticClient") as _,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = False
            mock_qbt_cls.return_value = mock_qbt
            # Mock sitemap fetch
            sitemap_resp = MagicMock()
            sitemap_resp.content = sitemap_xml
            sitemap_resp.raise_for_status = MagicMock()
            mock_get.return_value = sitemap_resp

            results = run_acquisition(
                db_path=str(tmp_path / "gamarr.db"),
                qbt_host="localhost",
                qbt_port=8080,
                qbt_username="admin",
                qbt_password="adminadmin",
            )

        assert isinstance(results, list)

    def test_sitemap_not_fetched_when_metacritic_browse_returns_nothing(self) -> None:
        """Metacritic-first: FitGirl sitemap must NOT be fetched if no Metacritic games to match.

        Reproduces the bug where the FitGirl sitemap was fetched as the
        first action of every cycle (logging 'FitGirl sitemap indexed 7759
        game titles'), even when there were zero Metacritic games to
        match against it. The user wants Metacritic browsed first, and
        the sitemap fetched only when there are games to match.
        """
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source

            # Metacritic browse returns NOTHING — no games to match
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
            )

            # Metacritic browse WAS called (multiple years now)
            assert mock_mc.scan_recent_games.called
            # But the FitGirl sitemap was NOT fetched because there is
            # nothing to match against (no pending games produced).
            mock_source.fetch_sitemap.assert_not_called()

    def test_sitemap_fetched_after_metacritic_browse(self) -> None:
        """When Metacritic browse produces a qualifying game, sitemap is fetched AFTER browse."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        call_order: list[str] = []

        def _make_mc_result(
            metascore: float,
            meta_reviews: int,
            user: float,
            user_reviews: int,
        ) -> object:
            import types

            return types.SimpleNamespace(
                metascore=metascore,
                metascore_review_count=meta_reviews,
                user_score=user,
                user_review_count=user_reviews,
                genres=["Action"],
                must_play=False,
                release_date="2026-06-01",
            )

        def record_mc_call(
            *_args: object,
            **_kwargs: object,
        ) -> list[dict[str, object]]:
            call_order.append("scan_recent_games")
            return [
                {
                    "title": "Passing Game",
                    "slug": "passing-game",
                    "score": 90.0,
                    "critic_review_count": 50,
                    "user_rating": 8.5,
                    "user_review_count": 200,
                    "release_date": "2026-06-01",
                },
            ]

        def record_source_call(
            *_args: object,
            **_kwargs: object,
        ) -> None:
            call_order.append("fetch_sitemap")

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_sitemap.side_effect = record_source_call
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            mock_mc.scan_recent_games.side_effect = record_mc_call
            mock_mc.lookup_game.return_value = _make_mc_result(
                metascore=90.0,
                meta_reviews=50,
                user=8.5,
                user_reviews=200,
            )
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.is_connected.return_value = True
            mock_qbt.add_torrent.return_value = False
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
            )

        # Multiple years are scanned now, so scan_recent_games appears
        # multiple times before fetch_sitemap.
        assert call_order[-1] == "fetch_sitemap", f"Expected fetch_sitemap last; got {call_order}"
        assert all(c == "scan_recent_games" for c in call_order[:-1]), (
            f"Expected only scan_recent_games calls before fetch_sitemap; got {call_order}"
        )
        assert len(call_order) > 1, f"Expected at least one scan_recent_games call; got {call_order}"
        # Belt-and-suspenders: sitemap should be called exactly once.
        mock_source.fetch_sitemap.assert_called_once()


class TestPhaseBannerLogging:
    """Phase banners must use logger.opt(colors=True) to avoid raw markup tags."""

    def test_phase_banner_does_not_leak_raw_markup_tags(self, tmp_path: Path) -> None:
        """Run acquisition and verify phase banners don't have raw <color> tags."""
        import io
        from unittest.mock import MagicMock, patch

        from loguru import logger

        from gamarr.pipeline import run_acquisition

        log_stream = io.StringIO()
        handler_id = logger.add(log_stream, format="{message}", level="INFO")

        try:
            with (
                patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
                patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
                patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            ):
                mock_source = MagicMock()
                mock_source_cls.return_value = mock_source

                mock_mc = MagicMock()
                mock_mc.scan_recent_games.return_value = []
                mock_mc_cls.return_value = mock_mc

                mock_qbt = MagicMock()
                mock_qbt.is_torrent_present.return_value = False
                mock_qbt.adopt_present_torrent.return_value = None
                mock_qbt.is_connected.return_value = True
                mock_qbt_cls.return_value = mock_qbt

                run_acquisition(
                    platform="pc",
                    qbt_host="localhost",
                    qbt_port=8080,
                )
        finally:
            logger.remove(handler_id)

        log_output = log_stream.getvalue()

        # Phase 1 banner fires when Metacritic returns empty.
        # Verify no raw colour markup tags leak into output.
        assert "<yellow>" not in log_output, f"Raw yellow markup leaked:\n{log_output}"
        assert "<cyan>" not in log_output, f"Raw cyan markup leaked:\n{log_output}"
        assert "<light-red>" not in log_output, f"Raw light-red markup leaked:\n{log_output}"
        assert "<magenta>" not in log_output, f"Raw magenta markup leaked:\n{log_output}"
        assert "<blue>" not in log_output, f"Raw blue markup leaked:\n{log_output}"
        assert "</>" not in log_output, f"Raw close-tag leaked into log output:\n{log_output}"
        # The phase banner text should appear (cleaned of markup)
        assert "Phase 1: Discovering games on Metacritic" in log_output, (
            f"Phase 1 banner missing from log output:\n{log_output}"
        )


class TestVerifyPendingScoresEdgeCases:
    """_verify_pending_scores edge cases."""

    def test_scores_present_with_no_valid_scores(self) -> None:
        """_scores_present returns False when all scores are None/0."""
        import types

        from gamarr.pipeline import _scores_present

        result = types.SimpleNamespace(
            metascore=0.0,
            metascore_review_count=None,
            user_score=0.0,
            user_review_count=0,
            genres=None,
            must_play=False,
            release_date=None,
        )
        assert _scores_present(result) is False
        assert _scores_present(None) is False

    def test_scores_present_with_valid_user_score(self) -> None:
        """_scores_present returns True when user_score is valid."""
        import types

        from gamarr.pipeline import _scores_present

        result = types.SimpleNamespace(
            metascore=0.0,
            metascore_review_count=None,
            user_score=8.0,
            user_review_count=100,
            genres=None,
            must_play=False,
            release_date=None,
        )
        assert _scores_present(result) is True

    def test_match_pending_jit_removes_game_with_failing_scores(self, tmp_path: Path) -> None:
        """When JIT verification reveals failing scores, the game should be removed."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="failing-jit",
            game_title="Failing JIT Game",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        # Mark as score-checked so it passes the gate
        db.update_pending_scores(slug="failing-jit", metascore=85.0, user_score=8.0)
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Failing JIT Game", "url": "https://example.com/failing-jit"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None  # Detail page not found
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            mc=mock_mc,
            thresholds=thresholds,
        )
        # Game should stay pending (transient Metacritic failure —
        # re-verify on next cycle)
        assert len(matched) == 0
        assert db.is_pending("failing-jit"), "Game should stay pending for re-check"
        # qBittorrent should NOT be called
        mock_qbt.add_torrent.assert_not_called()
        db.close()

    def test_verify_pending_checks_all_games(self, tmp_path: Path) -> None:
        """_verify_pending_scores should check all pending games per cycle.

        With max_games removed, the verify phase checks all pending games
        since the input pool is bounded by max_pages.
        """
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        # Create 3 pending games
        for i in range(3):
            db.record_pending(
                slug=f"game-{i:02d}",
                game_title=f"Game {i:02d}",
                platform="pc",
                metascore=85.0,
                user_score=8.0,
                expires_at=expires,
            )

        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=20,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        # Verify all pending games (no max_games cap anymore)
        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            thresholds,
            max_verify=len(db.get_pending()),
        )
        assert removed == 0  # All passed
        assert mock_mc.lookup_game.call_count == 3  # All 3 checked
        db.close()

    def test_verify_pending_scores_concurrently(self, tmp_path: Path) -> None:
        """Score-checking multiple games should be faster than sequential.

        When multiple pending games need score-checking, the lookup_game
        calls should run concurrently (ThreadPoolExecutor) rather than
        sequentially.  This test mocks a slow HTTP response and verifies
        that N games are checked in less than N × response_time.
        """
        import datetime
        import time
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        # Create 10 pending games
        for i in range(10):
            db.record_pending(
                slug=f"game-{i:02d}",
                game_title=f"Game {i:02d}",
                platform="pc",
                metascore=85.0,
                user_score=8.0,
                expires_at=expires,
            )

        mock_mc = MagicMock()
        # Each lookup takes 0.3 seconds (simulating a slow HTTP response)
        import types

        def slow_lookup(*args: object, **kwargs: object) -> types.SimpleNamespace:
            time.sleep(0.3)
            return types.SimpleNamespace(
                metascore=85.0,
                metascore_review_count=20,
                user_score=8.0,
                user_review_count=100,
                genres=["Action"],
                must_play=False,
                release_date="2026-06-01",
            )

        mock_mc.lookup_game.side_effect = slow_lookup

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        start = time.time()
        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            thresholds,
            max_verify=10,
        )
        elapsed = time.time() - start

        assert removed == 0  # All pass
        # Sequential would take ~3 seconds (10 × 0.3s).
        # With 10 concurrent workers, should take ~0.3-0.6s.
        # Allow generous margin: should complete in under 2s.
        assert elapsed < 2.0, f"Score-checking 10 games took {elapsed:.1f}s — expected <2.0s with concurrent lookups"
        # All 10 lookups should have been called
        assert mock_mc.lookup_game.call_count == 10
        db.close()

    def test_verify_pending_pass_emits_log(self, tmp_path: Path) -> None:
        """A game that passes score-checking should log that it passed.

        Without this log, games that pass all filters but have no FitGirl
        match are completely invisible in the output.
        """
        import datetime
        import io
        from unittest.mock import MagicMock

        from loguru import logger

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="passing-game",
            game_title="Passing Game",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=20,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        buf = io.StringIO()
        logger_id = logger.add(buf, format="{message}", colorize=False, level="DEBUG")
        try:
            removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, max_verify=10)
        finally:
            logger.remove(logger_id)

        assert removed == 0
        output = buf.getvalue()
        assert "Passing Game" in output, "Game that passed score checks should appear in log"
        assert "score check" in output.lower() or "passed" in output.lower(), "Should mention that score check passed"
        db.close()

    def test_reject_genre_matches(self, tmp_path: Path) -> None:
        """Game with a genre in reject_genre should be removed immediately."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=95.0,
            metascore_reviews=100,
            user_score=9.0,
            user_reviews=500,
            release_date="2022-02-25",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=95.0,
            metascore_review_count=100,
            user_score=9.0,
            user_review_count=500,
            genres=["Action", "RPG"],
            must_play=True,
            release_date="2022-02-25",
            slug="elden-ring",
        )

        thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        assert db.is_pending("elden-ring") is True
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["rpg"])
        assert removed == 1, "Game with rejected genre should be removed"
        assert db.is_pending("elden-ring") is False, "Game should no longer be pending"
        db.close()

    def test_reject_genre_no_match(self, tmp_path: Path) -> None:
        """Game without a rejected genre should be processed normally."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="forza-horizon-6",
            game_title="Forza Horizon 6",
            platform="pc",
            metascore=1985.0,
            metascore_reviews=1986,
            user_score=1994.0,
            user_reviews=None,
            release_date="2026-05-19",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=88.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=100,
            genres=["Racing"],
            must_play=True,
            release_date="2026-05-19",
            slug="forza-horizon-6",
        )

        thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["action"])
        assert removed == 0, "Game genre 'Racing' not in reject_genre ['action'] — should NOT be removed"
        assert db.is_pending("forza-horizon-6") is True, "Game should remain pending"
        db.close()

    def test_reject_genre_empty_list(self, tmp_path: Path) -> None:
        """Empty reject_genre list should have no effect."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=95.0,
            metascore_reviews=100,
            user_score=9.0,
            user_reviews=500,
            release_date="2022-02-25",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=95.0,
            metascore_review_count=100,
            user_score=9.0,
            user_review_count=500,
            genres=["Action", "RPG"],
            must_play=True,
            release_date="2022-02-25",
            slug="elden-ring",
        )

        thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=[])
        assert removed == 0, "Empty reject_genre — game should NOT be removed"
        assert db.is_pending("elden-ring") is True, "Game should remain pending"
        db.close()

    def test_reject_genre_multi_match(self, tmp_path: Path) -> None:
        """Game with multiple genres where one matches reject_genre should be removed."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="cyberpunk-2077",
            game_title="Cyberpunk 2077",
            platform="pc",
            metascore=86.0,
            metascore_reviews=90,
            user_score=8.5,
            user_reviews=500,
            release_date="2020-12-10",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=86.0,
            metascore_review_count=90,
            user_score=8.5,
            user_review_count=500,
            genres=["Action", "RPG", "Open-World"],
            must_play=True,
            release_date="2020-12-10",
            slug="cyberpunk-2077",
        )

        thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            thresholds,
            reject_genre=["rpg", "sports"],
        )
        assert removed == 1, "Game has 'RPG' which is in reject_genre — should be removed"
        assert db.is_pending("cyberpunk-2077") is False
        db.close()

    def test_reject_genre_case_insensitive(self, tmp_path: Path) -> None:
        """Genre matching should be case-insensitive."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="hades-2",
            game_title="Hades II",
            platform="pc",
            metascore=90.0,
            metascore_reviews=80,
            user_score=8.5,
            user_reviews=300,
            release_date="2025-05-06",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=90.0,
            metascore_review_count=80,
            user_score=8.5,
            user_review_count=300,
            genres=["Early Access", "Roguelike"],
            must_play=False,
            release_date="2025-05-06",
            slug="hades-2",
        )

        thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            thresholds,
            reject_genre=["ROGUELIKE"],
        )
        assert removed == 1, "Case-insensitive match — 'ROGUELIKE' should match 'Roguelike'"
        db.close()

    def test_reject_genre_result_none(self, tmp_path: Path) -> None:
        """When lookup returns None, genre check is skipped and normal retry logic applies."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="unknown-game",
            game_title="Unknown Game",
            platform="pc",
            metascore=0.0,
            metascore_reviews=0,
            user_score=0.0,
            user_reviews=0,
            release_date="2026-01-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None  # lookup failed

        thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["action"])
        assert removed == 0, "Lookup returned None — genre check skipped, game stays for re-check"
        assert db.is_pending("unknown-game") is True, "Game should remain pending for re-try"
        db.close()

    def test_reject_genre_none_genres(self, tmp_path: Path) -> None:
        """When result.genres is None, genre check is skipped and score check proceeds."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="no-genre-game",
            game_title="No Genre Game",
            platform="pc",
            metascore=1985.0,
            metascore_reviews=1986,
            user_score=1994.0,
            user_reviews=None,
            release_date="2026-05-19",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=88.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=100,
            genres=None,
            must_play=True,
            release_date="2026-05-19",
            slug="no-genre-game",
        )

        thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["action"])
        assert removed == 0, "genres=None — genre check skipped, game should not be removed"
        assert db.is_pending("no-genre-game") is True, "Game should remain pending (scores pass)"
        db.close()

    def test_reject_genre_none_default(self, tmp_path: Path) -> None:
        """When reject_genre is None (default), the check is skipped entirely."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="default-test",
            game_title="Default Test",
            platform="pc",
            metascore=1985.0,
            metascore_reviews=1986,
            user_score=1994.0,
            user_reviews=None,
            release_date="2026-05-19",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=88.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=True,
            release_date="2026-05-19",
            slug="default-test",
        )

        thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        # reject_genre not passed (defaults to None)
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds)
        assert removed == 0, "reject_genre=None — game should not be removed by genre check"
        assert db.is_pending("default-test") is True, "Game should remain pending"
        db.close()

    def test_reject_genre_substring_broad(self, tmp_path: Path) -> None:
        """reject_genre=["RPG"] should match "Action RPG" (substring)."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="action-rpg-game",
            game_title="Action RPG Game",
            platform="pc",
            metascore=90.0,
            metascore_reviews=50,
            user_score=8.5,
            user_reviews=200,
            release_date="2026-01-01",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.5,
            user_review_count=200,
            genres=["Action RPG"],
            must_play=True,
            release_date="2026-01-01",
            slug="action-rpg-game",
        )

        thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["RPG"])
        assert removed == 1, "'RPG' should match 'Action RPG' via substring"
        assert db.is_pending("action-rpg-game") is False
        db.close()

    def test_reject_genre_substring_narrow(self, tmp_path: Path) -> None:
        """reject_genre=["Western RPG"] should NOT match "Action RPG" (substring mismatch)."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="action-rpg-game2",
            game_title="Action RPG Game 2",
            platform="pc",
            metascore=90.0,
            metascore_reviews=50,
            user_score=8.5,
            user_reviews=200,
            release_date="2026-01-01",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.5,
            user_review_count=200,
            genres=["Action RPG"],
            must_play=True,
            release_date="2026-01-01",
            slug="action-rpg-game2",
        )

        thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            thresholds,
            reject_genre=["Western RPG"],
        )
        assert removed == 0, "'Western RPG' should NOT match 'Action RPG' — substring not found"
        assert db.is_pending("action-rpg-game2") is True
        db.close()


class TestLogGameDetails:
    """_log_game_details end-to-end behaviour."""

    def test_log_game_details_logs_all_fields(self) -> None:
        from unittest.mock import patch

        from gamarr.metacritic import ScoreResult
        from gamarr.pipeline import _log_game_details

        mc = ScoreResult(
            title="Elden Ring",
            slug="elden-ring",
            metascore=96.0,
            metascore_review_count=120,
            user_score=8.5,
            user_review_count=5000,
            passed=True,
            genres=["Action", "RPG"],
            must_play=True,
            release_date="2022-02-25",
            description="A great game",
        )

        with patch("gamarr.pipeline.logger") as mock_logger:
            _log_game_details(mc)

        mock_logger.opt.assert_called_once_with(colors=True)
        mock_logger.opt.return_value.info.assert_called_once()
        msg = mock_logger.opt.return_value.info.call_args[0][0]
        assert "Elden Ring" in msg
        assert "96" in msg
        assert "120" in msg
        assert "8.5" in msg
        assert "5000" in msg
        assert "Action" in msg
        assert "Yes" in msg
        assert "2022-02-25" in msg

    def test_log_game_details_none_result_skips(self) -> None:
        from unittest.mock import patch

        from gamarr.pipeline import _log_game_details

        with patch("gamarr.pipeline.logger") as mock_logger:
            _log_game_details(None)

        mock_logger.opt.assert_not_called()

    def test_log_game_details_none_fields_use_fallbacks(self) -> None:
        from unittest.mock import patch

        from gamarr.metacritic import ScoreResult
        from gamarr.pipeline import _log_game_details

        mc = ScoreResult(
            title="Unknown Game",
            slug="unknown",
            metascore=None,
            metascore_review_count=None,
            user_score=None,
            user_review_count=None,
            passed=False,
            genres=None,
            must_play=None,
            release_date=None,
            description=None,
        )

        with patch("gamarr.pipeline.logger") as mock_logger:
            _log_game_details(mc)

        mock_logger.opt.assert_called_once_with(colors=True)
        mock_logger.opt.return_value.info.assert_called_once()
        msg = mock_logger.opt.return_value.info.call_args[0][0]
        assert "TBD" in msg
        assert "?" in msg
        assert "N/A" in msg
        assert "No" in msg


class TestFitgirlRecheckExpiry:
    """Tests for fitgirl_max_queue_days expiry recalculation."""

    def test_fitgirl_max_queue_days_updates_expiry(self, tmp_path: Path) -> None:
        """Game with passing scores should have expires_at recalculated to now + fitgirl_max_queue_days."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="passing-game",
            game_title="Passing Game",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            release_date="2026-06-01",
            expires_at=expires,
        )
        original_expiry = expires  # Capture before verification

        mock_mc = MagicMock()
        import types

        mock_result = types.SimpleNamespace(
            metascore=88.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=True,
            release_date="2026-06-01",
        )
        mock_mc.lookup_game.return_value = mock_result

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        _verify_pending_scores(db, mock_mc, "pc", thresholds, fitgirl_max_queue_days=60)

        # Game should still be pending
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        row = pending[0]
        # Expiry should be recalculated to now + 60 days (not the original +30)
        new_expiry = datetime.datetime.fromisoformat(row.expires_at)
        expected_min = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=59)
        expected_max = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=61)
        assert expected_min < new_expiry < expected_max, f"Expiry should be near now+60d, got {new_expiry}"
        # Verify it's different from the original
        assert row.expires_at != original_expiry, "Expiry should have been updated"
        db.close()

    def test_fitgirl_max_queue_days_zero_indefinite(self, tmp_path: Path) -> None:
        """fitgirl_max_queue_days=0 should set expiry to far-future (indefinite pending)."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="zero-days-game",
            game_title="Zero Days Game",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_result = types.SimpleNamespace(
            metascore=88.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=True,
            release_date="2026-06-01",
        )
        mock_mc.lookup_game.return_value = mock_result

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        _verify_pending_scores(db, mock_mc, "pc", thresholds, fitgirl_max_queue_days=0)

        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        # Expiry should be updated to far-future
        new_expiry = datetime.datetime.fromisoformat(pending[0].expires_at)
        expected_min = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=9998)
        expected_max = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=10000)
        assert expected_min < new_expiry < expected_max, (
            f"Expiry should be near now+9999d (indefinite), got {new_expiry}"
        )
        db.close()

    def test_fitgirl_max_queue_days_does_not_affect_failure(self, tmp_path: Path) -> None:
        """Game with failing scores should NOT have its expiry updated."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="failing-game",
            game_title="Failing Game",
            platform="pc",
            metascore=62.0,
            user_score=3.3,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_result = types.SimpleNamespace(
            metascore=62.0,
            metascore_review_count=25,
            user_score=3.3,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )
        mock_mc.lookup_game.return_value = mock_result

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        _verify_pending_scores(db, mock_mc, "pc", thresholds, fitgirl_max_queue_days=60)

        # Game should still be pending (re-check)
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        # Expiry must NOT be updated (still original +30d)
        assert pending[0].expires_at == expires, f"Expiry should be unchanged ({expires}), got {pending[0].expires_at}"
        db.close()


class TestRejectTitle:
    """Tests for reject_title title substring filtering."""

    def test_reject_title_at_browse(self, tmp_path: Path) -> None:
        """Game with title matching reject_title should be skipped at browse stage."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "Resident Evil 4 Remake",
                "slug": "resident-evil-4-remake",
                "score": 85,
                "critic_review_count": 20,
                "user_rating": 8.0,
                "user_review_count": 100,
            },
        ]
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games,
            "pc",
            db,
            thresholds,
            reject_title=["Remake"],
        )
        assert added == 0, "Game with matching title should not be added"
        assert not db.is_pending("resident-evil-4-remake")
        db.close()

    def test_reject_title_at_verify(self, tmp_path: Path) -> None:
        """Game with title matching reject_title should be removed during verification."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="vr-game",
            game_title="VR Adventure",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=20,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        assert db.is_pending("vr-game") is True
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_title=["VR"])
        assert removed == 1, "Game with matching title should be removed"
        assert not db.is_pending("vr-game"), "Game should no longer be pending"
        db.close()

    def test_reject_title_no_match(self, tmp_path: Path) -> None:
        """Game with non-matching title should proceed normally."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "Elden Ring",
                "slug": "elden-ring",
                "score": 96,
                "critic_review_count": 120,
                "user_rating": 8.5,
                "user_review_count": 5000,
            },
        ]
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games,
            "pc",
            db,
            thresholds,
            reject_title=["Remake"],
            mc=_passing_detail_client(),
        )
        assert added == 1, "Non-matching game should be added"
        assert db.is_pending("elden-ring")
        db.close()

    def test_reject_title_empty_list(self, tmp_path: Path) -> None:
        """Empty reject_title should have no effect."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "VR Adventure",
                "slug": "vr-adventure",
                "score": 85,
                "critic_review_count": 20,
                "user_rating": 8.0,
                "user_review_count": 100,
            },
        ]
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games,
            "pc",
            db,
            thresholds,
            reject_title=[],
            mc=_passing_detail_client(),
        )
        assert added == 1, "Empty reject_title should not filter anything"
        db.close()

    def test_reject_title_case_insensitive(self, tmp_path: Path) -> None:
        """reject_title should match case-insensitively."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "The Legend of Zelda: Remake",
                "slug": "zelda-remake",
                "score": 95,
                "critic_review_count": 100,
                "user_rating": 9.0,
                "user_review_count": 5000,
            },
        ]
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games,
            "pc",
            db,
            thresholds,
            reject_title=["remake"],  # lowercase, title has "Remake"
        )
        assert added == 0, "reject_title should match case-insensitively"
        db.close()

    def test_reject_title_substring(self, tmp_path: Path) -> None:
        """reject_title should match partial substrings, not just whole words."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "Collection of Classic Games Vol 3",
                "slug": "collection-classic-3",
                "score": 80,
                "critic_review_count": 10,
                "user_rating": 7.5,
                "user_review_count": 50,
            },
        ]
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games,
            "pc",
            db,
            thresholds,
            reject_title=["Classic"],
        )
        assert added == 0, "reject_title should match on substrings"
        db.close()


class TestScrapeHealth:
    """Tests for _check_scrape_health connectivity checks."""

    def test_scrape_health_metacritic_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When Metacritic responds <500, should return 'metacritic_broken'."""
        import requests

        from gamarr.pipeline import _check_scrape_health

        def mock_head(url: str, **kwargs: Any) -> Any:
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 200
            return resp

        monkeypatch.setattr(requests, "head", mock_head)
        assert _check_scrape_health() == "metacritic_broken"

    def test_scrape_health_metacritic_5xx(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When Metacritic returns 503, should return 'metacritic_down'."""
        import requests

        from gamarr.pipeline import _check_scrape_health

        def mock_head(url: str, **kwargs: Any) -> Any:
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 503
            return resp

        monkeypatch.setattr(requests, "head", mock_head)
        assert _check_scrape_health() == "metacritic_down"

    def test_scrape_health_metacritic_down_google_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When Metacritic fails but Google works, return 'metacritic_down'."""
        import requests

        from gamarr.pipeline import _check_scrape_health

        def mock_head(url: str, **kwargs: Any) -> Any:
            if "metacritic" in url:
                raise requests.ConnectionError("Metacritic unreachable")
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 200
            return resp

        monkeypatch.setattr(requests, "head", mock_head)
        assert _check_scrape_health() == "metacritic_down"

    def test_scrape_health_internet_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When both Metacritic and Google fail, return 'internet_down'."""
        import requests

        from gamarr.pipeline import _check_scrape_health

        def mock_head(url: str, **kwargs: Any) -> Any:
            raise requests.ConnectionError("Network unreachable")

        monkeypatch.setattr(requests, "head", mock_head)
        assert _check_scrape_health() == "internet_down"

    def test_verify_phase_passes_with_notifier(self, tmp_path: Path) -> None:
        """When at least one game verifies, no scrape notification fires."""
        import datetime
        import types
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.notifications import Notifier
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="passing-game",
            game_title="Passing Game",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            release_date="2026-06-01",
            expires_at=expires,
        )

        # Also add a "no details" game so we have a mix of pass/fail
        db.record_pending(
            slug="no-details-game",
            game_title="No Details Game",
            platform="pc",
            metascore=62.0,
            user_score=3.3,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.side_effect = [
            types.SimpleNamespace(
                metascore=88.0,
                metascore_review_count=50,
                user_score=8.0,
                user_review_count=100,
                genres=["Action"],
                must_play=True,
                release_date="2026-06-01",
            ),
            types.SimpleNamespace(
                metascore=62.0,
                metascore_review_count=5,
                user_score=3.3,
                user_review_count=20,
                genres=["RPG"],
                must_play=False,
                release_date="2026-06-01",
            ),
        ]

        mock_notifier = MagicMock(spec=Notifier)

        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            thresholds,
            notifier=mock_notifier,
        )
        # At least one game succeeded, so no scrape notification
        mock_notifier.send_scrape_notification.assert_not_called()
        db.close()

    def test_verify_phase_all_fail_sends_notification(self, tmp_path: Path) -> None:
        """When every lookup returns None, scrape notification should fire."""
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.notifications import Notifier
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="failing-game",
            game_title="Failing Game",
            platform="pc",
            metascore=62.0,
            user_score=3.3,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None  # All lookups return None

        mock_notifier = MagicMock(spec=Notifier)

        with patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"):
            _verify_pending_scores(
                db,
                mock_mc,
                "pc",
                {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10},
                notifier=mock_notifier,
            )

        mock_notifier.send_scrape_notification.assert_called_once()
        db.close()

    def test_verify_phase_all_fail_metacritic_down_sends_notification(self, tmp_path: Path) -> None:
        """When _check_scrape_health returns metacritic_down, notification should fire."""
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.notifications import Notifier
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="down-game",
            game_title="Down Game",
            platform="pc",
            metascore=62.0,
            user_score=3.3,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None

        mock_notifier = MagicMock(spec=Notifier)

        with patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_down"):
            _verify_pending_scores(
                db,
                mock_mc,
                "pc",
                {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10},
                notifier=mock_notifier,
            )

        mock_notifier.send_scrape_notification.assert_called_once()
        db.close()

    def test_verify_phase_all_fail_internet_down_skips_notification(self, tmp_path: Path) -> None:
        """When _check_scrape_health returns internet_down, no notification fires."""
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.notifications import Notifier
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="offline-game",
            game_title="Offline Game",
            platform="pc",
            metascore=62.0,
            user_score=3.3,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None

        mock_notifier = MagicMock(spec=Notifier)

        with patch("gamarr.pipeline._check_scrape_health", return_value="internet_down"):
            _verify_pending_scores(
                db,
                mock_mc,
                "pc",
                {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10},
                notifier=mock_notifier,
            )

        mock_notifier.send_scrape_notification.assert_not_called()
        db.close()


class TestCancellation:
    """Cancel event propagation through the pipeline."""

    def test_process_verify_batch_returns_early_when_precancelled(
        self,
        tmp_path: Path,
    ) -> None:
        """When cancel_event is pre-set, _process_verify_batch returns
        immediately with (0, False) without processing any games."""
        import threading
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _process_verify_batch

        db = Database(str(tmp_path / "test.db"))
        cancel_event = threading.Event()
        cancel_event.set()  # Pre-set before call

        mock_mc = MagicMock()
        batch = [MagicMock()]

        removed, any_success = _process_verify_batch(
            db,
            mock_mc,
            "pc",
            {
                "min_criticscore": 75,
                "min_criticscore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
            batch,
            max_verify=10,
            total_pending=10,
            cancel_event=cancel_event,
        )

        assert removed == 0
        assert any_success is False
        mock_mc.lookup_game.assert_not_called()

    def test_process_verify_batch_handles_lookup_exception(
        self,
        tmp_path: Path,
    ) -> None:
        """When lookup_game raises, the exception is caught and the game stays pending."""
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _process_verify_batch

        db = Database(str(tmp_path / "test.db"))
        mock_mc = MagicMock()
        mock_mc.lookup_game.side_effect = RuntimeError("Network timeout")

        batch = [MagicMock()]
        batch[0].game_title = "Test Game"
        batch[0].slug = "test-game"
        batch[0].metascore = 85.0
        batch[0].metascore_reviews = 20
        batch[0].user_score = 8.5
        batch[0].user_reviews = 50
        batch[0].release_date = "2025-01-01"
        batch[0].expires_at = "2099-01-01T00:00:00"
        batch[0].platform = "pc"
        batch[0].score_checks_passed = False

        removed, any_success = _process_verify_batch(
            db,
            mock_mc,
            "pc",
            {
                "min_criticscore": 75,
                "min_criticscore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
            batch,
            max_verify=1,
            total_pending=1,
        )

        assert any_success is False
        mock_mc.lookup_game.assert_called_once()
        mock_mc.reset_cache_hits.assert_called_once()

    def test_process_verify_batch_cancels_mid_batch(
        self,
        tmp_path: Path,
    ) -> None:
        """When cancel_event fires mid-batch, remaining futures are cancelled."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _process_verify_batch

        db = Database(str(tmp_path / "test.db"))
        mock_mc = MagicMock()

        fake_result = MagicMock()
        fake_result.metascore = 85.0
        fake_result.metascore_review_count = 20
        fake_result.user_score = 8.5
        fake_result.user_review_count = 50
        fake_result.genres = ["Action"]
        fake_result.must_play = True
        fake_result.release_date = "2025-01-01"
        fake_result.description = None
        mock_mc.lookup_game.return_value = fake_result

        # Fire cancel_event after the first result is checked
        call_count = 0

        import threading

        cancel_event = threading.Event()

        def fire_cancel_on_second_call(*args: object, **kwargs: object) -> bool:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                cancel_event.set()
            return cancel_event.is_set()

        batch = []
        for i in range(3):
            g = MagicMock()
            g.game_title = f"Test Game {i}"
            g.slug = f"test-game-{i}"
            g.metascore = 85.0
            g.metascore_reviews = 20
            g.user_score = 8.5
            g.user_reviews = 50
            g.release_date = "2025-01-01"
            g.expires_at = "2099-01-01T00:00:00"
            g.platform = "pc"
            g.score_checks_passed = False
            batch.append(g)

        with patch("gamarr.pipeline.is_cancelled", side_effect=fire_cancel_on_second_call):
            removed, any_success = _process_verify_batch(
                db,
                mock_mc,
                "pc",
                {
                    "min_criticscore": 75,
                    "min_criticscore_reviews": 5,
                    "min_user_score": 7.5,
                    "min_user_reviews": 10,
                },
                batch,
                max_verify=3,
                total_pending=3,
                cancel_event=cancel_event,
            )

        assert call_count >= 2

    def test_run_acquisition_returns_early_when_precancelled(self) -> None:
        """When cancel_event is pre-set, run_acquisition returns
        early — the year loop breaks before calling scan_recent_games
        and no further pipeline steps run."""
        import threading
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        cancel_event = threading.Event()
        cancel_event.set()

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
                cancel_event=cancel_event,
            )

        # is_cancelled check at the top of the year loop prevents the
        # scan entirely when the event is pre-set — returns empty results.
        mock_mc.scan_recent_games.assert_not_called()
        mock_mc.lookup_game.assert_not_called()
        mock_source.fetch_sitemap.assert_not_called()
        assert results == []


class TestBrowseReviewCountPrefilter:
    """_reject_by_browse_review_counts function."""

    def test_returns_none_when_both_counts_missing(self) -> None:
        """When both critic_review_count and user_review_count are None
        (not available in browse data), the function should return None
        so the game proceeds to detail-page verification as before."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Test Game",
            "slug": "test-game",
            "critic_review_count": None,
            "user_review_count": None,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=5, min_user_reviews=10)
        assert result is None

    def test_returns_none_when_counts_sufficient(self) -> None:
        """When both counts meet or exceed thresholds, return None."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Popular Game",
            "slug": "popular-game",
            "critic_review_count": 20,
            "user_review_count": 50,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=5, min_user_reviews=10)
        assert result is None

    def test_returns_reason_when_critic_count_too_low(self) -> None:
        """When critic_review_count is below min_critic_reviews, return the reason string."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Obscure Game",
            "slug": "obscure-game",
            "critic_review_count": 2,
            "user_review_count": 0,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=5, min_user_reviews=10)
        assert result == "critic_reviews_too_few_at_browse"

    def test_returns_reason_when_user_count_too_low(self) -> None:
        """When user_review_count is below min_user_reviews (and critic count is fine), return reason."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Unreviewed Game",
            "slug": "unreviewed-game",
            "critic_review_count": 20,
            "user_review_count": 3,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=5, min_user_reviews=10)
        assert result == "user_reviews_too_few_at_browse"

    def test_ignores_zero_threshold(self) -> None:
        """When min thresholds are 0 (disabled), function should never reject."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Zero Reviews Game",
            "slug": "zero-review-game",
            "critic_review_count": 0,
            "user_review_count": 0,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=0, min_user_reviews=0)
        assert result is None

    def test_process_browse_games_skips_low_review_count_games(self, tmp_path: Path) -> None:
        """Games with browse-page critic_review_count below threshold
        should NOT be added to the pending queue by _process_browse_games."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "Low Reviews",
                "slug": "low-reviews",
                "score": 1478.0,
                "critic_review_count": 2,  # below threshold 5
                "user_rating": 2007.0,
                "user_review_count": 50,
                "release_date": "2026-06-01",
            },
        ]
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        new_count = _process_browse_games(
            browse_games,
            platform="pc",
            db=db,
            thresholds=thresholds,
            max_queue_days=30,
        )
        assert new_count == 0, "Low-review-count game should not be added to pending"
        pending = db.get_pending(platform="pc")
        assert len(pending) == 0
        db.close()

    def test_process_browse_games_passes_when_review_counts_unavailable(self, tmp_path: Path) -> None:
        """Games with None review counts on the browse page should still
        enter the pending queue (fallback to detail-page verification)."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "No Review Data",
                "slug": "no-review-data",
                "score": 1478.0,
                "critic_review_count": None,  # missing from browse data
                "user_rating": 2007.0,
                "user_review_count": None,
                "release_date": "2026-06-01",
            },
        ]
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        new_count = _process_browse_games(
            browse_games,
            platform="pc",
            db=db,
            thresholds=thresholds,
            max_queue_days=30,
            mc=_passing_detail_client(),
        )
        assert new_count == 1, "Game with missing review data should enter pending"
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        assert pending[0].slug == "no-review-data"
        db.close()


class TestLogVerifyProgress:
    """Tests for _log_verify_progress helper."""

    def test_log_verify_progress_logs_at_interval(self) -> None:
        """Should log at every 100th verified game."""
        from unittest.mock import patch

        from gamarr.pipeline import _log_verify_progress

        with patch("gamarr.pipeline.logger") as mock_logger:
            _log_verify_progress(verified=100, max_verify=500, total=5000)
        mock_logger.debug.assert_called_once()
        args, _ = mock_logger.debug.call_args
        # Loguru: args[0]=format_string, args[1]=verified, args[2]=total
        assert "games" in str(args[0]), "Should mention games"
        assert args[1] == 100, f"Expected verified count 100, got {args[1]}"

    def test_log_verify_progress_skips_non_interval(self) -> None:
        """Should NOT log when verified is not a multiple of 100."""
        from unittest.mock import patch

        from gamarr.pipeline import _log_verify_progress

        with patch("gamarr.pipeline.logger") as mock_logger:
            _log_verify_progress(verified=50, max_verify=500, total=5000)
        mock_logger.debug.assert_not_called()

    def test_log_verify_progress_logs_at_zero(self) -> None:
        """Should log when verified is 0 (starts at first iteration)."""
        from unittest.mock import patch

        from gamarr.pipeline import _log_verify_progress

        with patch("gamarr.pipeline.logger") as mock_logger:
            _log_verify_progress(verified=0, max_verify=500, total=5000)
        mock_logger.debug.assert_called_once()


class TestProcessAgedGames:
    """Tests for _process_aged_games sweep function."""

    def test_process_aged_games_uses_active_sort_order(self) -> None:
        """Aged processing must request the pending queue in active sort order."""
        from unittest.mock import MagicMock

        from gamarr.pipeline import AcquisitionConfig, _process_aged_games

        db = MagicMock()
        db.get_pending.return_value = []
        cfg = AcquisitionConfig(
            min_criticscore=75,
            min_criticscore_reviews=10,
            min_user_score=7.5,
            min_user_reviews=10,
            age_recheck_weeks=4,
            sort_order="userscore",
        )

        assert _process_aged_games(db, cfg, platform="pc") == 0
        db.get_pending.assert_called_once_with(platform="pc", sort_order="userscore")

    def test_should_age_game_returns_false_when_not_checked(self) -> None:
        """_should_age_game returns False when last_checked_at is None."""
        from unittest.mock import MagicMock

        from gamarr.pipeline import _should_age_game

        game = MagicMock(last_checked_at=None, release_date="2010-01-01", score_checks_passed=True)
        assert _should_age_game(game, age_recheck_weeks=52) is False

    def test_should_age_game_returns_false_when_no_release_date(self) -> None:
        """_should_age_game returns False when release_date is None."""
        from unittest.mock import MagicMock

        from gamarr.pipeline import _should_age_game

        game = MagicMock(last_checked_at="2024-01-01", release_date=None, score_checks_passed=True)
        assert _should_age_game(game, age_recheck_weeks=52) is False

    def test_should_age_game_returns_false_when_scores_not_passed(self) -> None:
        """_should_age_game returns False when score_checks_passed is not True."""
        from unittest.mock import MagicMock

        from gamarr.pipeline import _should_age_game

        game = MagicMock(last_checked_at="2024-01-01", release_date="2010-01-01", score_checks_passed=False)
        assert _should_age_game(game, age_recheck_weeks=52) is False

    def test_should_age_game_returns_true_for_old_verified_game(self) -> None:
        """_should_age_game returns True when all criteria are met."""
        from unittest.mock import MagicMock

        from gamarr.pipeline import _should_age_game

        game = MagicMock(last_checked_at="2024-01-01", release_date="2010-01-01", score_checks_passed=True)
        # 2010-01-01 is well over 52 weeks ago — should be True
        assert _should_age_game(game, age_recheck_weeks=52) is True

    def test_process_aged_games_processes_old_verified_games(self, tmp_path: Path) -> None:
        """Old games with last_checked_at set should be processed."""
        import datetime

        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig, _process_aged_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
        db.record_pending(
            slug="old-checked",
            game_title="Old Checked",
            platform="pc",
            metascore=80.0,
            user_score=7.5,
            release_date="2010-01-01",
            expires_at=expires,
        )
        with db._session() as session:
            from gamarr.database import PendingGame

            row = session.get(PendingGame, "old-checked")
            assert row is not None, "old-checked should exist"
            row.last_checked_at = past
            row.score_checks_passed = True
            session.commit()

        db.record_pending(
            slug="old-unchecked",
            game_title="Old Unchecked",
            platform="pc",
            metascore=80.0,
            user_score=7.5,
            release_date="2010-01-01",
            expires_at=expires,
        )

        cfg = AcquisitionConfig(
            min_criticscore=75,
            min_criticscore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            age_recheck_weeks=52,
        )
        count = _process_aged_games(db, cfg, platform="pc")
        assert count == 1, f"Expected 1 processed, got {count}"
        assert not db.is_pending("old-checked"), "Old checked game should be removed"
        assert db.is_pending("old-unchecked"), "Old unchecked game should remain"
        stats = db.get_stats()
        assert stats["total"] == 1, "One history record should exist"
        db.close()

    def test_process_aged_games_skips_failed_score_check(self, tmp_path: Path) -> None:
        """Old games that were checked but failed score verification should NOT be aged out."""
        import datetime

        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig, _process_aged_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=1)).isoformat()

        # Game that was checked but scores failed verification
        db.record_pending(
            slug="old-checked-failed",
            game_title="Old Checked Failed",
            platform="pc",
            metascore=60.0,  # below typical threshold
            user_score=5.0,
            release_date="2010-01-01",
            expires_at=expires,
        )
        with db._session() as session:
            from gamarr.database import PendingGame

            row = session.get(PendingGame, "old-checked-failed")
            assert row is not None
            row.last_checked_at = past
            row.score_checks_passed = False
            session.commit()

        cfg = AcquisitionConfig(
            min_criticscore=75,
            min_criticscore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            age_recheck_weeks=52,
        )
        count = _process_aged_games(db, cfg, platform="pc")
        assert count == 0, f"Expected 0 processed (scores failed), got {count}"
        assert db.is_pending("old-checked-failed"), "Failed game should remain in queue"
        db.close()

    def test_process_aged_games_skips_recent_games(self, tmp_path: Path) -> None:
        """Recent games should NOT be processed by the sweep."""
        import datetime

        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig, _process_aged_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        recent = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
        db.record_pending(
            slug="recent-game",
            game_title="Recent Game",
            platform="pc",
            metascore=80.0,
            user_score=7.5,
            release_date=recent,
            expires_at=expires,
        )
        with db._session() as session:
            from gamarr.database import PendingGame

            row = session.get(PendingGame, "recent-game")
            assert row is not None, "recent-game should exist"
            row.last_checked_at = past
            session.commit()

        cfg = AcquisitionConfig(
            min_criticscore=75,
            min_criticscore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            age_recheck_weeks=52,
        )
        count = _process_aged_games(db, cfg, platform="pc")
        assert count == 0, "Recent game should not be processed"
        assert db.is_pending("recent-game"), "Recent game should remain"
        db.close()

    def test_process_aged_games_disabled_when_none(self, tmp_path: Path) -> None:
        """When age_recheck_weeks is None, no games should be processed."""
        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig, _process_aged_games

        db = Database(str(tmp_path / "test.db"))
        cfg = AcquisitionConfig(
            min_criticscore=75,
            min_criticscore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            age_recheck_weeks=None,
        )
        count = _process_aged_games(db, cfg, platform="pc")
        assert count == 0
        db.close()

    def test_verify_result_touches_pending_even_when_none(self, tmp_path: Path) -> None:
        """_process_verify_result should set last_checked_at even when result is None.

        Without this, games that fail lookup never get ``last_checked_at`` set,
        so ``_process_aged_games`` skips them forever and they get re-verified
        every cycle instead of being aged out.
        """
        import datetime

        from gamarr.database import Database, PendingGame
        from gamarr.pipeline import _process_verify_result

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="unreviewed-game",
            game_title="Unreviewed Game",
            platform="pc",
            metascore=80.0,
            user_score=7.5,
            release_date="2010-01-01",
            expires_at=expires,
        )
        game = db.get_pending()[0]
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        # result=None simulates a game with no Metacritic page
        removed = _process_verify_result(db, game, result=None, thresholds=thresholds)
        assert removed == -1, "Game with no result should stay pending (kept, failed scores)"

        # The game should now have last_checked_at set
        with db._session() as session:
            row = session.get(PendingGame, "unreviewed-game")
            assert row is not None
            assert row.last_checked_at is not None, (
                "last_checked_at should be set even when result is None — "
                "otherwise the game gets re-verified every cycle"
            )
        db.close()


class TestRealScoresPassThresholds:
    """_real_scores_pass_thresholds correctly enforces review counts."""

    def test_rejects_none_user_reviews_when_threshold_set(self) -> None:
        """When user_review_count is None and min_user_reviews > 0,
        the check should fail.
        """
        import types

        from gamarr.pipeline import _real_scores_pass_thresholds

        result = types.SimpleNamespace(
            metascore=91.0,
            metascore_review_count=26,
            user_score=8.6,
            user_review_count=None,
        )
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 10,
            "min_user_score": 8.0,
            "min_user_reviews": 10,
        }
        assert _real_scores_pass_thresholds(result, thresholds) is False

    def test_rejects_none_metascore_reviews_when_threshold_set(self) -> None:
        """When metascore_review_count is None and min_criticscore_reviews > 0,
        the check should fail.
        """
        import types

        from gamarr.pipeline import _real_scores_pass_thresholds

        result = types.SimpleNamespace(
            metascore=91.0,
            metascore_review_count=None,
            user_score=8.6,
            user_review_count=20,
        )
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 10,
            "min_user_score": 8.0,
            "min_user_reviews": 10,
        }
        assert _real_scores_pass_thresholds(result, thresholds) is False

    def test_passes_with_sufficient_reviews(self) -> None:
        """When review counts are present and meet thresholds, check passes."""
        import types

        from gamarr.pipeline import _real_scores_pass_thresholds

        result = types.SimpleNamespace(
            metascore=91.0,
            metascore_review_count=26,
            user_score=8.6,
            user_review_count=15,
        )
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 10,
            "min_user_score": 8.0,
            "min_user_reviews": 10,
        }
        assert _real_scores_pass_thresholds(result, thresholds) is True

    def test_passes_with_zero_thresholds_and_none_reviews(self) -> None:
        """When min_user_reviews is 0 (no threshold), None review counts pass."""
        import types

        from gamarr.pipeline import _real_scores_pass_thresholds

        result = types.SimpleNamespace(
            metascore=91.0,
            metascore_review_count=26,
            user_score=8.6,
            user_review_count=None,
        )
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 10,
            "min_user_score": 8.0,
            "min_user_reviews": 0,
        }
        assert _real_scores_pass_thresholds(result, thresholds) is True

    def test_rejects_tbd_metascore_when_threshold_set(self) -> None:
        """When metascore is None (TBD) and min_criticscore > 0, fail.

        A game with no metascore (like WEBFISHING which shows "TBD")
        should be rejected when min_criticscore > 0, even if user scores
        would pass.
        """
        import types

        from gamarr.pipeline import _real_scores_pass_thresholds

        result = types.SimpleNamespace(
            metascore=None,
            metascore_review_count=None,
            user_score=8.6,
            user_review_count=29,
        )
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 10,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        assert _real_scores_pass_thresholds(result, thresholds) is False

    def test_rejects_when_result_is_none(self) -> None:
        """When result is None, scores cannot pass."""
        from gamarr.pipeline import _real_scores_pass_thresholds

        assert _real_scores_pass_thresholds(None, {}) is False

    def test_rejects_when_user_score_is_none_and_threshold_set(self) -> None:
        """When user_score is None and min_user_score > 0, fail."""
        import types

        from gamarr.pipeline import _real_scores_pass_thresholds

        result = types.SimpleNamespace(
            metascore=80.0,
            metascore_review_count=20,
            user_score=None,
            user_review_count=15,
        )
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 10,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        assert _real_scores_pass_thresholds(result, thresholds) is False

    def test_rejects_when_no_review_data_at_all(self) -> None:
        """When all scores and review counts are absent, cannot pass."""
        import types

        from gamarr.pipeline import _real_scores_pass_thresholds

        result = types.SimpleNamespace(
            metascore=None,
            metascore_review_count=None,
            user_score=None,
            user_review_count=None,
        )
        thresholds = {
            "min_criticscore": 0,
            "min_criticscore_reviews": 0,
            "min_user_score": 0,
            "min_user_reviews": 0,
        }
        assert _real_scores_pass_thresholds(result, thresholds) is False

    def test_rejects_zero_user_score_with_no_reviews_and_thresholds(self) -> None:
        """User score 0.0 with 0 reviews should be rejected when thresholds are set.

        A game with ``user_score=0.0`` and ``user_review_count=0`` has NO
        meaningful user score data. It should fail both ``min_user_score``
        and ``min_user_reviews`` checks instead of being silently skipped.
        """
        import types

        from gamarr.pipeline import _real_scores_pass_thresholds

        result = types.SimpleNamespace(
            metascore=82.0,
            metascore_review_count=37,
            user_score=0.0,
            user_review_count=0,
        )
        thresholds = {
            "min_criticscore": 75,
            "min_criticscore_reviews": 10,
            "min_user_score": 8.0,
            "min_user_reviews": 10,
        }
        assert _real_scores_pass_thresholds(result, thresholds) is False


class TestAnyThresholdedScoreAbsent:
    """_any_thresholded_score_absent helper edge cases."""

    @staticmethod
    def _make_result(
        metascore: float | None = 80.0,
        user_score: float | None = 8.0,
    ) -> object:
        import types

        return types.SimpleNamespace(
            metascore=metascore,
            metascore_review_count=10,
            user_score=user_score,
            user_review_count=10,
        )

    def test_rejects_absent_metascore_with_active_threshold(self) -> None:
        from gamarr.pipeline import _any_thresholded_score_absent

        result = self._make_result(metascore=None)
        assert (
            _any_thresholded_score_absent(
                result,
                {"min_criticscore": 75, "min_user_score": 7.5},
            )
            is True
        )

    def test_rejects_absent_user_score_with_active_threshold(self) -> None:
        from gamarr.pipeline import _any_thresholded_score_absent

        result = self._make_result(user_score=None)
        assert (
            _any_thresholded_score_absent(
                result,
                {"min_criticscore": 75, "min_user_score": 7.5},
            )
            is True
        )

    def test_passes_when_both_scores_present(self) -> None:
        from gamarr.pipeline import _any_thresholded_score_absent

        result = self._make_result()
        assert (
            _any_thresholded_score_absent(
                result,
                {"min_criticscore": 75, "min_user_score": 7.5},
            )
            is False
        )

    def test_passes_when_all_thresholds_zero(self) -> None:
        from gamarr.pipeline import _any_thresholded_score_absent

        result = self._make_result(metascore=None, user_score=None)
        assert (
            _any_thresholded_score_absent(
                result,
                {"min_criticscore": 0, "min_user_score": 0},
            )
            is False
        )


class TestFailsReviewCountCheck:
    """_fails_review_count_check helper edge cases."""

    def test_fails_when_review_count_is_none_with_score(self) -> None:
        from gamarr.pipeline import _fails_review_count_check

        assert (
            _fails_review_count_check(
                score_value=8.0,
                review_count=None,
                threshold=5,
            )
            is True
        )

    def test_fails_when_review_count_is_zero_with_score(self) -> None:
        from gamarr.pipeline import _fails_review_count_check

        assert (
            _fails_review_count_check(
                score_value=8.0,
                review_count=0,
                threshold=5,
            )
            is True
        )

    def test_passes_when_threshold_is_zero(self) -> None:
        from gamarr.pipeline import _fails_review_count_check

        assert (
            _fails_review_count_check(
                score_value=8.0,
                review_count=None,
                threshold=0,
            )
            is False
        )

    def test_passes_when_score_is_none(self) -> None:
        from gamarr.pipeline import _fails_review_count_check

        assert (
            _fails_review_count_check(
                score_value=None,
                review_count=None,
                threshold=5,
            )
            is False
        )

    def test_fails_when_score_is_zero_with_no_reviews(self) -> None:
        """A score of 0.0 with no reviews should fail the review count check."""
        from gamarr.pipeline import _fails_review_count_check

        assert (
            _fails_review_count_check(
                score_value=0.0,
                review_count=None,
                threshold=5,
            )
            is True
        )

    def test_passes_when_review_count_sufficient(self) -> None:
        from gamarr.pipeline import _fails_review_count_check

        assert (
            _fails_review_count_check(
                score_value=8.0,
                review_count=20,
                threshold=5,
            )
            is False
        )


class TestAgedGamesMatchOrder:
    """Old verified games must be matched against FitGirl BEFORE being aged out."""

    def test_old_verified_game_matches_before_aging(self, tmp_path: Path) -> None:
        """A pending game with an old release date that passes score
        verification must be matched against FitGirl BEFORE
        ``_process_aged_games`` removes it from the queue.

        Reproduces the bug where ``_process_aged_games`` runs BEFORE
        the FitGirl sitemap fetch and matching phase, causing every
        old verified game to be silently removed before it can match.
        """
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import run_acquisition

        db_path = str(tmp_path / "test.db")

        # Pre-seed the DB with an old verified pending game that has
        # a matching FitGirl source title.
        db = Database(db_path)
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="old-but-matchable",
            game_title="Old But Matchable",
            platform="pc",
            metascore=85.0,
            metascore_reviews=20,
            user_score=8.0,
            user_reviews=100,
            release_date="2025-01-01",  # Old — older than age_recheck_weeks=4
            expires_at=expires,
        )
        # Mark as score-checked so _verify_pending_scores touches it
        db.update_pending_scores(slug="old-but-matchable", metascore=85.0, user_score=8.0)
        # Pre-populate FitGirl source titles so matching works even
        # if the mocked fetch_sitemap does nothing
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Old But Matchable", "url": "https://example.com/old-but-matchable"}],
        )
        db.close()

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            import types

            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source

            # Metacritic browse returns no new games
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            # Detail-page lookup returns passing scores so the
            # game stays in pending during verification
            mock_mc.lookup_game.return_value = types.SimpleNamespace(
                metascore=85.0,
                metascore_review_count=20,
                user_score=8.0,
                user_review_count=100,
                genres=["Action"],
                must_play=False,
                release_date="2025-01-01",
            )
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.is_connected.return_value = True
            mock_qbt.add_torrent.return_value = "gamarr-tag"
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                platform="pc",
                db_path=db_path,
                qbt_host="localhost",
                qbt_port=8080,
                min_criticscore=75,
                min_criticscore_reviews=5,
                min_user_score=7.5,
                min_user_reviews=10,
                max_pages=52,
                max_cycle_pages=100,
                age_recheck_weeks=4,
            )

            # The old game should be matched and delivered BEFORE
            # _process_aged_games removes it.  With the bug (aged
            # before match), results is empty.  After the fix (match
            # before aged), results contains the match.
            assert len(results) >= 1, "Old verified game should match FitGirl before being aged out"


class TestScanWindowAdvancing:
    """The scan window must not expand to max_pages when the advancing
    cutoff reaches the hard limit."""

    def test_window_uses_max_pages(self, tmp_path: Path) -> None:
        """Acquisition uses max_pages to limit browse scanning."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                platform="pc",
                db_path=":memory:",
                qbt_host="localhost",
                qbt_port=8080,
                max_pages=104,
                max_cycle_pages=100,
            )
            assert isinstance(results, list)
            assert mock_mc.scan_recent_games.call_count > 0

    def test_max_cycle_pages_steady_state_persists_across_cycles(self, tmp_path: Path) -> None:
        """Acquisition runs with max_pages and max_cycle_pages."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                platform="pc",
                db_path=":memory:",
                qbt_host="localhost",
                qbt_port=8080,
                max_pages=104,
                max_cycle_pages=100,
            )
            assert isinstance(results, list)

    def test_sort_order_change_detected(self, tmp_path: Path) -> None:
        """When sort_order changes, the pipeline detects and logs the change."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                platform="pc",
                db_path=":memory:",
                qbt_host="localhost",
                qbt_port=8080,
                max_pages=104,
                max_cycle_pages=100,
                sort_order="new",
            )
            assert isinstance(results, list)


class TestBacklogAdvancing:
    """Backlog page advancement across scheduler cycles."""

    def test_backlog_advances_start_page_across_cycles(self, tmp_path: Path) -> None:
        """Start_page should come from persisted last_scanned_page, not 1."""
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import run_acquisition

        db_path = str(tmp_path / "test.db")
        scan_year = datetime.datetime.now(tz=datetime.UTC).year
        db = Database(db_path)
        db.set_last_scanned_page("pc", scan_year, 4)
        db.close()

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            # Set _recent_games_last_page so DB set_last_scanned_page receives an int
            mock_mc._recent_games_last_page = 4
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                platform="pc",
                db_path=db_path,
                qbt_host="localhost",
                qbt_port=8080,
                max_pages=280,
                max_cycle_pages=4,
                sort_order="new",
            )

            # sort_order='new' now walks the newest year first, so the
            # current-year call is the first one.  Select it explicitly —
            # ``call_args`` only ever exposes the most recent call.
            calls_by_year = {c.kwargs["year"]: c.kwargs for c in mock_mc.scan_recent_games.call_args_list}
            assert scan_year in calls_by_year, f"Expected a scan call for {scan_year}, got: {sorted(calls_by_year)}"
            start_page = calls_by_year[scan_year]["start_page"]
            assert start_page == 5, f"Expected start_page=5 for {scan_year}, got {start_page}"

    def test_backlog_progress_logged_in_phase_5(self, tmp_path: Path) -> None:
        """Backlog progress line should appear at the end of a cycle."""
        from io import StringIO
        from unittest.mock import MagicMock, patch

        from loguru import logger

        from gamarr.database import Database
        from gamarr.pipeline import run_acquisition

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.set_last_scanned_page("pc", 2026, 12)
        db.close()

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            # Set _recent_games_last_page so DB set_last_scanned_page receives an int
            mock_mc._recent_games_last_page = 12
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            log_buffer = StringIO()
            handler_id = logger.add(log_buffer, level="INFO", format="{message}")

            run_acquisition(
                platform="pc",
                db_path=db_path,
                qbt_host="localhost",
                qbt_port=8080,
                max_pages=280,
                max_cycle_pages=4,
                sort_order="new",
                enabled=True,
            )

            logger.remove(handler_id)
            log_output = log_buffer.getvalue()

            assert "Progress:" in log_output, f"Expected 'Progress:' in log output, got: {log_output[:500]}"


class TestYearDrainOrder:
    """The per-cycle page allowance is spent on one year at a time, newest first."""

    @staticmethod
    def _run_cycle(
        tmp_path: Path,
        *,
        max_pages: int,
        max_cycle_pages: int,
        available_pages: dict[int, int],
        db_path: str | None = None,
        fetch_fails: bool = False,
        stalled_years: set[int] | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Run one acquisition cycle against a fake Metacritic client.

        ``available_pages`` maps a year to the highest page number that still
        returns games; a year absent from the map is exhausted at page 1.
        Returns the current year and the recorded scan calls.
        """
        import datetime

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        calls: list[dict[str, Any]] = []
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            start_page = kwargs.get("start_page", 1)
            limit = kwargs.get("max_pages", 0)
            scan_year = kwargs.get("year")
            last_available = available_pages.get(scan_year, 0) if isinstance(scan_year, int) else 0
            stalled = bool(stalled_years) and scan_year in (stalled_years or set())
            if stalled:
                last_page = start_page - 1
            elif limit and start_page + limit - 1 <= last_available:
                last_page = start_page + limit - 1
            elif start_page <= last_available:
                last_page = last_available
            else:
                last_page = start_page - 1
            calls.append(
                {
                    "year": scan_year,
                    "start_page": start_page,
                    "max_pages": limit,
                    "pages": max(last_page - start_page + 1, 0),
                    # The real client always fetches one page past the pages it
                    # credits: that probe is what proves the year is drained.
                    "requests": max(last_page - start_page + 1, 0) + 1,
                }
            )
            mock_mc._recent_games_last_page = last_page
            # A fetch failure is not a drained year: no empty page was parsed.
            # A stalled probe is neither drained nor failed.
            mock_mc._recent_games_exhausted = last_page < start_page and not fetch_fails and not stalled
            mock_mc._recent_games_failed = fetch_fails
            return []

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc.scan_recent_games.side_effect = fake_scan

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
        ):
            mock_source_cls.return_value = MagicMock()
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                platform="pc",
                db_path=db_path or str(tmp_path / "test.db"),
                qbt_host="localhost",
                qbt_port=8080,
                max_pages=max_pages,
                max_cycle_pages=max_cycle_pages,
                sort_order="new",
            )
        return current_year, calls

    def test_cycle_allowance_spent_on_newest_year_only(self, tmp_path: Path) -> None:
        """max_cycle_pages is a per-cycle total, not a per-year allowance."""
        import datetime

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        _current, calls = self._run_cycle(
            tmp_path,
            max_pages=1000,
            max_cycle_pages=4,
            available_pages={current_year: 500},
        )

        years = [call["year"] for call in calls]
        assert years == [current_year], f"Only the newest year should be drained first, got {years}"
        assert sum(call["pages"] for call in calls) == 4, (
            f"A cycle must credit at most max_cycle_pages pages, got {[c['pages'] for c in calls]}"
        )

    def test_exhausted_newest_year_descends_within_same_cycle(self, tmp_path: Path) -> None:
        """A drained year moves the scan on to the next newest year, descending."""
        import datetime

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        _current, calls = self._run_cycle(
            tmp_path,
            max_pages=1000,
            max_cycle_pages=4,
            available_pages={current_year - 1: 500},
        )

        years = [call["year"] for call in calls]
        assert years == [current_year, current_year - 1], f"Expected descending drain, got {years}"
        # The exhausted newest year only costs an empty probe; the real pages
        # fetched must still fit inside the cycle allowance.
        assert [call["pages"] for call in calls] == [0, 4], f"Unexpected page counts: {calls}"
        assert sum(call["pages"] for call in calls) == 4, f"Cycle allowance was exceeded: {calls}"

    def test_partially_drained_year_shares_remaining_allowance_with_next_year(self, tmp_path: Path) -> None:
        """Pages left in a drained year are spent on the next year down."""
        import datetime

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        _current, calls = self._run_cycle(
            tmp_path,
            max_pages=1000,
            max_cycle_pages=4,
            available_pages={current_year: 2, current_year - 1: 500},
        )

        assert [(call["year"], call["max_pages"]) for call in calls] == [
            (current_year, 4),
            (current_year - 1, 2),
        ], f"Expected the leftover allowance to be used on the next year, got {calls}"

    def test_exhausted_year_progress_does_not_creep(self, tmp_path: Path) -> None:
        """A year with no pages left must not advance its stored progress row."""
        import datetime

        from gamarr.database import Database

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        db_path = str(tmp_path / "progress.db")
        db = Database(db_path)
        db.set_last_scanned_page("pc", current_year, 12)
        db.close()

        _current, calls = self._run_cycle(
            tmp_path,
            max_pages=1000,
            max_cycle_pages=4,
            available_pages={current_year: 12, current_year - 1: 500},
            db_path=db_path,
        )
        assert calls[0]["year"] == current_year
        assert calls[0]["start_page"] == 13
        assert calls[0]["max_pages"] == 4

        db = Database(db_path)
        assert db.get_last_scanned_page("pc", current_year) == 12, (
            "An exhausted year must keep its last real page so the budget is not spent on empty probes"
        )
        db.close()

    def test_unconfirmed_zero_page_probe_does_not_block_drain_reset(self, tmp_path: Path) -> None:
        """A probe that is neither drained nor failed must not suppress the reset."""
        import datetime

        from gamarr.database import Database

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        db_path = str(tmp_path / "stalled.db")
        db = Database(db_path)
        db.set_last_scanned_page("pc", current_year, 12)
        db.set_last_scanned_page("pc", current_year - 2, 7)
        db.close()

        _current, calls = self._run_cycle(
            tmp_path,
            max_pages=1000,
            max_cycle_pages=4,
            available_pages={current_year: 12},
            stalled_years={current_year - 1},
            db_path=db_path,
        )
        probed = {call["year"] for call in calls}
        assert probed >= {current_year, current_year - 1}, f"Expected both years probed, got {probed}"

        db = Database(db_path)
        assert db.get_last_scanned_page("pc", current_year) == 0, (
            "An unconfirmed zero-page probe must not block the drained reset"
        )
        assert db.get_last_scanned_page("pc", current_year - 2) == 0, (
            "The drained reset must clear every year's progress row"
        )
        db.close()

    def test_fetch_failure_is_still_reported_as_a_scrape_failure(self, tmp_path: Path) -> None:
        """A failed browse fetch must not be silenced by another year being drained."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.metacritic_cache import MetacriticCache

        db_path = str(tmp_path / "mixed_health.db")

        class _FakeClient:
            """Real cache; the newest year fails to fetch, the next year is drained."""

            def __init__(self) -> None:
                self.sort_order = "new"
                self.cache_hits = 0
                self._cache = MetacriticCache(Database(db_path))
                self._recent_games_last_page = 0
                self._recent_games_exhausted = False
                self._recent_games_failed = False
                self._calls = 0

            def scan_recent_games(self, *_args: Any, **kwargs: Any) -> list:
                self._calls += 1
                start_page = kwargs.get("start_page", 1)
                # Flags are per call, exactly like the real client.
                self._recent_games_exhausted = False
                self._recent_games_failed = False
                if self._calls == 1:
                    # Only the newest year fails to fetch; every older year drains.
                    self._recent_games_last_page = 0
                    self._recent_games_exhausted = False
                    self._recent_games_failed = True
                    return []
                self._recent_games_last_page = start_page - 1
                self._recent_games_exhausted = True
                self._recent_games_failed = False
                return []

            def lookup_game(self, *_args: Any, **_kwargs: Any) -> None:
                return None

            def close(self) -> None:
                self._cache.close()

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient", return_value=_FakeClient()),
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken") as mock_health,
            patch("gamarr.pipeline._diagnose_and_notify_scrape") as mock_notify,
        ):
            mock_source_cls.return_value = MagicMock()
            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                platform="pc",
                db_path=db_path,
                qbt_host="localhost",
                qbt_port=8080,
                max_pages=200,
                max_cycle_pages=4,
                sort_order="new",
                notify_on_scrape_failure=True,
            )

        assert mock_health.called, "A real fetch failure must still trigger the scrape check"
        assert mock_notify.called, "A fetch failure must not be silenced by a drained year"

    def test_drained_range_does_not_report_a_scrape_failure(self, tmp_path: Path) -> None:
        """A confirmed-drained sweep (probes resumed past page 1) stays quiet."""
        import datetime
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.metacritic_cache import MetacriticCache

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        db_path = str(tmp_path / "drained_notify.db")
        # Every year in the window was scanned before, so the drain probes resume
        # past page 1: the only state the real client reports as drained.
        seed = Database(db_path)
        for offset in range(5):
            seed.set_last_scanned_page("pc", current_year - offset, 5)
        seed.close()

        class _FakeClient:
            """Real cache, drained scans - the state that used to look like a scrape failure."""

            def __init__(self) -> None:
                self.sort_order = "new"
                self.cache_hits = 0
                self._cache = MetacriticCache(Database(db_path))
                self._recent_games_last_page = 0
                self._recent_games_exhausted = False
                self._recent_games_failed = False

            def scan_recent_games(self, *_args: Any, **kwargs: Any) -> list:
                start_page = kwargs.get("start_page", 1)
                # Mirror the real client: flags are per call, and a drain is only
                # confirmed for a probe past page 1.
                self._recent_games_last_page = start_page - 1
                self._recent_games_exhausted = start_page > 1
                self._recent_games_failed = False
                return []

            def lookup_game(self, *_args: Any, **_kwargs: Any) -> None:
                return None

            def close(self) -> None:
                self._cache.close()

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient", return_value=_FakeClient()),
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
            patch("gamarr.pipeline._diagnose_and_notify_scrape") as mock_notify,
        ):
            mock_source_cls.return_value = MagicMock()
            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                platform="pc",
                db_path=db_path,
                qbt_host="localhost",
                qbt_port=8080,
                max_pages=200,
                max_cycle_pages=4,
                sort_order="new",
                notify_on_scrape_failure=True,
            )

        assert not mock_notify.called, "A drained sweep is expected to collect no games - that is not a scrape failure"

    def test_fully_drained_range_restarts_from_newest_year(self, tmp_path: Path) -> None:
        """A sweep that finds no pages anywhere must reset so new releases are seen."""
        import datetime

        from gamarr.database import Database

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        db_path = str(tmp_path / "drained.db")
        db = Database(db_path)
        db.set_last_scanned_page("pc", current_year, 12)
        db.set_last_scanned_page("pc", current_year - 1, 30)
        db.close()

        _current, calls = self._run_cycle(
            tmp_path,
            max_pages=1000,
            max_cycle_pages=4,
            available_pages={},
            db_path=db_path,
        )
        assert calls, "The sweep must still probe the year range"

        db = Database(db_path)
        assert db.get_last_scanned_page("pc", current_year) == 0, (
            "A fully drained range must reset so new releases get picked up"
        )
        assert db.get_last_scanned_page("pc", current_year - 1) == 0
        db.close()

    def test_failed_browse_fetch_does_not_reset_progress(self, tmp_path: Path) -> None:
        """A sweep that only failed to fetch must not be read as a drained range."""
        import datetime

        from gamarr.database import Database

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        db_path = str(tmp_path / "offline.db")
        db = Database(db_path)
        db.set_last_scanned_page("pc", current_year, 12)
        db.close()

        _current, calls = self._run_cycle(
            tmp_path,
            max_pages=1000,
            max_cycle_pages=4,
            available_pages={},
            db_path=db_path,
            fetch_fails=True,
        )
        assert calls, "The sweep must still attempt the year range"

        db = Database(db_path)
        assert db.get_last_scanned_page("pc", current_year) == 12, (
            "A failed fetch is not a drained year — progress must be preserved"
        )
        db.close()


class TestBacklogLatestMode:
    """Tests for unified browse loop budget and auto-reset behaviour."""

    def test_budget_exhausted_resets_and_continues(self, mocker: Any, tmp_path: Path) -> None:
        """When budget is exhausted, progress resets to page 1 and scanning continues."""
        from gamarr.pipeline import run_acquisition

        mock_db = mocker.patch("gamarr.pipeline.Database")
        mock_db_instance = mock_db.return_value
        mock_db_instance.sum_scanned_pages.return_value = 20
        mock_db_instance.has_verified_pending.return_value = False
        mock_db_instance.get_pending.return_value = []
        mock_db_instance.get_last_scanned_page.return_value = 0

        mock_qbt = mocker.patch("gamarr.pipeline.QBittorrentClient")
        mock_qbt_instance = mock_qbt.return_value
        mock_qbt_instance.is_connected.return_value = True

        mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient")
        mock_mc_instance = mock_mc.return_value
        mock_mc_instance.scan_recent_games.return_value = []

        run_acquisition(
            platform="pc",
            db_path=str(tmp_path / "test.db"),
            min_criticscore=75,
            min_criticscore_reviews=10,
            min_user_score=7.5,
            min_user_reviews=10,
            enabled=True,
            max_pages=20,
            max_cycle_pages=4,
        )
        # Budget exhausted -> reset_progress called, then scanning continues
        mock_db_instance.reset_progress.assert_called()
        mock_mc_instance.scan_recent_games.assert_called()

    def test_budget_exhausted_when_total_exceeds_max(self, mocker: Any, tmp_path: Path) -> None:
        """When total_scanned > max_pages, budget resets and scanning continues."""
        from gamarr.pipeline import run_acquisition

        mock_db = mocker.patch("gamarr.pipeline.Database")
        mock_db_instance = mock_db.return_value
        mock_db_instance.sum_scanned_pages.return_value = 24
        mock_db_instance.has_verified_pending.return_value = False
        mock_db_instance.get_pending.return_value = []
        mock_db_instance.get_last_scanned_page.return_value = 0

        mock_qbt = mocker.patch("gamarr.pipeline.QBittorrentClient")
        mock_qbt_instance = mock_qbt.return_value
        mock_qbt_instance.is_connected.return_value = True

        mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient")
        mock_mc_instance = mock_mc.return_value
        mock_mc_instance.scan_recent_games.return_value = []

        run_acquisition(
            platform="pc",
            db_path=str(tmp_path / "test.db"),
            min_criticscore=75,
            min_criticscore_reviews=10,
            min_user_score=7.5,
            min_user_reviews=10,
            enabled=True,
            max_pages=20,
            max_cycle_pages=4,
        )
        # Budget exceeded -> reset_progress called, then scanning continues
        mock_db_instance.reset_progress.assert_called()
        mock_mc_instance.scan_recent_games.assert_called()

    def test_metascore_to_criticscore_rename_triggers_progress_reset(self, mocker: Any, tmp_path: Path) -> None:
        """A legacy 'metascore' progress state must reset when config now says criticscore."""
        from gamarr.pipeline import run_acquisition

        mock_db = mocker.patch("gamarr.pipeline.Database")
        mock_db_instance = mock_db.return_value
        mock_db_instance.sum_scanned_pages.return_value = 0
        mock_db_instance.has_verified_pending.return_value = False
        mock_db_instance.get_pending.return_value = []
        mock_db_instance.get_last_scanned_page.return_value = 0
        mock_db_instance.get_last_sort_order.return_value = "metascore"  # legacy state

        mock_qbt = mocker.patch("gamarr.pipeline.QBittorrentClient")
        mock_qbt_instance = mock_qbt.return_value
        mock_qbt_instance.is_connected.return_value = True

        mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient")
        mock_mc_instance = mock_mc.return_value
        mock_mc_instance.scan_recent_games.return_value = []

        run_acquisition(
            platform="pc",
            db_path=str(tmp_path / "test.db"),
            enabled=True,
            sort_order="criticscore",
            max_pages=20,
            max_cycle_pages=4,
        )

        mock_db_instance.clear_browse_cache.assert_called_once()
        mock_db_instance.reset_progress.assert_called_once()

    def test_unified_loop_starts_from_progress_page(self, mocker: Any, tmp_path: Path) -> None:
        """Unified loop starts from the last scanned page + 1."""
        from gamarr.pipeline import run_acquisition

        mock_db = mocker.patch("gamarr.pipeline.Database")
        mock_db_instance = mock_db.return_value
        mock_db_instance.get_pending.return_value = []
        mock_db_instance.has_verified_pending.return_value = False
        mock_db_instance.get_last_scanned_page.return_value = 0
        mock_db_instance.sum_scanned_pages.return_value = 0

        mock_qbt = mocker.patch("gamarr.pipeline.QBittorrentClient")
        mock_qbt_instance = mock_qbt.return_value
        mock_qbt_instance.is_connected.return_value = True

        mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient")
        mock_mc_instance = mock_mc.return_value
        mock_mc_instance.scan_recent_games.return_value = []

        run_acquisition(
            platform="pc",
            db_path=str(tmp_path / "test.db"),
            min_criticscore=75,
            min_criticscore_reviews=10,
            min_user_score=7.5,
            min_user_reviews=10,
            enabled=True,
            max_pages=500,
            max_cycle_pages=4,
        )
        kwargs = mock_mc_instance.scan_recent_games.call_args[1]
        assert kwargs["start_page"] == 1
        assert kwargs["max_pages"] == 4

    def test_sort_order_not_overridden(self, mocker: Any) -> None:
        """Unified loop respects configured sort_order — no mode override."""

        from gamarr.pipeline import run_acquisition

        mock_db = mocker.patch("gamarr.pipeline.Database")
        mock_db_instance = mock_db.return_value
        mock_db_instance.get_pending.return_value = []
        mock_db_instance.has_verified_pending.return_value = False
        mock_db_instance.get_last_scanned_page.return_value = 0
        mock_db_instance.sum_scanned_pages.return_value = 0

        mock_qbt = mocker.patch("gamarr.pipeline.QBittorrentClient")
        mock_qbt_instance = mock_qbt.return_value
        mock_qbt_instance.is_connected.return_value = True

        mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient")
        mock_mc_instance = mock_mc.return_value
        mock_mc_instance.scan_recent_games.return_value = []

        run_acquisition(
            platform="pc",
            db_path=":memory:",
            min_criticscore=75,
            min_criticscore_reviews=10,
            min_user_score=7.5,
            min_user_reviews=10,
            enabled=True,
            max_pages=500,
            max_cycle_pages=4,
            sort_order="criticscore",
        )

        # The unified loop respects the configured sort_order — no override
        kwargs = mock_mc_instance.scan_recent_games.call_args[1]
        assert mock_mc_instance.sort_order == "criticscore", (
            f"Unified loop must set sort_order from config. Got sort_order={mock_mc_instance.sort_order}."
        )
        # sort_order="criticscore" means no year dimension — year=None
        assert kwargs.get("year") is None, (
            f"Critic sort should pass year=None (no year filter). Got year={kwargs.get('year')}."
        )

    def test_sort_order_userscore_not_overridden(self, mocker: Any) -> None:
        """Unified loop respects sort_order='userscore' like criticscore."""

        from gamarr.pipeline import run_acquisition

        mock_db = mocker.patch("gamarr.pipeline.Database")
        mock_db_instance = mock_db.return_value
        mock_db_instance.get_pending.return_value = []
        mock_db_instance.has_verified_pending.return_value = False
        mock_db_instance.get_last_scanned_page.return_value = 0
        mock_db_instance.sum_scanned_pages.return_value = 0
        mock_db_instance.get_last_sort_order.return_value = "userscore"

        mock_qbt = mocker.patch("gamarr.pipeline.QBittorrentClient")
        mock_qbt_instance = mock_qbt.return_value
        mock_qbt_instance.is_connected.return_value = True

        mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient")
        mock_mc_instance = mock_mc.return_value
        mock_mc_instance.scan_recent_games.return_value = []

        run_acquisition(
            platform="pc",
            db_path=":memory:",
            min_criticscore=75,
            min_criticscore_reviews=10,
            min_user_score=7.5,
            min_user_reviews=10,
            enabled=True,
            max_pages=500,
            max_cycle_pages=4,
            sort_order="userscore",
        )

        assert mock_mc_instance.sort_order == "userscore", (
            f"Unified loop must set sort_order from config. Got sort_order={mock_mc_instance.sort_order}."
        )
        kwargs = mock_mc_instance.scan_recent_games.call_args[1]
        assert kwargs.get("year") is None, (
            f"Userscore sort should pass year=None (no year filter). Got year={kwargs.get('year')}."
        )

    def test_backlog_shared_budget_respects_max_pages(self, mocker: Any, tmp_path: Path) -> None:
        """Backlog mode should cap total pages across all years, not per-year.

        With max_pages=5 on a fresh database (sum_scanned_pages=0), the year
        loop has 2 years. The old code gave each year an independent max_pages
        budget of 5 (total potential: 10). The fix shares the budget globally,
        so after the first year exhausts the 5-page budget with a single
        10-page scan, no more years are scanned.
        """
        from unittest.mock import MagicMock

        from gamarr.pipeline import run_acquisition

        mock_db = mocker.patch("gamarr.pipeline.Database")
        mock_db_instance = mock_db.return_value
        mock_db_instance.sum_scanned_pages.return_value = 0
        mock_db_instance.has_verified_pending.return_value = False
        mock_db_instance.get_pending.return_value = []
        mock_db_instance.get_last_scanned_page.return_value = 0

        mock_qbt = mocker.patch("gamarr.pipeline.QBittorrentClient")
        mock_qbt_instance = mock_qbt.return_value
        mock_qbt_instance.is_connected.return_value = True

        mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient")
        mock_mc_instance = mock_mc.return_value

        # Simulate scanning 10 pages per year call
        def _mock_scan(*args: Any, **kwargs: Any) -> list:
            mock_mc_instance._recent_games_last_page = 10
            return []

        scan_mock = MagicMock(side_effect=_mock_scan)
        mock_mc_instance.scan_recent_games = scan_mock

        # sort_order "new" and max_pages=5 → years_back = ceil(5/52) = 1 → 2 years
        run_acquisition(
            platform="pc",
            db_path=str(tmp_path / "test.db"),
            min_criticscore=75,
            min_criticscore_reviews=10,
            min_user_score=7.5,
            min_user_reviews=10,
            enabled=True,
            max_pages=5,
            max_cycle_pages=0,
        )
        # The shared budget should limit to 1 year (5 pages exhausted after first call)
        assert scan_mock.call_count == 1
        # First call should use the full initial budget (5 pages remaining)
        assert scan_mock.call_args[1]["max_pages"] == 5


def test_process_browse_games_backlog_mode(tmp_path: Path) -> None:
    """A candidate that passes detail verification is queued as verified."""
    from gamarr.database import Database
    from gamarr.pipeline import _process_browse_games

    db = Database(tmp_path / "test.db")
    browse_games = [
        {
            "title": "Backlog Game",
            "slug": "backlog-game",
            "score": 88,
            "user_rating": 8.5,
            "critic_review_count": 12,
            "user_review_count": 15,
            "release_date": None,
        }
    ]
    thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 10, "min_user_score": 7.5, "min_user_reviews": 10}
    count = _process_browse_games(
        browse_games,
        "pc",
        db,
        thresholds,
        max_queue_days=30,
        reject_title=None,
        mc=_passing_detail_client(),
    )
    assert count == 1
    rows = db.get_pending(platform="pc")
    assert len(rows) == 1
    assert rows[0].game_title == "Backlog Game"
    db.close()


def test_process_browse_games_reject_title(tmp_path: Path) -> None:
    """_process_browse_games skips games matching reject_title."""
    from gamarr.database import Database
    from gamarr.pipeline import _process_browse_games

    db = Database(tmp_path / "test.db")
    browse_games = [
        {
            "title": "Some Game Remake",
            "slug": "some-game-remake",
            "score": 88,
            "user_rating": 8.5,
            "critic_review_count": 12,
            "user_review_count": 15,
            "release_date": None,
        }
    ]
    thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 10, "min_user_score": 7.5, "min_user_reviews": 10}
    count = _process_browse_games(
        browse_games,
        "pc",
        db,
        thresholds,
        max_queue_days=30,
        reject_title=["Remake"],
    )
    assert count == 0
    assert len(db.get_pending(platform="pc")) == 0
    db.close()


def test_process_verify_result_genre_reject_none_genres(tmp_path: Path) -> None:
    """_process_verify_result handles None genres gracefully."""
    from unittest.mock import MagicMock

    from gamarr.database import Database
    from gamarr.pipeline import _process_verify_result

    db = Database(tmp_path / "test.db")
    db.record_pending(slug="no-genre", game_title="No Genre", platform="pc")
    game = db.get_pending()[0]
    thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10}

    # Simulate a ScoreResult with matching genre
    mock_result = MagicMock()
    mock_result.metascore = 80.0
    mock_result.metascore_review_count = 10
    mock_result.user_score = 8.0
    mock_result.user_review_count = 15
    mock_result.genres = None

    removed = _process_verify_result(
        db,
        game,
        mock_result,
        thresholds,
        reject_genre=["RPG"],
        reject_title=None,
    )
    # Should not remove (None genres don't match any reject_genre)
    # Game passes scores, so returns 1 (passed)
    assert removed == 1
    assert db.is_pending("no-genre")
    db.close()


def test_process_single_pending_match_no_match(tmp_path: Path) -> None:
    """_process_single_pending_match touches pending when no source match found."""
    from gamarr.database import Database
    from gamarr.pipeline import _process_single_pending_match

    db = Database(tmp_path / "test.db")
    db.record_pending(
        slug="no-match",
        game_title="No Match Game",
        platform="pc",
        metascore=85.0,
        user_score=8.5,
    )
    db.update_pending_scores(slug="no-match", metascore=85.0, user_score=8.5)

    result = _process_single_pending_match(
        db,
        mc=None,
        thresholds=None,
        qbt=None,
        magnet_fetcher=None,
        notifier=None,
        library=None,
        can_deliver=False,
        game_title="No Match Game",
        game_slug="no-match",
        game_platform="pc",
        game_metascore=85.0,
        game_metascore_reviews=None,
        game_user_score=8.5,
        game_user_reviews=None,
        game_release_date=None,
        source_name="fitgirl",
    )
    assert result is None, "No match should return None"
    assert db.is_pending("no-match"), "Game should stay pending"
    db.close()


def test_process_single_pending_match_dlc_deep_search_matches(tmp_path: Path) -> None:
    """Deep search of article body finds DLC name in repack features when title match fails.

    When a Metacritic pending game has a DLC/expansion title (e.g.
    "Dark Souls III: The Ringed City") and the FitGirl sitemap only has
    the base game title (URL slug-derived "Dark Souls Iii"), the normal
    title match fails. But the article body contains the DLC name in the
    repack features section, so deep search should find the match.
    """
    import datetime
    from unittest.mock import MagicMock, patch

    from gamarr.database import Database
    from gamarr.pipeline import _process_single_pending_match

    db = Database(str(tmp_path / "test.db"))
    expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
    db.record_pending(
        slug="dark-souls-iii-the-ringed-city",
        game_title="Dark Souls III: The Ringed City",
        platform="pc",
        metascore=85.0,
        user_score=8.0,
        expires_at=expires,
    )
    db.update_pending_scores(slug="dark-souls-iii-the-ringed-city", metascore=85.0, user_score=8.0)
    # Store a FitGirl sitemap entry for the BASE game (URL slug-derived title)
    db.rebuild_source_titles(
        "fitgirl",
        [{"title": "Dark Souls Iii", "url": "https://fitgirl-repacks.site/dark-souls-iii/"}],
    )

    # Article body contains repack features with the DLC name
    html_with_dlc = (
        "<html><head><title>Dark Souls III [FitGirl Repack]</title></head>"
        "<body><article>"
        "Repack Features"
        "Based on Dark.Souls.III.The.Ringed.City-CODEX ISO release: "
        "codex-dark.souls.iii.the.ringed.city.iso (26 GB)"
        '<a href="magnet:?xt=urn:btih:abc">magnet</a>'
        "</article></body></html>"
    )

    with patch("gamarr.pipeline.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = html_with_dlc
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = _process_single_pending_match(
            db,
            mc=None,
            thresholds=None,
            qbt=None,
            magnet_fetcher=None,
            notifier=None,
            library=None,
            can_deliver=False,
            game_title="Dark Souls III: The Ringed City",
            game_slug="dark-souls-iii-the-ringed-city",
            game_platform="pc",
            game_metascore=85.0,
            game_metascore_reviews=100,
            game_user_score=8.0,
            game_user_reviews=50,
            game_release_date=None,
            reject_keywords=None,
            source_name="fitgirl",
        )
        assert result is not None, "Should match via deep search in article body"
        assert result.get("result") is not None, "Match should produce a result"
        mock_get.assert_called_once()
        db.close()


def test_process_single_pending_match_dlc_deep_search_no_match_when_article_empty(tmp_path: Path) -> None:
    """Deep search should NOT match when article body does not contain the DLC name.

    Token overlap picks the candidate (>= 3 shared tokens), but if the article
    body doesn't confirm the match, the game should not be matched.
    """
    import datetime
    from unittest.mock import MagicMock, patch

    from gamarr.database import Database
    from gamarr.pipeline import _process_single_pending_match

    db = Database(str(tmp_path / "test.db"))
    expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
    db.record_pending(
        slug="mhw-2-curse-vampire",
        game_title="Total War: WARHAMMER II - Curse of the Vampire Coast",
        platform="pc",
        metascore=85.0,
        user_score=8.0,
        expires_at=expires,
    )
    db.update_pending_scores(slug="mhw-2-curse-vampire", metascore=85.0, user_score=8.0)
    db.rebuild_source_titles(
        "fitgirl",
        [{"title": "Total War Warhammer", "url": "https://fitgirl-repacks.site/total-war-warhammer/"}],
    )

    # Article body does NOT contain the expansion name
    html_other = (
        "<html><head><title>Total War Warhammer [FitGirl Repack]</title></head>"
        "<body><article>"
        "Repack Features"
        "Based on something else entirely"
        '<a href="magnet:?xt=urn:btih:abc">magnet</a>'
        "</article></body></html>"
    )

    with patch("gamarr.pipeline.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = html_other
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = _process_single_pending_match(
            db,
            mc=None,
            thresholds=None,
            qbt=None,
            magnet_fetcher=None,
            notifier=None,
            library=None,
            can_deliver=False,
            game_title="Total War: WARHAMMER II - Curse of the Vampire Coast",
            game_slug="mhw-2-curse-vampire",
            game_platform="pc",
            game_metascore=85.0,
            game_metascore_reviews=None,
            game_user_score=8.0,
            game_user_reviews=None,
            game_release_date=None,
            reject_keywords=None,
            source_name="fitgirl",
        )
        assert result is None, "Should NOT match when article doesn't contain DLC name"
        assert db.is_pending("mhw-2-curse-vampire"), "Game should stay pending"
        mock_get.assert_called_once()
        db.close()


def test_jit_verify_and_update_metacritic_unavailable(tmp_path: Path) -> None:
    """_jit_verify_and_update returns None when Metacritic is unavailable."""
    from unittest.mock import MagicMock

    from gamarr.database import Database
    from gamarr.pipeline import _jit_verify_and_update

    db = Database(tmp_path / "test.db")
    db.record_pending(
        slug="jit-down",
        game_title="JIT Down",
        platform="pc",
        metascore=85.0,
        user_score=8.5,
    )
    db.update_pending_scores(slug="jit-down", metascore=85.0, user_score=8.5)

    mock_mc = MagicMock()
    mock_mc.lookup_game.return_value = None

    result = _jit_verify_and_update(
        db,
        mock_mc,
        {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10},
        game_title="JIT Down",
        game_slug="jit-down",
        game_platform="pc",
    )
    assert result is None, "Should return None when MC unavailable"
    assert db.is_pending("jit-down"), "Game should stay pending"
    db.close()


def test_jit_verify_and_update_passes(tmp_path: Path) -> None:
    """_jit_verify_and_update returns scores when game passes thresholds."""
    from unittest.mock import MagicMock

    from gamarr.database import Database
    from gamarr.pipeline import _jit_verify_and_update

    db = Database(tmp_path / "test.db")
    db.record_pending(
        slug="jit-pass",
        game_title="JIT Pass",
        platform="pc",
        metascore=90.0,
        user_score=9.0,
    )

    mock_result = MagicMock()
    mock_result.metascore = 92.0
    mock_result.metascore_review_count = 20
    mock_result.user_score = 9.2
    mock_result.user_review_count = 30
    mock_result.genres = ["Action"]
    mock_result.must_play = True
    mock_result.description = "Great game"

    mock_mc = MagicMock()
    mock_mc.lookup_game.return_value = mock_result

    result = _jit_verify_and_update(
        db,
        mock_mc,
        {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10},
        game_title="JIT Pass",
        game_slug="jit-pass",
        game_platform="pc",
    )
    assert result is not None
    assert result[0] == 92.0  # metascore
    assert result[1] == 9.2  # user_score
    assert result[2] == 20  # metascore_reviews
    assert result[3] == 30  # user_reviews
    db.close()


def test_jit_verify_and_update_failing_scores(tmp_path: Path) -> None:
    """_jit_verify_and_update returns None when scores fail thresholds."""
    from unittest.mock import MagicMock

    from gamarr.database import Database
    from gamarr.pipeline import _jit_verify_and_update

    db = Database(tmp_path / "test.db")
    db.record_pending(
        slug="jit-fail",
        game_title="JIT Fail",
        platform="pc",
        metascore=60.0,
        user_score=5.0,
    )

    mock_result = MagicMock()
    mock_result.metascore = 60.0
    mock_result.metascore_review_count = 3
    mock_result.user_score = 5.0
    mock_result.user_review_count = 2

    mock_mc = MagicMock()
    mock_mc.lookup_game.return_value = mock_result

    thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10}
    result = _jit_verify_and_update(
        db,
        mock_mc,
        thresholds,
        game_title="JIT Fail",
        game_slug="jit-fail",
        game_platform="pc",
    )
    assert result is None, "Should return None when scores fail"
    # Game should be removed from pending (failing scores)
    assert not db.is_pending("jit-fail"), "Failing game should be removed from pending"
    db.close()


def test_jit_verify_and_update_no_mc_returns_empty_tuple(tmp_path: Path) -> None:
    """_jit_verify_and_update returns () when mc or thresholds is None (skip verification)."""
    from unittest.mock import MagicMock

    from gamarr.database import Database
    from gamarr.pipeline import _jit_verify_and_update

    db = Database(tmp_path / "test.db")
    result = _jit_verify_and_update(
        db,
        mc=None,
        thresholds={"min_criticscore": 75},
        game_title="Skip",
        game_slug="skip",
        game_platform="pc",
    )
    assert result == (), "Should return empty tuple when mc is None"

    result2 = _jit_verify_and_update(
        db,
        mc=MagicMock(),
        thresholds=None,
        game_title="Skip",
        game_slug="skip2",
        game_platform="pc",
    )
    assert result2 == (), "Should return empty tuple when thresholds is None"
    db.close()


def test_jit_verify_and_update_failing_scores_latest_mode(tmp_path: Path) -> None:
    """_jit_verify_and_update removes from latest table when scores fail."""
    from unittest.mock import MagicMock

    from gamarr.database import Database
    from gamarr.pipeline import _jit_verify_and_update

    db = Database(tmp_path / "test.db")
    db.record_pending(slug="jit-lt-fail", game_title="JIT LT Fail", platform="pc")

    mock_result = MagicMock()
    mock_result.metascore = 60.0
    mock_result.metascore_review_count = 3
    mock_result.user_score = 5.0
    mock_result.user_review_count = 2

    mock_mc = MagicMock()
    mock_mc.lookup_game.return_value = mock_result

    thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10}
    result = _jit_verify_and_update(
        db,
        mock_mc,
        thresholds,
        game_title="JIT LT Fail",
        game_slug="jit-lt-fail",
        game_platform="pc",
    )
    assert result is None
    assert not db.is_pending("jit-lt-fail")
    db.close()


def test_jit_verify_and_update_passes_latest_mode(tmp_path: Path) -> None:
    """_jit_verify_and_update updates latest table scores on pass."""
    from unittest.mock import MagicMock

    from gamarr.database import Database
    from gamarr.pipeline import _jit_verify_and_update

    db = Database(tmp_path / "test.db")
    db.record_pending(slug="jit-lt-pass", game_title="JIT LT Pass", platform="pc")

    mock_result = MagicMock()
    mock_result.metascore = 92.0
    mock_result.metascore_review_count = 20
    mock_result.user_score = 9.2
    mock_result.user_review_count = 30

    mock_mc = MagicMock()
    mock_mc.lookup_game.return_value = mock_result

    thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10}
    result = _jit_verify_and_update(
        db,
        mock_mc,
        thresholds,
        game_title="JIT LT Pass",
        game_slug="jit-lt-pass",
        game_platform="pc",
    )
    assert result is not None
    assert result[0] == 92.0


def test_process_single_pending_match_iterates_through_rejected_matches(tmp_path: Path) -> None:
    """When the first match is rejected by reject_keywords, the function iterates
    to the next match. If a subsequent match passes, it should be used.

    Real scenario: Triangle Strategy has two FitGirl repack URLs. One mentions
    "switch" in the article body (rejected), the other is clean.
    """
    import datetime
    from unittest.mock import MagicMock, patch

    from gamarr.database import Database
    from gamarr.pipeline import _process_single_pending_match

    db = Database(str(tmp_path / "test.db"))
    expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
    db.record_pending(
        slug="triangle-strategy",
        game_title="Triangle Strategy",
        platform="pc",
        metascore=85.0,
        user_score=8.0,
        expires_at=expires,
    )
    db.update_pending_scores(slug="triangle-strategy", metascore=85.0, user_score=8.0)

    # Two matching FitGirl source entries with different titles.
    # "Triangle Strategy" gets score 2 (exact match) and sorts first.
    # "Triangle Strategy Pc" gets score 1 (substring match) and sorts second.
    db.rebuild_source_titles(
        "fitgirl",
        [
            {"title": "Triangle Strategy", "url": "https://fitgirl-repacks.site/triangle-strategy/"},
            {"title": "Triangle Strategy Pc", "url": "https://fitgirl-repacks.site/triangle-strategy-pc/"},
        ],
    )

    # First URL's article body contains "switch" (rejected keyword)
    html_with_switch = (
        "<html><head><title>Triangle Strategy [FitGirl Repack]</title></head>"
        "<body><article>"
        "Repack Features"
        "Based on Triangle.Strategy.Switch-CODEX ISO release"
        '<a href="magnet:?xt=urn:btih:abc">magnet</a>'
        "</article></body></html>"
    )
    # Second URL's article body is clean
    html_clean = (
        "<html><head><title>Triangle Strategy PC [FitGirl Repack]</title></head>"
        "<body><article>"
        "Repack Features"
        "PC version only no console references"
        '<a href="magnet:?xt=urn:btih:def">magnet</a>'
        "</article></body></html>"
    )

    url_responses = {
        "https://fitgirl-repacks.site/triangle-strategy/": html_with_switch,
        "https://fitgirl-repacks.site/triangle-strategy-pc/": html_clean,
    }

    def mock_get(url: str, *args: object, **kwargs: object) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.text = url_responses.get(url, html_clean)
        mock_resp.raise_for_status.return_value = None
        return mock_resp

    with patch("gamarr.pipeline.requests.get", side_effect=mock_get):
        result = _process_single_pending_match(
            db,
            mc=None,
            thresholds=None,
            qbt=None,
            magnet_fetcher=None,
            notifier=None,
            library=None,
            can_deliver=False,
            game_title="Triangle Strategy",
            game_slug="triangle-strategy",
            game_platform="pc",
            game_metascore=85.0,
            game_metascore_reviews=100,
            game_user_score=8.0,
            game_user_reviews=50,
            game_release_date=None,
            reject_keywords=["switch"],
            source_name="fitgirl",
        )
        assert result is not None, "Should match the second FitGirl URL (clean, no reject keyword)"
        assert result.get("result") == "Passed", "Match should be recorded as Passed"
        assert not db.is_pending("triangle-strategy"), "Game should be removed from pending"
        assert "triangle-strategy-pc" in result.get("result_details", ""), (
            f"Should use the PC URL, got details: {result.get('result_details')}"
        )
        db.close()


class TestRecordResultGenres:
    """Tests that _record_result passes genres to record_processed."""

    def test_record_result_passes_genres(self, tmp_path: Path) -> None:

        from gamarr.database import Database
        from gamarr.pipeline import _record_result

        db = Database(str(tmp_path / "test.db"))
        with patch.object(db, "record_processed") as mock_record:
            _record_result(
                db,
                source="fitgirl",
                source_title="Test Game",
                source_url="mc:test-game",
                game_title="Test Game",
                platform="pc",
                result="Passed",
                torrent_tag="gamarr-test",
                genres="Action, RPG",
            )
            mock_record.assert_called_once()
            call_kwargs = mock_record.call_args.kwargs
            assert call_kwargs.get("genres") == "Action, RPG"

    def test_record_result_passes_none_genres(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _record_result

        db = Database(str(tmp_path / "test.db"))
        with patch.object(db, "record_processed") as mock_record:
            _record_result(
                db,
                source="fitgirl",
                source_title="Test Game",
                source_url="mc:test-game",
                game_title="Test Game",
                platform="pc",
                result="Passed",
                torrent_tag="gamarr-test",
                genres=None,
            )
            call_kwargs = mock_record.call_args.kwargs
            assert call_kwargs.get("genres") is None


# ---------------------------------------------------------------------------
# Bug 1: _process_browse_games overcounts when duplicate slugs exist in batch
# ---------------------------------------------------------------------------


def test_process_browse_games_indefinite_max_queue_days(tmp_path: Path) -> None:
    """_process_browse_games with max_queue_days=0 uses indefinite expiry."""
    from gamarr.database import Database
    from gamarr.pipeline import _process_browse_games

    db = Database(tmp_path / "test.db")
    browse_games = [
        {
            "title": "Indefinite Game",
            "slug": "indefinite-game",
            "score": 90,
            "user_rating": 9.0,
            "critic_review_count": 15,
            "user_review_count": 20,
            "release_date": None,
        }
    ]
    thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 10, "min_user_score": 7.5, "min_user_reviews": 10}
    count = _process_browse_games(
        browse_games,
        "pc",
        db,
        thresholds,
        max_queue_days=0,
        reject_title=None,
        mc=_passing_detail_client(),
    )
    assert count == 1
    rows = db.get_pending(platform="pc")
    assert len(rows) == 1
    # Indefinite expiry should be far in the future
    import datetime

    assert rows[0].expires_at > (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=365)).isoformat()
    db.close()


def test_process_browse_games_handles_duplicate_slugs(tmp_path: Path) -> None:
    """_process_browse_games returns actual unique games added, not attempted.

    When the same game appears on multiple browse pages (duplicate slug),
    _process_browse_games should count it only once — not once per occurrence.
    """
    from gamarr.database import Database
    from gamarr.pipeline import _process_browse_games

    db = Database(tmp_path / "test.db")
    browse_games = [
        {
            "title": "Awesome Game",
            "slug": "awesome-game",
            "score": 90,
            "user_rating": 9.0,
            "critic_review_count": 15,
            "user_review_count": 20,
            "release_date": None,
        },
        {
            "title": "Awesome Game",
            "slug": "awesome-game",  # DUPLICATE — same game on page 2
            "score": 90,
            "user_rating": 9.0,
            "critic_review_count": 15,
            "user_review_count": 20,
            "release_date": None,
        },
    ]
    thresholds = {"min_criticscore": 75, "min_criticscore_reviews": 10, "min_user_score": 7.5, "min_user_reviews": 10}
    count = _process_browse_games(
        browse_games,
        "pc",
        db,
        thresholds,
        max_queue_days=30,
        reject_title=None,
        mc=_passing_detail_client(),
    )
    assert count == 1, f"Expected 1 unique game added, got {count}"
    rows = db.get_pending(platform="pc")
    assert len(rows) == 1, "Should have 1 game in pending queue"
    db.close()


# ---------------------------------------------------------------------------
# Bug 2: No log when matching phase is skipped (no verified games)
# ---------------------------------------------------------------------------


class TestRunAcquisitionNoVerifiedLogging:
    """Tests that run_acquisition logs when no verified games are available."""

    def test_logs_when_no_verified_pending_skips_matching(self, tmp_path: Path) -> None:
        """run_acquisition emits a clear log when matching is skipped.

        When has_verified_pending returns False (no games passed score
        thresholds), the matching phase is skipped.  The absence of this log
        currently leaves users confused about why 0 games matched.
        """
        from gamarr.pipeline import run_acquisition

        # Use an in-memory DB and block the browse step so no games are
        # discovered.  With no verified games, matching should be explicitly
        # skipped with a log message — not silently.
        db_path = str(tmp_path / "test.db")

        with (
            patch("gamarr.pipeline.logger") as mock_logger,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_mc = MagicMock()
            mock_mc.fetch_browse_pages.return_value = []
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_torrent_present.return_value = False
            mock_qbt.adopt_present_torrent.return_value = None
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                db_path=db_path,
                platform="pc",
                max_pages=0,
                enabled=True,
            )

        # After run_acquisition, verify an INFO log was emitted about
        # skipping matching because no verified games are available.
        info_messages = []
        for call_args in mock_logger.info.call_args_list:
            if call_args[0]:
                info_messages.append(str(call_args[0][0]))
        combined = " ".join(info_messages)
        assert "no verified" in combined.lower() or "skip" in combined.lower(), (
            f"Expected a log about no verified games / skipping matching, got: {info_messages}"
        )


# ---------------------------------------------------------------------------
# Bug 3: "Score check: 88 verified" is misleading — doesn't show passed vs failed
# ---------------------------------------------------------------------------


class TestScoreCheckSummary:
    """Tests that the score verification summary distinguishes passed vs failed."""

    def test_batch_summary_breaks_down_passed_and_failed(self) -> None:
        """_log_batch_summary includes counts of games that passed vs failed scores.

        Currently the log says "88 verified" which means "88 looked up" —
        not "88 passed".  Users assume all 88 passed and are confused when
        0 match.  The summary should distinguish.
        """
        from gamarr.pipeline import _log_batch_summary

        # Simulate: 88 games checked, 0 passed scores, 60 kept (below thresholds),
        # 28 removed by genre
        with patch("gamarr.pipeline.logger") as mock_logger:
            _log_batch_summary(checked=88, cache_hits=0, passed=0, removed=28, kept_failed=60)

        info_calls = [
            call_args[0][0] if call_args[0] else str(call_args) for call_args in mock_logger.info.call_args_list
        ]
        combined = " ".join(str(c) for c in info_calls)
        # Should mention how many passed vs failed, not just "verified"
        assert "passed" in combined.lower(), f"Expected 'passed' count in summary, got: {info_calls}"


class TestPendingProcessingOrder:
    """Processing and discovery follow the configured ``sort_order``.

    Regression: the browse phase honoured ``sort_order`` but the pending
    queue (verification, matching, delivery) did not, so switching from
    ``criticscore`` to ``new`` delivered carryover games in an arbitrary
    database order.  Discovery also walked year buckets oldest-first.
    """

    _EXPIRES = "2099-01-01T00:00:00+00:00"

    @staticmethod
    def _seed(db: Any, *, slug: str, title: str, release_date: str, metascore: float, user_score: float) -> None:
        from gamarr.database import Database as _Database

        assert isinstance(db, _Database)
        db.record_pending(
            slug=slug,
            game_title=title,
            platform="pc",
            metascore=metascore,
            user_score=user_score,
            release_date=release_date,
            expires_at=TestPendingProcessingOrder._EXPIRES,
        )
        db.update_pending_scores(slug=slug, metascore=metascore, user_score=user_score)

    @staticmethod
    def _seed_source_titles(db: Any, titles: list[str]) -> None:
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": t, "url": f"https://fitgirl-repacks.site/{t.casefold().replace(' ', '-')}/"} for t in titles],
        )

    def test_match_delivers_newest_release_first_for_new_sort_order(self, tmp_path: Path) -> None:
        """sort_order='new' delivers the most recently released game first."""
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        self._seed(
            db,
            slug="brigandine-the-legend-of-runersia",
            title="Brigandine: The Legend of Runersia",
            release_date="2022-05-10",
            metascore=76.0,
            user_score=8.3,
        )
        self._seed(
            db,
            slug="hack-gu-last-recode",
            title=".hack//G.U. Last Recode",
            release_date="2017-11-03",
            metascore=76.0,
            user_score=8.0,
        )
        self._seed(
            db,
            slug="crash-bandicoot-n-sane-trilogy",
            title="Crash Bandicoot N. Sane Trilogy",
            release_date="2018-06-29",
            metascore=80.0,
            user_score=8.3,
        )
        self._seed_source_titles(
            db,
            [
                "Brigandine: The Legend of Runersia",
                ".hack//G.U. Last Recode",
                "Crash Bandicoot N. Sane Trilogy",
            ],
        )

        mock_qbt = MagicMock()
        mock_qbt.is_torrent_present.return_value = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            sort_order="new",
        )

        assert [m["slug"] for m in matched] == [
            "brigandine-the-legend-of-runersia",
            "crash-bandicoot-n-sane-trilogy",
            "hack-gu-last-recode",
        ], f"Expected newest-release-first delivery, got: {[m['slug'] for m in matched]}"
        db.close()

    def test_run_acquisition_threads_sort_order_to_processing(self, mocker: Any, tmp_path: Path) -> None:
        """run_acquisition must pass the configured order to verify and match."""
        from gamarr.pipeline import run_acquisition

        mock_db = mocker.patch("gamarr.pipeline.Database")
        db = mock_db.return_value
        db.get_last_sort_order.return_value = "userscore"
        db.get_last_scanned_page.return_value = 0
        db.sum_scanned_pages.return_value = 0
        db.get_pending.return_value = [MagicMock()]
        db.has_verified_pending.return_value = True

        mock_qbt = mocker.patch("gamarr.pipeline.QBittorrentClient")
        mock_qbt.return_value.is_connected.return_value = True
        mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient")
        mock_mc.return_value.scan_recent_games.return_value = []
        mock_mc.return_value._recent_games_last_page = 0
        mocker.patch("gamarr.pipeline.FitGirlSource")
        verify = mocker.patch("gamarr.pipeline._verify_pending_scores", return_value=0)
        match = mocker.patch("gamarr.pipeline._match_pending_games", return_value=[])

        run_acquisition(
            platform="pc",
            db_path=str(tmp_path / "test.db"),
            enabled=True,
            sort_order="userscore",
            max_pages=10,
            max_cycle_pages=1,
        )

        assert verify.call_args.kwargs["sort_order"] == "userscore"
        assert match.call_args.kwargs["sort_order"] == "userscore"

    def test_new_sort_order_scans_newest_year_first(self, mocker: Any, tmp_path: Path) -> None:
        """The cycle allowance is spent on the newest year before any older year."""
        import datetime

        from gamarr.pipeline import run_acquisition

        mock_db = mocker.patch("gamarr.pipeline.Database")
        mock_db_instance = mock_db.return_value
        mock_db_instance.sum_scanned_pages.return_value = 0
        mock_db_instance.has_verified_pending.return_value = False
        mock_db_instance.get_pending.return_value = []
        mock_db_instance.get_last_scanned_page.return_value = 0

        mock_qbt = mocker.patch("gamarr.pipeline.QBittorrentClient")
        mock_qbt.return_value.is_connected.return_value = True

        mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient")
        mock_mc_instance = mock_mc.return_value

        def _mock_scan(*args: Any, **kwargs: Any) -> list:
            # Report one page scanned so the newest year consumes the allowance.
            mock_mc_instance._recent_games_last_page = kwargs["start_page"]
            mock_mc_instance._recent_games_exhausted = False
            return []

        mock_mc_instance.scan_recent_games = MagicMock(side_effect=_mock_scan)

        # max_pages=200 -> years_back = ceil(200/52) = 4 -> five year buckets.
        run_acquisition(
            platform="pc",
            db_path=str(tmp_path / "test.db"),
            enabled=True,
            sort_order="new",
            max_pages=200,
            max_cycle_pages=1,
        )

        anchor = datetime.datetime.now(tz=datetime.UTC).year
        years = [c.kwargs["year"] for c in mock_mc_instance.scan_recent_games.call_args_list]

        assert years == [anchor], f"Expected the newest year to be drained first, got: {years}"

    def test_new_sort_order_descends_through_drained_years(self, mocker: Any, tmp_path: Path) -> None:
        """Drained year buckets are walked from the current year backwards."""
        import datetime

        from gamarr.pipeline import run_acquisition

        mock_db = mocker.patch("gamarr.pipeline.Database")
        mock_db_instance = mock_db.return_value
        mock_db_instance.sum_scanned_pages.return_value = 0
        mock_db_instance.has_verified_pending.return_value = False
        mock_db_instance.get_pending.return_value = []
        mock_db_instance.get_last_scanned_page.return_value = 0

        mock_qbt = mocker.patch("gamarr.pipeline.QBittorrentClient")
        mock_qbt.return_value.is_connected.return_value = True

        mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient")
        mock_mc_instance = mock_mc.return_value

        def _mock_scan(*args: Any, **kwargs: Any) -> list:
            # Every year is already drained: an empty page was parsed.
            mock_mc_instance._recent_games_last_page = kwargs["start_page"] - 1
            mock_mc_instance._recent_games_exhausted = True
            return []

        mock_mc_instance.scan_recent_games = MagicMock(side_effect=_mock_scan)

        run_acquisition(
            platform="pc",
            db_path=str(tmp_path / "test.db"),
            enabled=True,
            sort_order="new",
            max_pages=200,
            max_cycle_pages=1,
        )

        anchor = datetime.datetime.now(tz=datetime.UTC).year
        years = [c.kwargs["year"] for c in mock_mc_instance.scan_recent_games.call_args_list]

        assert years == [anchor - offset for offset in range(5)], (
            f"Expected newest-year-first traversal starting at {anchor}, got: {years}"
        )


class TestDateModeResolution:
    """max_date switches sort_order "new" to date mode; score modes ignore it."""

    @staticmethod
    def _run(
        *,
        tmp_path: Path,
        max_date: int = 0,
        max_cycle_months: int = 0,
        sort_order: Literal["new", "criticscore", "userscore"] = "new",
        max_pages: int = 500,
        max_cycle_pages: int = 0,
        scan_games: list[dict[str, Any]] | None = None,
        cutoff_reached: bool = False,
        seed_pages: dict[int, int] | None = None,
        db_path: str | None = None,
        cache_hours: int = 6,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Run one cycle and return (scan calls, log lines)."""
        from loguru import logger as loguru_logger

        calls: list[dict[str, Any]] = []
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            # Record the client's sort_order too: latest mode sets it as an
            # attribute, so a bare kwargs capture cannot see the coercion.
            calls.append(dict(kwargs))
            mock_mc._recent_games_last_page = kwargs.get("start_page", 1)
            mock_mc._recent_games_exhausted = False
            mock_mc._recent_games_failed = False
            mock_mc._recent_games_cutoff_reached = cutoff_reached
            return scan_games or []

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc.scan_recent_games.side_effect = fake_scan

        path = db_path or str(tmp_path / "date-mode.db")
        if seed_pages:
            from gamarr.database import Database

            seed_db = Database(path)
            for year, page in seed_pages.items():
                seed_db.set_last_scanned_page("pc", year, page)
            seed_db.close()

        logs: list[str] = []
        sink_id = loguru_logger.add(lambda msg: logs.append(str(msg)), level="INFO", format="{message}")
        try:
            with (
                patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
                patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
                patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
            ):
                mock_mc_cls.return_value = mock_mc
                mock_qbt = MagicMock()
                mock_qbt.is_connected.return_value = True
                mock_qbt_cls.return_value = mock_qbt
                run_acquisition(
                    platform="pc",
                    db_path=path,
                    qbt_host="localhost",
                    qbt_port=8080,
                    max_pages=max_pages,
                    max_cycle_pages=max_cycle_pages,
                    max_date=max_date,
                    max_cycle_months=max_cycle_months,
                    cache_hours=cache_hours,
                    sort_order=sort_order,
                )
        finally:
            loguru_logger.remove(sink_id)
        return calls, logs

    def test_date_mode_passes_floor_cutoff_and_no_page_limit(self, tmp_path: Path) -> None:
        """Date mode bounds the scan by the floor date, not a page count."""
        calls, _logs = self._run(tmp_path=tmp_path, max_date=202401, max_pages=500)
        assert calls, "Expected at least one browse call"
        for call in calls:
            assert call["cutoff_date"] == "2024-01-01"
            assert call["max_pages"] == 0
            assert isinstance(call["year"], int) and call["year"] >= 2024

    def test_date_mode_year_range_starts_at_floor_year(self, tmp_path: Path) -> None:
        """The sweep never walks older than the floor year."""
        calls, _logs = self._run(tmp_path=tmp_path, max_date=202401)
        years = [c["year"] for c in calls]
        assert min(years) == 2024

    def test_score_mode_ignores_max_date(self, tmp_path: Path) -> None:
        """criticscore has no year dimension, so the date keys are inert."""
        calls, logs = self._run(tmp_path=tmp_path, max_date=202401, sort_order="criticscore", max_pages=40)
        assert calls
        for call in calls:
            assert call["cutoff_date"] is None
            assert call["year"] is None
            assert call["max_pages"] == 40
        assert any("max_date/max_cycle_months ignored" in line for line in logs)

    def test_date_mode_logs_ignored_page_keys(self, tmp_path: Path) -> None:
        """Page keys set alongside max_date are reported as ignored."""
        _calls, logs = self._run(
            tmp_path=tmp_path,
            max_date=202401,
            max_pages=500,
            max_cycle_pages=4,
        )
        assert any("max_pages (500) is unused in date mode" in line for line in logs), logs
        assert any("max_cycle_pages ignored in date mode" in line for line in logs)

    def test_date_mode_passes_month_span(self, tmp_path: Path) -> None:
        """The month allowance reaches the client as max_month_span."""
        calls, _logs = self._run(tmp_path=tmp_path, max_date=202401, max_cycle_months=2)
        assert calls[0]["max_month_span"] == 2

    def test_no_month_span_when_pacing_disabled(self, tmp_path: Path) -> None:
        """max_cycle_months 0 sweeps to the floor with no span and no pace cutoff.

        The scan returns a game so the later-call branch is actually exercised:
        with pacing off, every call must still see only the floor cutoff.
        """
        calls, _logs = self._run(
            tmp_path=tmp_path,
            max_date=202401,
            max_cycle_months=0,
            scan_games=[{"title": "G", "slug": "g", "release_date": "2025-11-05"}],
        )
        assert len(calls) >= 2, f"Expected a second year call, got {len(calls)}"
        for call in calls:
            assert call["max_month_span"] == 0
            assert call["cutoff_date"] == "2024-01-01"

    def test_date_mode_skips_budget_reset(self, tmp_path: Path) -> None:
        """The page budget and its reset branch must not run in date mode."""
        import datetime

        year = datetime.datetime.now(tz=datetime.UTC).year
        _calls, logs = self._run(
            tmp_path=tmp_path,
            max_date=202401,
            max_pages=2,
            seed_pages={year: 500},
        )
        assert not any("Budget exhausted" in line for line in logs)

    @staticmethod
    def _run_floor_cycle(
        *,
        path: str,
        floor_month: int,
        floor_games: list[dict[str, Any]],
        stop_cutoff: str | None = None,
        seed: dict[int, int] | None = None,
        max_cycle_months: int = 0,
        drain_newer: bool = False,
    ) -> None:
        """Run one cycle whose newer years are consumed and whose floor year ends it.

        ``stop_cutoff``, when given, is the effective cutoff the fake client
        reports as the cause of a date stop.  When omitted, the cutoff is derived
        the way the real client does it (the cutoff it was given, or its own span
        cutoff when a month span is in force).
        """
        floor_year = floor_month // 100
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            scan_year = kwargs.get("year")
            start_page = kwargs.get("start_page", 1)
            mock_mc._recent_games_failed = False
            if scan_year == floor_year:
                # Model the real client: it reports the effective cutoff it was
                # given, or its own span cutoff when a month span is in force.
                span = kwargs.get("max_month_span", 0)
                if span and floor_games:
                    anchor = max(month_of(game.get("release_date")) or 0 for game in floor_games)
                    span_cutoff = year_month_to_date(subtract_months(anchor, span))
                    given = kwargs.get("cutoff_date")
                    effective = span_cutoff if given is None or span_cutoff > given else given
                else:
                    effective = kwargs.get("cutoff_date")
                if stop_cutoff is not None:
                    effective = stop_cutoff
                    reached = True
                else:
                    reached = effective is not None and bool(floor_games)
                mock_mc._recent_games_cutoff_reached = reached
                mock_mc._recent_games_stop_cutoff = effective if reached else None
                if floor_games:
                    mock_mc._recent_games_exhausted = False
                    mock_mc._recent_games_last_page = start_page
                    return floor_games
                mock_mc._recent_games_exhausted = True
                mock_mc._recent_games_last_page = start_page - 1
                return []
            if drain_newer:
                mock_mc._recent_games_cutoff_reached = False
                mock_mc._recent_games_stop_cutoff = None
                mock_mc._recent_games_exhausted = True
                mock_mc._recent_games_last_page = start_page - 1
                return []
            mock_mc._recent_games_cutoff_reached = False
            mock_mc._recent_games_stop_cutoff = None
            mock_mc._recent_games_exhausted = False
            mock_mc._recent_games_last_page = start_page
            return [{"title": f"G{scan_year}", "slug": f"g{scan_year}", "release_date": f"{scan_year}-06-01"}]

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc._recent_games_stop_cutoff = None
        mock_mc.scan_recent_games.side_effect = fake_scan

        if seed:
            from gamarr.database import Database

            seed_db = Database(path)
            for seed_year, seed_page in seed.items():
                seed_db.set_last_scanned_page("pc", seed_year, seed_page)
            seed_db.close()

        with (
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
        ):
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt
            run_acquisition(
                platform="pc",
                db_path=path,
                qbt_host="localhost",
                qbt_port=8080,
                max_date=floor_month,
                max_cycle_months=max_cycle_months,
                sort_order="new",
            )

    def test_date_mode_resets_progress_at_floor(self, tmp_path: Path) -> None:
        """A stop at the ABSOLUTE floor in the floor year resets the cursors.

        A June floor is used because a January floor can never be met by an
        all-before-floor page inside its own year (that path is exhaustion).
        """
        from gamarr.database import Database

        path = str(tmp_path / "floor.db")
        self._run_floor_cycle(
            path=path,
            floor_month=202406,
            floor_games=[{"title": "Floor", "slug": "floor", "release_date": "2024-05-20"}],
        )
        db = Database(path)
        assert db.get_last_scanned_page("pc", 2024) == 0, "Floor reached must reset the year cursor"
        db.close()

    def test_resumed_floor_year_completes_in_the_same_cycle(self, tmp_path: Path) -> None:
        """A floor year resumed mid-listing completes as soon as its probe is empty.

        The real drain shape credits pages from the resume point and then hits an
        empty probe, so completion must not require a zero-page year.
        """
        from gamarr.database import Database

        path = str(tmp_path / "resumed-floor.db")
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            scan_year = kwargs.get("year")
            start_page = kwargs.get("start_page", 1)
            mock_mc._recent_games_failed = False
            mock_mc._recent_games_cutoff_reached = False
            mock_mc._recent_games_stop_cutoff = None
            if scan_year == 2026:
                mock_mc._recent_games_exhausted = False
                mock_mc._recent_games_last_page = start_page
                return [{"title": "N", "slug": "n", "release_date": "2026-06-01"}]
            # Floor year: one call credits pages 4..6 and its page-7 probe is empty,
            # so the call reports credited pages AND exhaustion together.
            mock_mc._recent_games_last_page = 6
            mock_mc._recent_games_exhausted = True
            return [{"title": "F", "slug": "f", "release_date": "2024-06-01"}]

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc._recent_games_stop_cutoff = None
        mock_mc.scan_recent_games.side_effect = fake_scan

        seed = Database(path)
        seed.set_last_scanned_page("pc", 2024, 3)
        seed.close()

        with (
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
        ):
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt
            run_acquisition(
                platform="pc",
                db_path=path,
                qbt_host="localhost",
                qbt_port=8080,
                max_date=202401,
                max_cycle_months=0,
                sort_order="new",
            )

        db = Database(path)
        assert db.get_last_scanned_page("pc", 2024) == 0, "resumed floor year must complete in-cycle"
        db.close()

    def test_data_free_floor_year_still_completes_the_sweep(self, tmp_path: Path) -> None:
        """A floor year with no games starts at page 1, so it cannot report a drain.

        It must still count as finished, otherwise completion waits for a later
        cycle's all-empty re-probe.
        """
        from gamarr.database import Database

        path = str(tmp_path / "empty-floor.db")
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            scan_year = kwargs.get("year")
            start_page = kwargs.get("start_page", 1)
            mock_mc._recent_games_failed = False
            mock_mc._recent_games_cutoff_reached = False
            mock_mc._recent_games_stop_cutoff = None
            mock_mc._recent_games_last_page = start_page - 1
            if scan_year == 2024:
                # Empty page 1: no drain evidence, no games, no failure.
                mock_mc._recent_games_exhausted = False
                return []
            mock_mc._recent_games_exhausted = False
            mock_mc._recent_games_last_page = start_page
            return [{"title": f"G{scan_year}", "slug": f"g{scan_year}", "release_date": f"{scan_year}-06-01"}]

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc._recent_games_stop_cutoff = None
        mock_mc.scan_recent_games.side_effect = fake_scan

        with (
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
        ):
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt
            run_acquisition(
                platform="pc",
                db_path=path,
                qbt_host="localhost",
                qbt_port=8080,
                max_date=202401,
                max_cycle_months=0,
                sort_order="new",
            )

        db = Database(path)
        assert db.get_last_scanned_page("pc", 2025) == 0, "the sweep must complete and reset"
        db.close()

    def test_pace_stop_in_floor_year_keeps_cursors(self, tmp_path: Path) -> None:
        """A pace (month-span) stop in the floor year must NOT be read as completion.

        The client reports the effective cutoff that stopped the page; if it is
        not the floor, the sweep has not reached max_date yet, so resetting would
        wipe every cursor and re-sweep forever without ever reaching the floor.
        """
        from gamarr.database import Database

        path = str(tmp_path / "pace-stop.db")
        self._run_floor_cycle(
            path=path,
            floor_month=202406,
            floor_games=[{"title": "Pace", "slug": "pace", "release_date": "2024-10-20"}],
            max_cycle_months=2,
            drain_newer=True,
        )
        db = Database(path)
        assert db.get_last_scanned_page("pc", 2024) == 1, "A pace stop must keep the floor-year cursor"
        db.close()

    def test_floor_year_exhaustion_completes_the_sweep(self, tmp_path: Path) -> None:
        """A floor year that ends by listing exhaustion also completes the sweep.

        A January floor can never be met by an all-before-floor page inside its
        own year, so exhaustion has to count as completion too.
        """
        from gamarr.database import Database

        path = str(tmp_path / "floor-exhausted.db")
        self._run_floor_cycle(
            path=path,
            floor_month=202401,
            floor_games=[],
            stop_cutoff=None,
            seed={2024: 3},
        )
        db = Database(path)
        assert db.get_last_scanned_page("pc", 2024) == 0, "Exhausted floor year must reset the cursor"
        db.close()

    def test_mid_year_fetch_failure_does_not_reset_progress(self, tmp_path: Path) -> None:
        """A failure after some pages were credited must still disqualify the sweep.

        The failure flag is raised by the client for any failed page, not only a
        zero-page probe; reading it only on a zero-page probe would let the floor
        year "complete" and wipe a cursor for a year that was only partly listed.
        """
        import datetime

        from gamarr.database import Database

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        path = str(tmp_path / "mid-year-failure.db")
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            scan_year = kwargs.get("year")
            start_page = kwargs.get("start_page", 1)
            if scan_year == current_year:
                # One page credited, then the next fetch failed.
                mock_mc._recent_games_last_page = start_page
                mock_mc._recent_games_exhausted = False
                mock_mc._recent_games_failed = True
                mock_mc._recent_games_cutoff_reached = False
                mock_mc._recent_games_stop_cutoff = None
                return [{"title": "P", "slug": "p", "release_date": f"{scan_year}-06-01"}]
            mock_mc._recent_games_last_page = start_page - 1
            mock_mc._recent_games_exhausted = True
            mock_mc._recent_games_failed = False
            mock_mc._recent_games_cutoff_reached = False
            mock_mc._recent_games_stop_cutoff = None
            return []

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc._recent_games_stop_cutoff = None
        mock_mc.scan_recent_games.side_effect = fake_scan

        seed = Database(path)
        seed.set_last_scanned_page("pc", 2024, 3)
        seed.close()

        with (
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
        ):
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt
            run_acquisition(
                platform="pc",
                db_path=path,
                qbt_host="localhost",
                qbt_port=8080,
                max_date=202401,
                max_cycle_months=0,
                sort_order="new",
            )

        db = Database(path)
        assert db.get_last_scanned_page("pc", 2024) == 3, "a partial failure must not complete the sweep"
        db.close()


class TestMonthSpan:
    """_month_span drives the year-loop allowance guard."""

    def test_within_a_year(self) -> None:
        from gamarr.pipeline import _month_span

        assert _month_span(202511, 202508) == 3

    def test_across_a_year_boundary(self) -> None:
        from gamarr.pipeline import _month_span

        assert _month_span(202601, 202512) == 1
        assert _month_span(202512, 202412) == 12

    def test_unknown_side_is_zero(self) -> None:
        from gamarr.pipeline import _month_span

        assert _month_span(None, 202401) == 0
        assert _month_span(202511, None) == 0

    def test_reversed_order_clamps_to_zero(self) -> None:
        from gamarr.pipeline import _month_span

        assert _month_span(202401, 202511) == 0


class TestDateModeRegressions:
    """Regressions found in code review rounds 1-3."""

    def test_pipeline_call_kwargs_exist_on_client_signature(self, tmp_path: Path) -> None:
        """Every kwarg the pipeline sends must exist on the real client method.

        The pipeline holds the client as ``Any``, so mypy cannot see a renamed
        keyword.  Without this test a stale kwarg raises TypeError inside the
        per-year ``except`` and silently disables discovery.
        """
        import inspect

        from gamarr.metacritic import MetacriticClient

        calls, _logs = TestDateModeResolution._run(tmp_path=tmp_path, max_date=202401, max_cycle_months=2)
        assert calls, "Expected at least one browse call"
        allowed = set(inspect.signature(MetacriticClient.scan_recent_games).parameters)
        for call in calls:
            unknown = sorted(set(call) - allowed)
            assert not unknown, f"pipeline sends kwargs the client does not accept: {unknown}"

    def test_page_mode_call_shape_has_no_date_arguments(self, tmp_path: Path) -> None:
        """max_date 0 must leave the client call byte-identical to page mode."""
        calls, _logs = TestDateModeResolution._run(
            tmp_path=tmp_path,
            max_date=0,
            max_pages=104,
            max_cycle_pages=4,
        )
        assert calls, "Expected at least one browse call"
        for call in calls:
            assert call["cutoff_date"] is None
            assert call["max_month_span"] == 0
            assert call["cache_pages_hours"] == 6
            assert call["max_pages"] > 0

    def test_max_cycle_months_without_max_date_is_inert(self, tmp_path: Path) -> None:
        """A pace set without a floor must not leak into page mode."""
        calls, logs = TestDateModeResolution._run(
            tmp_path=tmp_path,
            max_date=0,
            max_cycle_months=3,
            max_pages=104,
            max_cycle_pages=4,
        )
        assert calls, "Expected at least one browse call"
        for call in calls:
            assert call["cutoff_date"] is None
            assert call["max_month_span"] == 0
            assert call["max_pages"] > 0
        assert any("max_cycle_months ignored" in line for line in logs), logs

    def test_later_calls_use_the_cycle_anchor_not_a_fresh_span(self, tmp_path: Path) -> None:
        """After the first call the pace must be an absolute cutoff, not a re-anchor."""
        calls, _logs = TestDateModeResolution._run(
            tmp_path=tmp_path,
            max_date=202401,
            max_cycle_months=2,
            scan_games=[{"title": "G", "slug": "g", "release_date": "2025-11-05"}],
        )
        assert len(calls) >= 2, f"Expected a second year call, got {len(calls)}"
        assert calls[0]["max_month_span"] == 2
        for later in calls[1:]:
            assert later["max_month_span"] == 0, later
            assert later["cutoff_date"] is not None
            assert later["cutoff_date"] > "2024-01-01", later

    def test_floor_level_stop_above_the_floor_year_still_descends(self, tmp_path: Path) -> None:
        """A mis-indexed floor-level stop above the floor year must not stall the sweep.

        Breaking there would probe the same page every cycle and never reach the
        floor year, so the loop must descend and let the floor year complete it.
        """
        import datetime

        from gamarr.database import Database

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        path = str(tmp_path / "descend.db")
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            scan_year = kwargs.get("year")
            start_page = kwargs.get("start_page", 1)
            mock_mc._recent_games_failed = False
            if scan_year == current_year:
                # Mis-indexed: every page looks older than the floor.
                mock_mc._recent_games_cutoff_reached = True
                mock_mc._recent_games_stop_cutoff = "2024-01-01"
                mock_mc._recent_games_exhausted = False
                mock_mc._recent_games_last_page = start_page - 1
                return []
            if scan_year == 2024:
                mock_mc._recent_games_cutoff_reached = False
                mock_mc._recent_games_stop_cutoff = None
                mock_mc._recent_games_exhausted = True
                mock_mc._recent_games_last_page = start_page - 1
                return []
            mock_mc._recent_games_cutoff_reached = False
            mock_mc._recent_games_stop_cutoff = None
            mock_mc._recent_games_exhausted = False
            mock_mc._recent_games_last_page = start_page
            return [{"title": f"G{scan_year}", "slug": f"g{scan_year}", "release_date": f"{scan_year}-06-01"}]

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc._recent_games_stop_cutoff = None
        mock_mc.scan_recent_games.side_effect = fake_scan

        seed = Database(path)
        seed.set_last_scanned_page("pc", 2024, 3)
        seed.close()

        from loguru import logger as loguru_logger

        logs: list[str] = []
        sink_id = loguru_logger.add(lambda msg: logs.append(str(msg)), level="WARNING", format="{message}")
        try:
            with (
                patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
                patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
                patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
            ):
                mock_mc_cls.return_value = mock_mc
                mock_qbt = MagicMock()
                mock_qbt.is_connected.return_value = True
                mock_qbt_cls.return_value = mock_qbt
                run_acquisition(
                    platform="pc",
                    db_path=path,
                    qbt_host="localhost",
                    qbt_port=8080,
                    max_date=202401,
                    max_cycle_months=0,
                    sort_order="new",
                )
        finally:
            loguru_logger.remove(sink_id)

        db = Database(path)
        assert db.get_last_scanned_page("pc", 2024) == 0, "The sweep must reach the floor year and complete"
        db.close()
        assert any("mis-indexed" in line for line in logs), logs

    def test_games_older_than_the_floor_are_not_queued(self) -> None:
        """A mixed boundary page must not queue games older than max_date."""
        from gamarr.pipeline import _games_at_or_after_floor

        page: list[dict[str, Any]] = [
            {"title": "InFloor", "release_date": "2024-07-01"},
            {"title": "BelowFloor", "release_date": "2024-03-01"},
            {"title": "InFloorMonth", "release_date": "2024-06-30"},
            {"title": "Undated", "release_date": None},
        ]
        kept = [game["title"] for game in _games_at_or_after_floor(page, 202406)]
        assert kept == ["InFloor", "InFloorMonth", "Undated"]

    def test_below_floor_games_never_reach_the_pending_queue(self, tmp_path: Path) -> None:
        """The date-mode filter is applied to what the client returns, end to end."""
        from gamarr.database import Database
        from gamarr.metacritic import ScoreResult

        path = str(tmp_path / "queue-filter.db")
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            mock_mc._recent_games_last_page = kwargs.get("start_page", 1)
            mock_mc._recent_games_exhausted = False
            mock_mc._recent_games_failed = False
            mock_mc._recent_games_cutoff_reached = False
            mock_mc._recent_games_stop_cutoff = None
            base = {"score": 1478.0, "user_rating": 8.2, "critic_review_count": 40, "user_review_count": 40}
            return [
                {"title": "In Floor", "slug": "in-floor", "release_date": "2024-07-01", **base},
                {"title": "Below Floor", "slug": "below-floor", "release_date": "2024-03-01", **base},
            ]

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc._recent_games_stop_cutoff = None
        mock_mc.scan_recent_games.side_effect = fake_scan
        # Verify-before-queue: the in-floor game only queues because its detail
        # page passes; the below-floor game is filtered before any lookup.
        mock_mc.lookup_game.return_value = ScoreResult(
            title="In Floor",
            slug="in-floor",
            metascore=88.0,
            metascore_review_count=40,
            user_score=8.4,
            user_review_count=40,
            passed=True,
        )

        with (
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
        ):
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt
            run_acquisition(
                platform="pc",
                db_path=path,
                qbt_host="localhost",
                qbt_port=8080,
                max_date=202406,
                max_cycle_months=0,
                sort_order="new",
            )

        db = Database(path)
        pending = {game.slug for game in db.get_pending(platform="pc")}
        db.close()
        assert "in-floor" in pending, f"in-floor game must be queued, got {pending}"
        assert "below-floor" not in pending, f"below-floor game must be filtered out, got {pending}"

    def test_invalid_max_date_from_a_direct_caller_is_ignored(self, tmp_path: Path) -> None:
        """A direct API caller passing a bad month must fall back to page mode."""
        calls, logs = TestDateModeResolution._run(tmp_path=tmp_path, max_date=202413, max_pages=104)
        assert calls
        for call in calls:
            assert call["cutoff_date"] is None
            assert call["max_month_span"] == 0
        assert any("not a valid YYYYMM" in line for line in logs), logs

    def test_negative_max_cycle_months_is_ignored(self, tmp_path: Path) -> None:
        """A negative pace must be treated as no pacing, with a warning."""
        calls, logs = TestDateModeResolution._run(
            tmp_path=tmp_path,
            max_date=202401,
            max_cycle_months=-3,
        )
        assert calls
        for call in calls:
            assert call["max_month_span"] == 0
        assert any("is negative" in line for line in logs), logs

    def test_non_integer_max_date_from_a_direct_caller_is_ignored(self, tmp_path: Path) -> None:
        """A string or bool floor must not be treated as a month."""
        for bad in ("202401", True):
            calls, logs = TestDateModeResolution._run(
                tmp_path=tmp_path,
                max_date=cast("int", bad),
                max_pages=104,
            )
            assert calls
            for call in calls:
                assert call["cutoff_date"] is None, bad
            assert any("not a valid YYYYMM" in line for line in logs), (bad, logs)

    def test_future_max_date_from_a_direct_caller_is_clamped(self, tmp_path: Path) -> None:
        """A future floor must be clamped, not swept as-is."""
        import datetime

        now = datetime.datetime.now(tz=datetime.UTC)
        future = (now.year + 3) * 100 + now.month
        calls, logs = TestDateModeResolution._run(tmp_path=tmp_path, max_date=future)
        assert calls
        expected = f"{now.year:04d}-{now.month:02d}-01"
        assert all(call["cutoff_date"] == expected for call in calls), calls[:3]
        assert any("in the future" in line for line in logs), logs

    def test_implausible_max_cycle_months_is_clamped(self, tmp_path: Path) -> None:
        """An absurd pace must be bounded so the month arithmetic cannot underflow."""
        calls, logs = TestDateModeResolution._run(
            tmp_path=tmp_path,
            max_date=202401,
            max_cycle_months=5000,
        )
        assert calls
        assert calls[0]["max_month_span"] == 1200
        assert any("implausibly large" in line for line in logs), logs

    def test_cancelled_cycle_does_not_reset_date_mode_progress(self, tmp_path: Path) -> None:
        """A shutdown mid-sweep must not wipe the per-year cursors."""
        import threading

        from gamarr.database import Database

        path = str(tmp_path / "cancel-reset.db")
        cancel_event = threading.Event()
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            start_page = kwargs.get("start_page", 1)
            scan_year = kwargs.get("year")
            mock_mc._recent_games_failed = False
            mock_mc._recent_games_last_page = start_page
            mock_mc._recent_games_exhausted = False
            if scan_year == 2024:
                # Floor stop on the floor year; shutdown arrives right after it.
                mock_mc._recent_games_cutoff_reached = True
                mock_mc._recent_games_stop_cutoff = kwargs.get("cutoff_date")
                kwargs["cancel_event"].set()
                return [{"title": "F", "slug": "f", "release_date": "2024-06-01"}]
            mock_mc._recent_games_cutoff_reached = False
            mock_mc._recent_games_stop_cutoff = None
            return [{"title": f"G{scan_year}", "slug": f"g{scan_year}", "release_date": f"{scan_year}-06-01"}]

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc._recent_games_stop_cutoff = None
        mock_mc.scan_recent_games.side_effect = fake_scan

        seed = Database(path)
        seed.set_last_scanned_page("pc", 2024, 3)
        seed.close()

        with (
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
        ):
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt
            run_acquisition(
                platform="pc",
                db_path=path,
                qbt_host="localhost",
                qbt_port=8080,
                max_date=202401,
                max_cycle_months=0,
                sort_order="new",
                cancel_event=cancel_event,
            )

        db = Database(path)
        assert db.get_last_scanned_page("pc", 2024) != 0, "a cancelled cycle must not reset progress"
        db.close()

    def test_empty_cached_row_is_not_cached_browse_data(self, tmp_path: Path) -> None:
        """An empty cached row must not suppress a scrape-failure notification."""
        import datetime

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            mock_mc._recent_games_last_page = kwargs.get("start_page", 1)
            mock_mc._recent_games_exhausted = False
            mock_mc._recent_games_failed = False
            mock_mc._recent_games_cutoff_reached = False
            mock_mc._recent_games_stop_cutoff = None
            return []

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc._recent_games_stop_cutoff = None
        mock_mc.scan_recent_games.side_effect = fake_scan
        # A legacy empty row is present in the cache: not usable browse data.
        mock_mc._cache.get_browse_page.return_value = []

        with (
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
            patch("gamarr.pipeline._diagnose_and_notify_scrape") as mock_notify,
        ):
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt
            run_acquisition(
                platform="pc",
                db_path=str(tmp_path / "empty-cache.db"),
                qbt_host="localhost",
                qbt_port=8080,
                max_date=current_year * 100 + 1,
                max_cycle_months=0,
                sort_order="new",
            )
        mock_notify.assert_called_once()

    def test_cache_hours_is_forwarded_to_the_client(self, tmp_path: Path) -> None:
        """A non-default cache TTL must reach the client, not be hard-coded."""
        calls, _logs = TestDateModeResolution._run(tmp_path=tmp_path, max_date=202401, cache_hours=7)
        assert calls, "Expected at least one browse call"
        assert all(call["cache_pages_hours"] == 7 for call in calls)

    def test_year_loop_stops_when_the_allowance_is_already_spent(self, tmp_path: Path) -> None:
        """A collected span >= max_cycle_months must skip the next year."""
        calls, _logs = TestDateModeResolution._run(
            tmp_path=tmp_path,
            max_date=202401,
            max_cycle_months=2,
            scan_games=[
                {"title": "A", "slug": "a", "release_date": "2025-11-05"},
                {"title": "B", "slug": "b", "release_date": "2025-08-05"},
            ],
        )
        assert len(calls) == 1, f"allowance already spent; expected 1 call, got {len(calls)}"

    def test_drained_year_then_cutoff_year_does_not_reset_progress(self, tmp_path: Path) -> None:
        """A drained probe plus a cutoff-stopped year must not wipe cursors.

        The cutoff break happens before the page counter would advance, so
        ``range_empty`` must not be judged on a page count that excludes the
        call which stopped on the cutoff.
        """
        import datetime

        from gamarr.database import Database

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        older_year = current_year - 1
        path = str(tmp_path / "drain-vs-cutoff.db")

        seed = Database(path)
        seed.set_last_scanned_page("pc", current_year, 5)
        seed.close()

        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            mock_mc._recent_games_failed = False
            if kwargs.get("year") == current_year:
                mock_mc._recent_games_exhausted = True
                mock_mc._recent_games_cutoff_reached = False
                mock_mc._recent_games_stop_cutoff = None
                mock_mc._recent_games_last_page = kwargs["start_page"] - 1
                return []
            mock_mc._recent_games_exhausted = False
            mock_mc._recent_games_cutoff_reached = True
            mock_mc._recent_games_stop_cutoff = f"{older_year}-09-01"
            mock_mc._recent_games_last_page = kwargs["start_page"]
            return [{"title": "G", "slug": "g", "release_date": f"{older_year}-11-01"}]

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc._recent_games_stop_cutoff = None
        mock_mc.scan_recent_games.side_effect = fake_scan

        with (
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
        ):
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt
            run_acquisition(
                platform="pc",
                db_path=path,
                qbt_host="localhost",
                qbt_port=8080,
                max_date=200001,
                max_cycle_months=2,
                sort_order="new",
            )

        db = Database(path)
        assert db.get_last_scanned_page("pc", current_year) == 5, "drained year cursor must be kept"
        assert db.get_last_scanned_page("pc", older_year) == 1, "cutoff-stopped year must keep its page"
        db.close()

    @staticmethod
    def _run_single_year_cycle(
        *,
        path: str,
        games: list[dict[str, Any]],
        cutoff_reached: bool,
        stop_cutoff: str | None,
        fetch_fails: bool,
    ) -> Any:
        """Run one date-mode cycle over the current year and return the notify mock."""
        import datetime

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            start_page = kwargs.get("start_page", 1)
            if fetch_fails:
                # A failed fetch credits no page and collects nothing.
                mock_mc._recent_games_last_page = start_page - 1
                mock_mc._recent_games_exhausted = False
                mock_mc._recent_games_failed = True
                mock_mc._recent_games_cutoff_reached = False
                mock_mc._recent_games_stop_cutoff = None
                return []
            mock_mc._recent_games_failed = False
            if cutoff_reached:
                # A date stop fetches a page, collects nothing from it, and always
                # reports the effective cutoff that fired.
                mock_mc._recent_games_last_page = start_page - 1
                mock_mc._recent_games_exhausted = False
                mock_mc._recent_games_cutoff_reached = True
                mock_mc._recent_games_stop_cutoff = stop_cutoff
                return []
            mock_mc._recent_games_last_page = start_page
            mock_mc._recent_games_exhausted = False
            mock_mc._recent_games_cutoff_reached = False
            mock_mc._recent_games_stop_cutoff = None
            return games

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc._recent_games_stop_cutoff = None
        mock_mc.scan_recent_games.side_effect = fake_scan
        # No cached browse page, so the scrape-health check reaches its notify path.
        mock_mc._cache.get_browse_page.return_value = None

        with (
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
            patch("gamarr.pipeline._diagnose_and_notify_scrape") as mock_notify,
        ):
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt
            run_acquisition(
                platform="pc",
                db_path=path,
                qbt_host="localhost",
                qbt_port=8080,
                max_date=current_year * 100 + 1,
                max_cycle_months=0,
                sort_order="new",
            )
        return mock_notify

    def test_date_mode_cutoff_stop_is_not_a_scrape_failure(self, tmp_path: Path) -> None:
        """A date-mode cycle that ends on its own cutoff must not notify."""
        import datetime

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        notify = self._run_single_year_cycle(
            path=str(tmp_path / "no-notify.db"),
            games=[],
            cutoff_reached=True,
            stop_cutoff=f"{current_year}-01-01",
            fetch_fails=False,
        )
        notify.assert_not_called()

    def test_date_mode_fetch_failure_still_notifies(self, tmp_path: Path) -> None:
        """A real fetch failure in date mode must still report a scrape failure."""
        notify = self._run_single_year_cycle(
            path=str(tmp_path / "notify.db"),
            games=[],
            cutoff_reached=False,
            stop_cutoff=None,
            fetch_fails=True,
        )
        notify.assert_called_once()


class TestExtractRepackFeatures:
    """The keyword scan is scoped to the Repack Features block."""

    def test_returns_block_after_heading(self) -> None:
        from gamarr.pipeline import _extract_repack_features

        article = (
            "Company: Game Science Original Size: 140 GB Download Mirrors "
            "Only updated files for a new non-HV repack. "
            "Repack Features Based on P2P Steam BuildID 21393610 release: 139.7 GB "
            "Hypervisor Bypass (INTEL & AMD) by DenuvOwO added "
            "Screenshots Screenshot gallery text"
        )
        features = _extract_repack_features(article)
        assert features is not None
        assert "Hypervisor Bypass" in features
        assert "Only updated files" not in features, "download-mirror boilerplate must be excluded"
        assert "Screenshot gallery" not in features, "block must stop at the next heading"

    def test_missing_heading_returns_none(self) -> None:
        from gamarr.pipeline import _extract_repack_features

        assert _extract_repack_features("Company: Ubisoft Repack Size: 40 GB") is None
        assert _extract_repack_features(None) is None
        assert _extract_repack_features("") is None

    def test_block_is_capped(self) -> None:
        from gamarr.pipeline import _extract_repack_features

        article = "Repack Features " + ("word " * 2000)
        features = _extract_repack_features(article)
        assert features is not None
        assert len(features) <= 4000, "a page missing the next heading must not scan the whole article"

    def test_stops_at_backwards_compatibility_heading(self) -> None:
        from gamarr.pipeline import _extract_repack_features

        article = (
            "Repack Features Nothing ripped, 100% lossless "
            "Backwards Compatibility This repack works with the HV version"
        )
        features = _extract_repack_features(article)
        assert features is not None
        assert "lossless" in features
        assert "works with the HV version" not in features


class TestKeywordMatch:
    """Whole-word matching with a negation guard."""

    def test_plain_mention_matches(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("Hypervisor Bypass (INTEL & AMD) applied", ["hv", "hypervisor"]) == "hypervisor"

    def test_hv_as_standalone_word_matches(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("Custom launcher added for easier HV steps processing", ["hv"]) == "hv"

    def test_negated_non_hv_does_not_match(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("Only updated files for a new non-HV repack", ["hv"]) is None

    def test_negated_without_hv_does_not_match(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("Works without HV and without VBS", ["hv"]) is None

    def test_negated_no_hv_does_not_match(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("No HV required for this build", ["hv"]) is None

    def test_keyword_inside_a_word_does_not_match(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("shevron graphics and whatever", ["hv"]) is None

    def test_multi_word_keyword_matches(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("Includes the Goldberg emulator build", ["emulator"]) == "emulator"

    def test_empty_inputs_return_none(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match(None, ["hv"]) is None
        assert _keyword_match("HV", None) is None
        assert _keyword_match("HV", []) is None


class TestVerifyBeforeQueue:
    """Only games that already pass detail verification enter the queue."""

    @staticmethod
    def _run(tmp_path: Path, *, result: Any) -> list[str]:
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "queue.db"))
        mc = MagicMock()
        mc.lookup_game.return_value = result
        games = [
            {
                "title": "Candidate",
                "slug": "candidate",
                "score": 1478.0,
                "user_rating": 8.2,
                "critic_review_count": 40,
                "user_review_count": 40,
                "release_date": "2026-11-01",
            }
        ]
        _process_browse_games(
            games,
            "pc",
            db,
            {
                "min_criticscore": 75,
                "min_criticscore_reviews": 10,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
            max_queue_days=60,
            mc=mc,
            cache_details_days=7,
            cache_pages_hours=6,
        )
        slugs = [game.slug for game in db.get_pending()]
        db.close()
        return slugs

    def test_passing_game_is_queued_verified(self, tmp_path: Path) -> None:
        from gamarr.metacritic import ScoreResult

        result = ScoreResult(
            title="Candidate",
            slug="candidate",
            metascore=88.0,
            metascore_review_count=40,
            user_score=8.4,
            user_review_count=40,
            passed=True,
        )
        assert self._run(tmp_path, result=result) == ["candidate"]

    def test_failing_game_is_not_queued(self, tmp_path: Path) -> None:
        from gamarr.metacritic import ScoreResult

        result = ScoreResult(
            title="Candidate",
            slug="candidate",
            metascore=40.0,
            metascore_review_count=40,
            user_score=4.0,
            user_review_count=40,
            passed=False,
        )
        assert self._run(tmp_path, result=result) == []

    def test_unreviewed_game_is_not_queued(self, tmp_path: Path) -> None:
        from gamarr.metacritic import ScoreResult

        result = ScoreResult(
            title="Candidate",
            slug="candidate",
            metascore=None,
            metascore_review_count=None,
            user_score=None,
            user_review_count=None,
            passed=False,
        )
        assert self._run(tmp_path, result=result) == []

    def test_lookup_failure_is_not_queued(self, tmp_path: Path) -> None:
        assert self._run(tmp_path, result=None) == []

    def test_queued_row_is_marked_verified(self, tmp_path: Path) -> None:
        from gamarr.database import Database
        from gamarr.metacritic import ScoreResult

        result = ScoreResult(
            title="Candidate",
            slug="candidate",
            metascore=88.0,
            metascore_review_count=40,
            user_score=8.4,
            user_review_count=40,
            passed=True,
        )
        self._run(tmp_path, result=result)
        db = Database(str(tmp_path / "queue.db"))
        pending = db.get_pending()
        assert pending and pending[0].score_checks_passed is True
        assert pending[0].metascore == 88.0, "the real detail score must be stored, not the browse metric"
        db.close()


class TestLatestMode:
    """Latest mode reads the top of the window and never touches cursors."""

    @staticmethod
    def _run(
        *,
        tmp_path: Path,
        mode: Literal["latest", "backlog"] = "latest",
        max_pages: int = 3,
        max_date: int = 0,
        max_cycle_months: int = 0,
        max_cycle_pages: int = 0,
        sort_order: Literal["new", "criticscore", "userscore"] = "new",
        db_path: str | None = None,
        sort_orders: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Run one cycle and return (scan calls, log lines).

        Pass *sort_orders* to collect the client's ``sort_order`` per call: the
        pipeline sets it as an attribute, so the kwargs capture cannot see it.
        """
        from loguru import logger as loguru_logger

        calls: list[dict[str, Any]] = []
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            calls.append(dict(kwargs))
            if sort_orders is not None:
                sort_orders.append(mock_mc.sort_order)
            mock_mc._recent_games_last_page = kwargs.get("start_page", 1)
            mock_mc._recent_games_exhausted = False
            mock_mc._recent_games_failed = False
            mock_mc._recent_games_cutoff_reached = False
            mock_mc._recent_games_stop_cutoff = None
            return [{"title": "G", "slug": "g", "release_date": "2026-11-01"}]

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc._recent_games_stop_cutoff = None
        mock_mc.scan_recent_games.side_effect = fake_scan

        logs: list[str] = []
        sink = loguru_logger.add(lambda msg: logs.append(str(msg)), level="INFO", format="{message}")
        try:
            with (
                patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
                patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
                patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
            ):
                mock_mc_cls.return_value = mock_mc
                mock_qbt = MagicMock()
                mock_qbt.is_connected.return_value = True
                mock_qbt_cls.return_value = mock_qbt
                run_acquisition(
                    platform="pc",
                    db_path=db_path or str(tmp_path / f"{mode}.db"),
                    qbt_host="localhost",
                    qbt_port=8080,
                    mode=mode,
                    max_pages=max_pages,
                    max_date=max_date,
                    max_cycle_months=max_cycle_months,
                    max_cycle_pages=max_cycle_pages,
                    sort_order=sort_order,
                )
        finally:
            loguru_logger.remove(sink)
        return calls, logs

    def test_starts_at_page_one_and_stops_at_depth(self, tmp_path: Path) -> None:
        calls, _logs = self._run(tmp_path=tmp_path, max_pages=3)
        assert calls, "expected at least one browse call"
        assert calls[0]["start_page"] == 1
        assert all(call["start_page"] == 1 for call in calls)
        assert calls[0]["max_pages"] == 2, "one page of the depth is reserved for the years below"
        # The fake yields one page per call, so the call count is the pages read.
        assert len(calls) <= 3, "the cycle may not exceed its depth"
        assert all(call["max_pages"] <= 3 for call in calls), "no call may exceed the depth"

    def test_does_not_persist_cursors(self, tmp_path: Path) -> None:
        import datetime

        from gamarr.database import Database

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        path = str(tmp_path / "latest.db")
        self._run(tmp_path=tmp_path, max_pages=3)
        db = Database(path)
        assert db.get_last_scanned_page("pc", current_year) == 0, "latest mode must not write cursors"
        db.close()

    def test_logs_the_mode_and_scan_summary(self, tmp_path: Path) -> None:
        _calls, logs = self._run(tmp_path=tmp_path, max_pages=3)
        assert any("Discovery mode: latest" in line for line in logs), logs
        assert any("Latest scan:" in line for line in logs), logs

    def test_inert_pacing_keys_are_reported(self, tmp_path: Path) -> None:
        _calls, logs = self._run(tmp_path=tmp_path, max_pages=3, max_cycle_months=2, max_cycle_pages=4)
        assert any("max_cycle_months ignored in latest mode" in line for line in logs), logs
        assert any("max_cycle_pages ignored in latest mode" in line for line in logs), logs

    def test_score_sort_is_coerced_with_a_warning(self, tmp_path: Path) -> None:
        orders: list[str] = []
        calls, logs = self._run(tmp_path=tmp_path, max_pages=3, sort_order="criticscore", sort_orders=orders)
        assert any("latest mode requires sort_order 'new'" in line.lower() for line in logs), logs
        assert calls[0]["year"] is not None, "coerced scans must keep the per-year dimension"
        assert orders[0] == "new", (
            "the coerced cycle must actually browse in 'new' order, else the date stop logic is dead"
        )

    def test_zero_depth_is_clamped(self, tmp_path: Path) -> None:
        calls, logs = self._run(tmp_path=tmp_path, max_pages=0)
        assert calls[0]["max_pages"] == 9, "the clamped depth of 10 keeps one page for the next year"
        assert any("clamping" in line.lower() for line in logs), logs

    def test_latest_with_a_floor_bounds_the_window_and_filters_the_queue(self, tmp_path: Path) -> None:
        """latest + max_date still passes the floor cutoff and the queue filter."""
        calls, _logs = self._run(tmp_path=tmp_path, max_pages=3, max_date=202401)
        assert calls, "expected at least one browse call"
        for call in calls:
            assert call["cutoff_date"] == "2024-01-01", call
            assert call["year"] is not None, "latest keeps the per-year dimension"

    def test_latest_sort_coercion_does_not_wipe_backlog_cursors(self, tmp_path: Path) -> None:
        """A coerced latest cycle must leave the stored sort order and cursors alone."""
        from gamarr.database import Database

        path = str(tmp_path / "coerce.db")
        seed = Database(path)
        seed.set_last_sort_order("pc", "criticscore")
        seed.set_last_scanned_page("pc", 2025, 7)
        seed.close()

        self._run(tmp_path=tmp_path, max_pages=3, sort_order="criticscore", db_path=path)

        db = Database(path)
        assert db.get_last_scanned_page("pc", 2025) == 7, "latest mode must not reset backlog progress"
        assert db.get_last_sort_order("pc") == "new", "the order the cache was written in is persisted"
        db.close()

    def test_backlog_after_latest_resets_cursors_from_another_order(self, tmp_path: Path) -> None:
        """Returning to backlog must not resume cursors that belong to another order.

        Regression guard for the HIGH finding: latest wrote the cache order into
        the field backlog used to validate its cursors, so a flipback could
        resume at page 8 of a differently-ordered listing and skip pages 1-7.
        """
        from gamarr.database import Database

        path = str(tmp_path / "flipback.db")
        seed = Database(path)
        seed.set_last_sort_order("pc", "criticscore")
        seed.set_cursor_sort_order("pc", "criticscore")
        seed.set_last_scanned_page("pc", 2025, 7)
        seed.close()

        # One coerced latest cycle: critic score sort -> scan order "new".
        self._run(tmp_path=tmp_path, max_pages=3, sort_order="criticscore", db_path=path)

        # Back to backlog in date mode (no page-budget auto-reset to mask it),
        # with the default order: the 2025 cursor belongs to another order.
        calls, _logs = self._run(
            tmp_path=tmp_path,
            mode="backlog",
            max_pages=3,
            max_date=202401,
            sort_order="new",
            db_path=path,
        )

        year_calls = [call for call in calls if call.get("year") == 2025]
        assert year_calls, f"expected a 2025 scan call, got {calls}"
        assert year_calls[0]["start_page"] == 1, (
            f"must resume from page 1, not from a cursor written under another order "
            f"(got {year_calls[0]['start_page']})"
        )
        db = Database(path)
        assert db.get_last_scanned_page("pc", 2025) == 1, "the run read exactly one page from the top"
        assert db.get_cursor_sort_order("pc") == "new"
        db.close()

    def test_coerced_latest_does_not_clear_the_cache_every_cycle(self, tmp_path: Path) -> None:
        """After the first coerced cycle the stored order matches, so no second clear."""
        from gamarr.database import Database

        path = str(tmp_path / "cache-thrash.db")
        seed = Database(path)
        seed.set_last_sort_order("pc", "criticscore")
        seed.close()

        cleared: list[str] = []
        original = Database.clear_browse_cache

        def spy(self: Database, platform: str) -> None:
            cleared.append(platform)
            return original(self, platform)

        with patch.object(Database, "clear_browse_cache", spy):
            self._run(tmp_path=tmp_path, max_pages=3, sort_order="criticscore", db_path=path)
            self._run(tmp_path=tmp_path, max_pages=3, sort_order="criticscore", db_path=path)

        assert len(cleared) == 1, f"expected one cache clear for the order change, got {len(cleared)}"

    def test_backlog_mode_keeps_using_cursors(self, tmp_path: Path) -> None:
        import datetime

        from gamarr.database import Database

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        path = str(tmp_path / "backlog.db")
        self._run(tmp_path=tmp_path, mode="backlog", max_pages=3)
        db = Database(path)
        assert db.get_last_scanned_page("pc", current_year) >= 1, "backlog mode must still advance cursors"
        db.close()


class TestBrowseLookupPool:
    """The threaded browse lookup preserves order, isolates failures, and honours cancellation."""

    @staticmethod
    def _games() -> list[dict[str, Any]]:
        return [
            {
                "title": f"Game {index}",
                "slug": f"game-{index}",
                "score": 1478.0,
                "user_rating": 8.2,
                "critic_review_count": 40,
                "user_review_count": 40,
                "release_date": "2026-11-01",
            }
            for index in (1, 2, 3)
        ]

    @staticmethod
    def _client(boom_slug: str | None = None) -> MagicMock:
        from gamarr.metacritic import ScoreResult

        mock_mc = MagicMock()

        def lookup(title: str, platform: str, **kwargs: Any) -> ScoreResult:
            if boom_slug is not None and kwargs.get("slug") == boom_slug:
                raise RuntimeError("malformed detail page")
            return ScoreResult(
                title=title,
                slug=str(kwargs.get("slug")),
                metascore=88.0,
                metascore_review_count=40,
                user_score=8.4,
                user_review_count=40,
                passed=True,
            )

        mock_mc.lookup_game.side_effect = lookup
        return mock_mc

    def _run(
        self,
        tmp_path: Path,
        *,
        boom_slug: str | None = None,
        cancelled: bool = False,
        db_path: str | None = None,
    ) -> tuple[int, list[str], MagicMock]:
        import threading

        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        mock_mc = self._client(boom_slug)
        db = Database(db_path or str(tmp_path / "pool.db"))
        cancel_event = threading.Event()
        if cancelled:
            cancel_event.set()
        count = _process_browse_games(
            self._games(),
            "pc",
            db,
            {
                "min_criticscore": 75,
                "min_criticscore_reviews": 10,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
            max_queue_days=60,
            mc=mock_mc,
            cancel_event=cancel_event,
        )
        slugs = [game.slug for game in db.get_pending()]
        db.close()
        return count, slugs, mock_mc

    def test_candidate_order_is_preserved(self, tmp_path: Path) -> None:
        count, slugs, _mc = self._run(tmp_path=tmp_path)
        assert count == 3
        assert slugs == ["game-1", "game-2", "game-3"], f"pool.map must preserve order, got {slugs}"

    def test_one_raising_lookup_does_not_abort_the_batch(self, tmp_path: Path) -> None:
        count, slugs, _mc = self._run(tmp_path=tmp_path, boom_slug="game-2")
        assert count == 2
        assert slugs == ["game-1", "game-3"], f"only the failing candidate is skipped, got {slugs}"

    def test_lookup_uses_direct_only(self, tmp_path: Path) -> None:
        _count, _slugs, mock_mc = self._run(tmp_path=tmp_path)
        for call in mock_mc.lookup_game.call_args_list:
            assert call.kwargs.get("direct_only") is True, call

    def test_cancelled_batch_queues_nothing(self, tmp_path: Path) -> None:
        count, slugs, _mc = self._run(tmp_path=tmp_path, cancelled=True)
        assert count == 0
        assert slugs == [], "an abandoned cycle must not queue games"

    def test_cancellation_arriving_during_lookups_discards_the_batch(self, tmp_path: Path) -> None:
        """An event set while the pool runs must still discard the whole batch."""
        import threading

        from gamarr.database import Database
        from gamarr.metacritic import ScoreResult
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "cancel-mid.db"))
        cancel_event = threading.Event()
        mock_mc = MagicMock()

        def lookup(title: str, platform: str, **kwargs: Any) -> ScoreResult:
            cancel_event.set()  # shutdown lands mid-batch
            return ScoreResult(
                title=title,
                slug=str(kwargs.get("slug")),
                metascore=88.0,
                metascore_review_count=40,
                user_score=8.4,
                user_review_count=40,
                passed=True,
            )

        mock_mc.lookup_game.side_effect = lookup
        count = _process_browse_games(
            self._games(),
            "pc",
            db,
            {
                "min_criticscore": 75,
                "min_criticscore_reviews": 10,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
            max_queue_days=60,
            mc=mock_mc,
            cancel_event=cancel_event,
        )
        slugs = [game.slug for game in db.get_pending()]
        db.close()
        assert count == 0, "a cycle cancelled during lookups must not queue"
        assert slugs == []

    def test_in_memory_database_works_from_worker_threads(self) -> None:
        """A pooled lookup reads the DB off-thread, so :memory: must be shared.

        The fake lookup performs a real cache read from the worker thread — which
        is what the production client does — so a per-thread in-memory database
        would raise ``no such table`` and queue nothing.
        """
        from gamarr.database import Database
        from gamarr.metacritic import ScoreResult
        from gamarr.metacritic_cache import MetacriticCache
        from gamarr.pipeline import _process_browse_games

        db = Database(":memory:")
        cache = MetacriticCache(db)
        mock_mc = MagicMock()

        def lookup(title: str, platform: str, **kwargs: Any) -> ScoreResult:
            cache.get_browse_page(platform, 1, ttl_hours=1, year=0)  # real DB read, off-thread
            return ScoreResult(
                title=title,
                slug=str(kwargs.get("slug")),
                metascore=88.0,
                metascore_review_count=40,
                user_score=8.4,
                user_review_count=40,
                passed=True,
            )

        mock_mc.lookup_game.side_effect = lookup
        count = _process_browse_games(
            self._games(),
            "pc",
            db,
            {
                "min_criticscore": 75,
                "min_criticscore_reviews": 10,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
            max_queue_days=60,
            mc=mock_mc,
        )
        slugs = [game.slug for game in db.get_pending()]
        db.close()
        assert count == 3, "the in-memory engine must be visible to worker threads"
        assert slugs == ["game-1", "game-2", "game-3"]


class TestKeywordNegationEdges:
    """Negation rules after the round-3 fixes: no forward look, contractions handled."""

    def test_not_supported_is_not_negation(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert (
            _keyword_match("Includes the Goldberg emulator (not supported for online play)", ["emulator"]) == "emulator"
        )

    def test_hv_with_unrelated_not_required_is_not_negated(self) -> None:
        """A neighbouring item's negation must not excuse an HV mention."""
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("Hypervisor Bypass included, DX11 not required", ["hypervisor"]) == "hypervisor"

    def test_contraction_negation_is_understood(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("This repack doesn't include an HV version", ["hv"]) is None

    def test_negation_before_a_filler_phrase_is_understood(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("It is not needed for HV support", ["hv"]) is None

    def test_negation_after_the_keyword_is_treated_as_a_genuine_mention(self) -> None:
        """Deliberate conservatism: a trailing negation never excuses an HV mention.

        A forward look cannot tell "HV bypass, DX not required" from "HV not
        required", and a missed rejection delivers a repack the user excluded, so
        only backward negation counts.
        """
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("HV not needed for this build", ["hv"]) == "hv"

    def test_article_between_negation_and_keyword(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("This is not an HV repack", ["hv"]) is None


class TestExtractRepackFeaturesHeadings:
    """Stop headings must be matched by their canonical spelling."""

    def test_included_dlcs_bullet_does_not_truncate_the_block(self) -> None:
        """A features bullet mentioning 'included DLCs' must not end the block."""
        from gamarr.pipeline import _extract_repack_features

        article = (
            "Repack Features Based on Steam release "
            "All included DLCs are present in the installer "
            "Hypervisor Bypass (INTEL & AMD) applied "
            "Screenshots gallery"
        )
        features = _extract_repack_features(article)
        assert features is not None
        assert "Hypervisor Bypass" in features, "the HV line must stay inside the scanned block"
        assert "Screenshot gallery" not in features


class TestMissingFeaturesBlockWarning:
    """The keyword check reports when it has no text to scan."""

    def test_no_block_and_no_title_is_reported(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _check_reject_keywords

        db = Database(str(tmp_path / "kw2.db"))
        db.record_pending(slug="plain-game", game_title="Plain Game", platform="pc")
        captured: list[str] = []

        from loguru import logger as loguru_logger

        sink = loguru_logger.add(lambda msg: captured.append(str(msg)), level="WARNING", format="{message}")
        try:
            with patch(
                "gamarr.pipeline._fetch_fitgirl_page_content",
                return_value=(None, "Download Mirrors boilerplate only"),
            ):
                skipped = _check_reject_keywords(
                    db,
                    {"url": "https://fitgirl-repacks.site/plain-game/"},
                    "Plain Game",
                    "plain-game",
                    ["hv"],
                )
        finally:
            loguru_logger.remove(sink)
        db.close()
        assert skipped is False
        assert any("neither a Repack Features block nor a page title" in line for line in captured), captured


class TestExpiryRefreshedOnlyOnQualification:
    """A continuously-verified game must not have its window pushed forward forever."""

    def test_recheck_does_not_extend_an_existing_expiry(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        from gamarr.database import Database
        from gamarr.pipeline import _process_verify_result

        db = Database(str(tmp_path / "expiry.db"))
        db.record_pending(
            slug="keeps-passing",
            game_title="Keeps Passing",
            platform="pc",
            metascore=90.0,
            score_checks_passed=True,
        )
        later = "2999-01-01T00:00:00+00:00"
        with db._session() as session:  # noqa: SLF001 - pinning the stored expiry
            from gamarr.database import PendingGame

            row = session.get(PendingGame, "keeps-passing")
            assert row is not None
            row.expires_at = later
            session.commit()

        game = db.get_pending()[0]
        result = SimpleNamespace(
            metascore=92.0,
            user_score=8.5,
            metascore_review_count=40,
            user_review_count=200,
            genres=[],
        )
        outcome = _process_verify_result(
            db,
            game,
            result,
            {
                "min_criticscore": 80.0,
                "min_criticscore_reviews": 5,
                "min_user_score": 8.0,
                "min_user_reviews": 5,
            },
            fitgirl_max_queue_days=60,
        )
        after = db.get_pending()[0]
        db.close()
        assert outcome == 1
        assert after.expires_at == later

    def test_first_qualification_extends_the_window(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        from gamarr.database import Database
        from gamarr.pipeline import _process_verify_result

        db = Database(str(tmp_path / "expiry2.db"))
        import datetime

        # Tomorrow: still queued, but inside the 60-day window the check refreshes.
        soon = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=1)).isoformat()
        db.record_pending(
            slug="freshly-verified",
            game_title="Freshly Verified",
            platform="pc",
            expires_at=soon,
        )
        before = db.get_pending()[0]
        game = db.get_pending()[0]
        result = SimpleNamespace(
            metascore=92.0,
            user_score=8.5,
            metascore_review_count=40,
            user_review_count=200,
            genres=[],
        )
        outcome = _process_verify_result(
            db,
            game,
            result,
            {
                "min_criticscore": 80.0,
                "min_criticscore_reviews": 5,
                "min_user_score": 8.0,
                "min_user_reviews": 5,
            },
            fitgirl_max_queue_days=60,
        )
        after = db.get_pending()[0]
        db.close()
        assert outcome == 1
        assert after.expires_at > before.expires_at


class TestNegationEncoding:
    """Typographic apostrophes and HTML entities must not defeat the negation guard."""

    @staticmethod
    def _features(apostrophe: str) -> str:
        return (
            "Repack Features Based on Steam release This repack doesn"
            f"{apostrophe}t include an HV version Screenshots gallery"
        )

    def test_ascii_apostrophe_negates(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match(self._features("'"), ["hv"]) is None

    def test_typographic_apostrophe_negates(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match(self._features("\u2019"), ["hv"]) is None

    def test_generic_contraction_negates(self) -> None:
        from gamarr.pipeline import _keyword_match

        text = "Repack Features This repack can't use HV code Screenshots gallery"
        assert _keyword_match(text, ["hv"]) is None

    def test_html_entity_is_decoded_before_scanning(self) -> None:
        from unittest.mock import patch

        from gamarr.pipeline import _fetch_fitgirl_page_content

        html = (
            "<html><head><title>Some Game</title></head><body><article>"
            "Repack Features This repack doesn&#8217;t include an HV version"
            "</article></body></html>"
        )
        with patch("gamarr.pipeline.requests.get") as mock_get:
            mock_get.return_value.text = html
            mock_get.return_value.raise_for_status.return_value = None
            _title, article = _fetch_fitgirl_page_content("https://fitgirl-repacks.site/some-game/")
        assert article is not None
        assert "&#8217;" not in article
        assert "doesn\u2019t include an HV version" in article


class TestNegativePageBudget:
    """A negative budget must fall back to the default, not become unbounded."""

    def test_negative_max_pages_uses_the_default_budget(self) -> None:
        from gamarr.pipeline import _DEFAULT_MAX_PAGES, _normalise_latest_depth

        assert _normalise_latest_depth(-5, "backlog") == _DEFAULT_MAX_PAGES
        assert _normalise_latest_depth(-5, "latest") == _DEFAULT_MAX_PAGES

    def test_zero_still_clamps_in_latest_mode(self) -> None:
        from gamarr.pipeline import _normalise_latest_depth

        assert _normalise_latest_depth(0, "latest") == 10
        assert _normalise_latest_depth(None, "backlog") is None


class TestCancelWhileQueueing:
    """A cancel landing after the lookup pool drains must stop the insert loop."""

    def test_cancel_between_lookup_and_insert_queues_nothing(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "cancel.db"))

        def _browse(slug: str, title: str, score: float) -> dict:
            # The cheap pre-filter reads the browse fields, not the detail scores.
            return {
                "title": title,
                "slug": slug,
                "score": score,
                "user_rating": 8.7,
                "critic_review_count": 50,
                "user_review_count": 500,
            }

        candidates = [_browse("game-one", "Game One", 90), _browse("game-two", "Game Two", 88)]
        from types import SimpleNamespace

        def _passing() -> SimpleNamespace:
            return SimpleNamespace(
                metascore=90.0,
                metascore_review_count=50,
                user_score=8.7,
                user_review_count=500,
                genres=[],
                must_play=None,
                release_date=None,
                description=None,
            )

        details = [_passing(), _passing()]
        thresholds = {
            "min_criticscore": 80.0,
            "min_criticscore_reviews": 5,
            "min_user_score": 8.0,
            "min_user_reviews": 5,
        }

        class _CancelAfterDrain:
            """Cancelled only once the lookups have been consumed."""

            def __init__(self) -> None:
                self.calls = 0

            def is_set(self) -> bool:
                self.calls += 1
                return self.calls > 2

        with patch("gamarr.pipeline._lookup_details", return_value=details):
            queued = _process_browse_games(
                candidates,
                "pc",
                db,
                thresholds,
                max_queue_days=30,
                mc=MagicMock(),
                cancel_event=_CancelAfterDrain(),
            )
        remaining = [row.slug for row in db.get_pending()]
        db.close()
        assert queued == 1, "the cancel must stop the insert loop after the first candidate"
        assert remaining == ["game-one"], f"only the already-inserted row may remain: {remaining}"


class TestVerifyBeforeQueueUsesTheMatchingWindow:
    """A game queued already-verified must get the source-matching window."""

    def test_insert_uses_fitgirl_window_not_the_metacritic_one(self, tmp_path: Path) -> None:
        import datetime
        from types import SimpleNamespace
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import run_acquisition

        detail = SimpleNamespace(
            metascore=95.0,
            metascore_review_count=100,
            user_score=9.2,
            user_review_count=1000,
            genres=["Action"],
            must_play=True,
            release_date="2026-11-01",
            description=None,
        )
        browse = [
            {
                "title": "New Release",
                "slug": "new-release",
                "score": 95,
                "user_rating": 9.2,
                "critic_review_count": 100,
                "user_review_count": 1000,
                "release_date": "2026-11-01",
            },
        ]
        mock_mc = MagicMock()
        mock_mc.scan_recent_games.return_value = browse
        mock_mc.lookup_game.return_value = None
        mock_mc._recent_games_last_page = 1
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False

        db_path = str(tmp_path / "window.db")
        with (
            patch("gamarr.pipeline.MetacriticClient", return_value=mock_mc),
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
            patch("gamarr.pipeline._lookup_details", return_value=[detail]),
        ):
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt
            run_acquisition(
                platform="pc",
                db_path=db_path,
                qbt_host="localhost",
                qbt_port=8080,
                mode="latest",
                max_pages=1,
                max_queue_days=1,
                fitgirl_max_queue_days=60,
            )

        db = Database(db_path)
        pending = db.get_pending(platform="pc")
        db.close()
        assert len(pending) == 1, "the verified candidate should be queued"
        expiry = datetime.datetime.fromisoformat(pending[0].expires_at)
        now = datetime.datetime.now(tz=datetime.UTC)
        assert now + datetime.timedelta(days=59) < expiry < now + datetime.timedelta(days=61), (
            f"a match-waiting game needs the 60-day source window, got {expiry}"
        )


class TestMissingFeaturesBlockIsFailClosed:
    """With no features block there is nothing to scope to, so scan the article."""

    def _run(self, tmp_path: Path, article: str) -> tuple[bool, list[str]]:
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _check_reject_keywords

        db = Database(str(tmp_path / "failclosed.db"))
        db.record_pending(slug="no-block", game_title="No Block Game", platform="pc")
        captured: list[str] = []
        from loguru import logger as loguru_logger

        sink = loguru_logger.add(lambda msg: captured.append(str(msg)), level="INFO", format="{message}")
        try:
            with patch(
                "gamarr.pipeline._fetch_fitgirl_page_content",
                return_value=("No Block Game", article),
            ):
                skipped = _check_reject_keywords(
                    db,
                    {"url": "https://fitgirl-repacks.site/no-block/"},
                    "No Block Game",
                    "no-block",
                    ["hv"],
                )
        finally:
            loguru_logger.remove(sink)
        db.close()
        return skipped, captured

    def test_hv_mentioned_without_a_features_block_still_rejects(self, tmp_path: Path) -> None:
        skipped, logs = self._run(
            tmp_path,
            "No Block Game description. HV Bypass (INTEL & AMD) applied by DenuvOwO.",
        )
        assert skipped is True, "a page with no features block must not degrade to title-only"
        assert any("neither a Repack Features block nor a page title" in line for line in logs) or any(
            "no Repack Features block" in line for line in logs
        ), logs

    def test_clean_page_without_a_block_is_still_matched(self, tmp_path: Path) -> None:
        skipped, _logs = self._run(tmp_path, "No Block Game description. Nothing special here.")
        assert skipped is False


class TestLatestModeReachesTheFloorYear:
    """A deep newest year must not starve the previous year's listing."""

    @staticmethod
    def _run(tmp_path: Path, *, max_pages: int) -> list[dict[str, Any]]:
        """Run one latest cycle against a client whose years are all deep."""
        from loguru import logger as loguru_logger

        calls: list[dict[str, Any]] = []
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            calls.append(dict(kwargs))
            grant = kwargs.get("max_pages", 0) or 1
            start_page = kwargs.get("start_page", 1)
            mock_mc._recent_games_last_page = start_page + grant - 1
            mock_mc._recent_games_exhausted = False
            mock_mc._recent_games_failed = False
            mock_mc._recent_games_cutoff_reached = False
            mock_mc._recent_games_stop_cutoff = None
            return [{"title": f"G{p}", "slug": f"g-{kwargs.get('year')}-{p}"} for p in range(grant)]

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc._recent_games_stop_cutoff = None
        mock_mc.scan_recent_games.side_effect = fake_scan

        logs: list[str] = []
        sink = loguru_logger.add(lambda msg: logs.append(str(msg)), level="INFO", format="{message}")
        try:
            with (
                patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
                patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
                patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
                patch("gamarr.pipeline._lookup_details", return_value=[]),
            ):
                mock_mc_cls.return_value = mock_mc
                mock_qbt = MagicMock()
                mock_qbt.is_connected.return_value = True
                mock_qbt_cls.return_value = mock_qbt
                run_acquisition(
                    platform="pc",
                    db_path=str(tmp_path / "floor.db"),
                    qbt_host="localhost",
                    qbt_port=8080,
                    mode="latest",
                    max_pages=max_pages,
                )
        finally:
            loguru_logger.remove(sink)
        return calls

    def test_previous_year_is_scanned_when_the_newest_year_is_deep(self, tmp_path: Path) -> None:
        import datetime

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        calls = self._run(tmp_path, max_pages=3)

        years = [call["year"] for call in calls]
        assert current_year in years, years
        assert (current_year - 1) in years, (
            f"the depth budget consumed the newest year and never reached the previous one: {years}"
        )

    def test_a_one_page_depth_still_scans_something(self, tmp_path: Path) -> None:
        calls = self._run(tmp_path, max_pages=1)
        assert calls, "a depth of one must still browse a page"
        assert calls[0]["max_pages"] == 1


class TestExpiryAllowsRediscovery:
    """After expiry a game must be discoverable again — expiry is not a blacklist."""

    @staticmethod
    def _browse_game() -> dict:
        return {
            "title": "Late Repack",
            "slug": "late-repack",
            "score": 90,
            "user_rating": 8.8,
            "critic_review_count": 60,
            "user_review_count": 600,
        }

    @staticmethod
    def _detail() -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            metascore=90.0,
            metascore_review_count=60,
            user_score=8.8,
            user_review_count=600,
            genres=["Action"],
            must_play=None,
            release_date="2026-05-01",
            description=None,
        )

    def test_expired_slug_is_requeued_by_the_next_browse_pass(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "requeue.db"))
        db.record_processed(
            source="metacritic",
            source_title="Late Repack",
            source_url="mc:late-repack",
            game_title="Late Repack",
            result="Expired",
        )
        thresholds = {
            "min_criticscore": 75.0,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        with patch("gamarr.pipeline._lookup_details", return_value=[self._detail()]):
            queued = _process_browse_games(
                [self._browse_game()],
                "pc",
                db,
                thresholds,
                max_queue_days=60,
                mc=MagicMock(),
            )
        pending = db.get_pending()
        db.close()
        assert queued == 1, "a game whose queue entry expired must be re-queueable"
        assert len(pending) == 1

    def test_a_failed_game_is_still_never_requeued(self, tmp_path: Path) -> None:
        """Only Expired is exempt; a rejection stays permanent."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "failed.db"))
        db.record_processed(
            source="metacritic",
            source_title="Late Repack",
            source_url="mc:late-repack",
            game_title="Late Repack",
            result="Failed",
        )
        thresholds = {
            "min_criticscore": 75.0,
            "min_criticscore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        with patch("gamarr.pipeline._lookup_details", return_value=[self._detail()]):
            queued = _process_browse_games(
                [self._browse_game()],
                "pc",
                db,
                thresholds,
                max_queue_days=60,
                mc=MagicMock(),
            )
        db.close()
        assert queued == 0


class TestExpiryRunsWithoutVerifiedPending:
    """A lapsed row must be swept even when nothing verified is waiting."""

    def test_last_lapsed_row_does_not_stay_queued_forever(self, tmp_path: Path) -> None:
        import datetime

        from gamarr.database import Database, HistoryRow

        db_path = str(tmp_path / "zombie.db")
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=1)).isoformat()
        seed = Database(db_path)
        seed.record_pending(
            slug="lapsed-row",
            game_title="Lapsed Row",
            platform="pc",
            metascore=90.0,
            score_checks_passed=True,
            expires_at=past,
        )
        seed.close()

        TestLatestMode._run(tmp_path=tmp_path, max_pages=1, db_path=db_path)

        db = Database(db_path)
        still_pending = db.is_pending("lapsed-row")
        db.close()
        assert still_pending is False, (
            "expiry must run before the match-phase gate, else the last lapsed row never leaves"
        )
        check = Database(db_path)
        with check._session() as session:  # noqa: SLF001 - no public history reader
            results = [row.result for row in session.query(HistoryRow).all()]
        check.close()
        assert "Expired" in results, results


class TestLapsedRowRequeueSameCycle:
    """A game whose window lapsed must be re-queueable in the very same cycle."""

    def test_a_single_cycle_sweeps_and_requeues(self, tmp_path: Path) -> None:
        import datetime
        from types import SimpleNamespace
        from unittest.mock import patch

        from loguru import logger as loguru_logger

        from gamarr.database import Database
        from gamarr.pipeline import run_acquisition

        db_path = str(tmp_path / "same_cycle.db")
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=1)).isoformat()
        seed = Database(db_path)
        seed.record_pending(
            slug="g",
            game_title="G",
            platform="pc",
            metascore=40.0,
            score_checks_passed=True,
            expires_at=past,
        )
        seed.close()

        detail = SimpleNamespace(
            metascore=91.0,
            metascore_review_count=70,
            user_score=8.9,
            user_review_count=700,
            genres=["Action"],
            must_play=None,
            release_date="2026-05-01",
            description=None,
        )
        mock_mc = MagicMock()
        mock_mc.scan_recent_games.return_value = [
            {
                "title": "G",
                "slug": "g",
                "score": 91,
                "user_rating": 8.9,
                "critic_review_count": 70,
                "user_review_count": 700,
                "release_date": "2026-05-01",
            },
        ]
        mock_mc.lookup_game.return_value = None
        mock_mc._recent_games_last_page = 1
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False

        sink = loguru_logger.add(lambda _msg: None, level="INFO")
        try:
            with (
                patch("gamarr.pipeline.MetacriticClient", return_value=mock_mc),
                patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
                patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
                patch("gamarr.pipeline._lookup_details", return_value=[detail]),
            ):
                mock_qbt = MagicMock()
                mock_qbt.is_connected.return_value = True
                mock_qbt_cls.return_value = mock_qbt
                run_acquisition(
                    platform="pc",
                    db_path=db_path,
                    qbt_host="localhost",
                    qbt_port=8080,
                    mode="latest",
                    max_pages=1,
                )
        finally:
            loguru_logger.remove(sink)

        db = Database(db_path)
        pending = db.get_pending()
        db.close()
        assert [row.slug for row in pending] == ["g"], (
            "one cycle must sweep the lapsed row and re-queue it with fresh scores"
        )
        assert pending[0].metascore == 91.0


class TestFitgirlUrlHostCheck:
    """TLS verification is disabled for FitGirl, so the host must match exactly."""

    def test_prefix_test_would_have_accepted_the_lookalike(self) -> None:
        """The reviewed bug: a prefix test accepts a suffix-domain lookalike."""
        lookalike = "https://fitgirl-repacks.site.evil.example/game/"
        assert lookalike.startswith("https://fitgirl-repacks.site") is True

    def test_accepts_the_real_host(self) -> None:
        from gamarr.pipeline import _is_fitgirl_url

        assert _is_fitgirl_url("https://fitgirl-repacks.site/game/") is True
        assert _is_fitgirl_url("https://www.fitgirl-repacks.site/game/") is True

    def test_rejects_a_prefix_lookalike(self) -> None:
        from gamarr.pipeline import _is_fitgirl_url

        assert _is_fitgirl_url("https://fitgirl-repacks.site.evil.example/game/") is False, (
            "a prefix match would fetch an attacker host with verify=False"
        )
        assert _is_fitgirl_url("https://evil.example/fitgirl-repacks.site") is False
        assert _is_fitgirl_url("not a url") is False
