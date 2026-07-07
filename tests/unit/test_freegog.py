"""Tests for gamarr FreeGOG source."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class TestFreeGOGSource:
    """FreeGOGSource construction and protocol conformance."""

    def test_implements_base_source(self) -> None:
        from gamarr.sources import BaseSource
        from gamarr.sources.freegog import FreeGOGSource

        source = FreeGOGSource()
        assert isinstance(source, BaseSource)

    def test_source_name(self) -> None:
        from gamarr.sources.freegog import FreeGOGSource

        source = FreeGOGSource()
        assert source.source_name == "freegog"

    def test_platform(self) -> None:
        from gamarr.sources.freegog import FreeGOGSource

        source = FreeGOGSource(platform="pc")
        assert source.platform == "pc"

    def test_accepts_shared_database(self) -> None:
        from gamarr.database import Database
        from gamarr.sources.freegog import FreeGOGSource

        shared_db = Database(":memory:")
        source = FreeGOGSource(db=shared_db)
        assert source._db is shared_db
        source.close()
        shared_db.close()


class TestCleanFreeGOGTitle:
    """Title cleaning for FreeGOG titles."""

    def test_clean_gothic_1_remake(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Gothic 1 Remake v1.0.2a")
        assert result == "Gothic 1 Remake"

    def test_clean_sea_of_stars(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Sea of Stars: Sunset Edition v3.0.60151 +3DLC")
        assert result == "Sea of Stars"

    def test_clean_kena_bridge_of_spirits(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Kena: Bridge of Spirits 2022(rc3)")
        assert result == "Kena: Bridge of Spirits"

    def test_clean_blades_of_fire(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Blades of Fire v2.0.0.10")
        assert result == "Blades of Fire"

    def test_clean_elden_ring(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Elden Ring")
        assert result == "Elden Ring"

    def test_clean_empty_string(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("")
        assert result == ""

    def test_clean_edition_with_en_dash(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Cyberpunk 2077 \u2013 Complete Edition")
        assert result == "Cyberpunk 2077"

    def test_clean_preserves_colon_in_name(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Kena: Bridge of Spirits")
        assert result == "Kena: Bridge of Spirits"

    def test_clean_dlc_only(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Some Game + DLCs")
        assert result == "Some Game"

    def test_clean_already_clean_title(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Half-Life 2")
        assert result == "Half-Life 2"


class TestParseFreeGOGAZPage:
    """FreeGOG A-Z page HTML parser."""

    def test_parse_multiple_entries(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_page

        # Real site structure: <section class="gd-az-section"> with <a><span>TITLE</span></a>
        html = """<section id="gd-az-a" class="gd-az-section" data-gd-az-section>
    <a href="https://freegogpcgames.com/33511/gothic-1-remake/"><span>Gothic 1 Remake v1.0.2a</span></a>
    <a href="https://freegogpcgames.com/12345/sea-of-stars/"><span>Sea of Stars: Sunset Edition v3.0.60151 +3DLC</span></a>
</section>
<section id="gd-az-b" class="gd-az-section" data-gd-az-section>
    <a href="https://freegogpcgames.com/67890/kena/"><span>Kena: Bridge of Spirits 2022(rc3)</span></a>
</section>"""
        result = _parse_freegog_az_page(html)
        assert len(result) == 3
        assert result[0]["title"] == "Gothic 1 Remake"
        assert result[0]["url"] == "https://freegogpcgames.com/33511/gothic-1-remake/"
        assert result[1]["title"] == "Sea of Stars"
        assert result[2]["title"] == "Kena: Bridge of Spirits"

    def test_parse_includes_letter_section(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_page

        html = """<section id="gd-az-a" class="gd-az-section" data-gd-az-section>
    <a href="https://freegogpcgames.com/1/game-a/"><span>Game A v1.0</span></a>
</section>
<section id="gd-az-b" class="gd-az-section" data-gd-az-section>
    <a href="https://freegogpcgames.com/2/game-b/"><span>Game B v1.0</span></a>
</section>"""
        result = _parse_freegog_az_page(html)
        assert len(result) == 2
        assert result[0]["letter"] == "a"
        assert result[1]["letter"] == "b"

    def test_parse_empty_page(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_page

        result = _parse_freegog_az_page("")
        assert result == []

    def test_parse_uses_cleaned_titles(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_page

        html = """<section id="gd-az-g" class="gd-az-section" data-gd-az-section>
    <a href="https://freegogpcgames.com/33511/gothic-1-remake/"><span>Gothic 1 Remake v1.0.2a</span></a>
</section>"""
        result = _parse_freegog_az_page(html)
        assert len(result) == 1
        # Title should be cleaned, not the raw "Gothic 1 Remake v1.0.2a"
        assert result[0]["title"] == "Gothic 1 Remake"

    def test_parse_decodes_html_entities(self) -> None:
        """HTML entities like &#039; in titles should be decoded before cleaning."""
        from gamarr.sources.freegog import _parse_freegog_az_page
        from gamarr.utils import normalise_for_compare

        html = """<section id="gd-az-b" class="gd-az-section" data-gd-az-section>
    <a href="https://freegogpcgames.com/5977/27-baldurs-gate-3/"><span>Baldur&#039;s Gate 3 v4.1.1.7209685 + 2 DLC</span></a>
</section>"""
        result = _parse_freegog_az_page(html)
        assert len(result) == 1
        parsed_title = result[0]["title"]
        # The cleaned title should have the apostrophe decoded, not the raw entity
        assert "&#039;" not in parsed_title, f"HTML entity should be decoded, got: {parsed_title!r}"
        # The normalized form should match the canonical game name
        norm_metacritic = normalise_for_compare("Baldur's Gate 3")
        norm_parsed = normalise_for_compare(parsed_title)
        assert norm_parsed == norm_metacritic, f"Normalised title '{norm_parsed}' should match '{norm_metacritic}'"


class TestExtractMagnetFromFreeGOGPage:
    """Magnet extraction from FreeGOG game pages."""

    ENCODED_MAGNET = (
        "bWFnbmV0Oj94dD11cm46YnRpaDo0Mjc4OEFGQjEwNzE0MzQ4OENCNTc2MDg1QkQ1QTVERUYxNjVGQzM3"
        "JnRyPWh0dHAlM0ElMkYlMkZidDMudC1ydS5vcmclMkZhbm4lM0ZtYWduZXQmZG49JTVCREwlNUQlMjBH"
        "b3RoaWMlMjAxJTIwUmVtYWtl"
    )

    def test_extract_real_magnet(self) -> None:
        from gamarr.sources.freegog import _extract_magnet_from_freegog_page

        html = (
            '<a href="https://gdl.freegogpcgames.xyz/download-gen.php?url=v1.'
            f'{self.ENCODED_MAGNET}.dummy123" data-type="magnet">Magnet</a>'
        )
        result = _extract_magnet_from_freegog_page(html)
        assert result is not None
        assert result.startswith("magnet:")

    def test_extract_no_magnet(self) -> None:
        from gamarr.sources.freegog import _extract_magnet_from_freegog_page

        html = "<p>No magnet here</p>"
        result = _extract_magnet_from_freegog_page(html)
        assert result is None

    def test_extract_empty_html(self) -> None:
        from gamarr.sources.freegog import _extract_magnet_from_freegog_page

        result = _extract_magnet_from_freegog_page("")
        assert result is None

    def test_extract_bad_base64(self) -> None:
        from gamarr.sources.freegog import _extract_magnet_from_freegog_page

        html = (
            '<a href="https://gdl.freegogpcgames.xyz/download-gen.php?url=v1.'
            '!!!invalid!!!.dummy123" data-type="magnet">Magnet</a>'
        )
        result = _extract_magnet_from_freegog_page(html)
        assert result is None


class TestFreeGOGFetchSitemap:
    """FreeGOG sitemap (A-Z page) indexing."""

    ENCODED_MAGNET = (
        "bWFnbmV0Oj94dD11cm46YnRpaDo0Mjc4OEFGQjEwNzE0MzQ4OENCNTc2MDg1QkQ1QTVERUYxNjVGQzM3"
        "JnRyPWh0dHAlM0ElMkYlMkZidDMudC1ydS5vcmclMkZhbm4lM0ZtYWduZXQmZG49JTVCREwlNUQlMjBH"
        "b3RoaWMlMjAxJTIwUmVtYWtl"
    )

    def test_indexes_new_games(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(str(tmp_path / "test.db"))
        source = FreeGOGSource(db=db, cache_pages_hours=0)

        az_html = """<section id="gd-az-g" class="gd-az-section" data-gd-az-section>
    <a href="https://freegogpcgames.com/33511/gothic-1-remake/"><span>Gothic 1 Remake v1.0.2a</span></a>
</section>"""
        game_html = (
            '<a href="https://gdl.freegogpcgames.xyz/download-gen.php?url=v1.'
            f'{self.ENCODED_MAGNET}.dummy123" data-type="magnet">Magnet</a>'
        )

        with patch("gamarr.sources.freegog.requests.get") as mock_get:

            def side_effect(url: str, **kwargs: object) -> MagicMock:
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                if "game-list" in url:
                    resp.text = az_html
                else:
                    resp.text = game_html
                return resp

            mock_get.side_effect = side_effect

            source.fetch_sitemap(db)

        titles = db.get_all_source_titles("freegog")
        assert len(titles) == 1
        assert titles[0]["title"] == "Gothic 1 Remake"
        assert titles[0]["url"] == "https://freegogpcgames.com/33511/gothic-1-remake/"
        assert titles[0]["magnet"] is not None
        assert titles[0]["magnet"].startswith("magnet:")

        source.close()
        db.close()

    def test_skips_known_games(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database, SourceTitle
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(str(tmp_path / "test.db"))
        source = FreeGOGSource(db=db, cache_pages_hours=0)

        # Pre-populate a known title with a real magnet
        with db._session() as session:
            session.add(
                SourceTitle(
                    source="freegog",
                    title="Existing Game",
                    url="https://freegogpcgames.com/00000/existing-game/",
                    magnet="magnet:?xt=urn:btih:existing",
                )
            )
            session.commit()

        az_html = """<section id="gd-az-g" class="gd-az-section" data-gd-az-section>
    <a href="https://freegogpcgames.com/00000/existing-game/"><span>Existing Game v1.0</span></a>
    <a href="https://freegogpcgames.com/33511/gothic-1-remake/"><span>Gothic 1 Remake v1.0.2a</span></a>
</section>"""
        game_html = (
            '<a href="https://gdl.freegogpcgames.xyz/download-gen.php?url=v1.'
            f'{self.ENCODED_MAGNET}.dummy123" data-type="magnet">Magnet</a>'
        )

        with patch("gamarr.sources.freegog.requests.get") as mock_get:
            call_count = 0

            def side_effect(url: str, **kwargs: object) -> MagicMock:
                nonlocal call_count
                call_count += 1
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                if "game-list" in url:
                    resp.text = az_html
                else:
                    resp.text = game_html
                return resp

            mock_get.side_effect = side_effect

            source.fetch_sitemap(db)

        # A-Z page (1 call) + 1 new game page = 2 calls
        assert call_count == 2, f"Expected 2 HTTP requests, got {call_count}"

        # Should have the existing + one new title
        titles = db.get_all_source_titles("freegog")
        assert len(titles) == 2

        source.close()
        db.close()

    def test_re_fetches_missing_magnet(self, tmp_path: Path) -> None:
        """Entries with magnet=None should be re-fetched, not skipped."""
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database, SourceTitle
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(str(tmp_path / "test.db"))
        source = FreeGOGSource(db=db, cache_pages_hours=0)

        # Pre-populate an entry with magnet=None (broken from earlier indexing)
        with db._session() as session:
            session.add(
                SourceTitle(
                    source="freegog",
                    title="Existing Game",
                    url="https://freegogpcgames.com/00000/existing-game/",
                    magnet=None,
                )
            )
            session.commit()

        az_html = """<section id="gd-az-g" class="gd-az-section" data-gd-az-section>
    <a href="https://freegogpcgames.com/00000/existing-game/"><span>Existing Game v1.0</span></a>
</section>"""
        game_html = (
            '<a href="https://gdl.freegogpcgames.xyz/download-gen.php?url=v1.'
            f'{self.ENCODED_MAGNET}.dummy123" data-type="magnet">Magnet</a>'
        )

        with patch("gamarr.sources.freegog.requests.get") as mock_get:
            call_count = 0

            def side_effect(url: str, **kwargs: object) -> MagicMock:
                nonlocal call_count
                call_count += 1
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                if "game-list" in url:
                    resp.text = az_html
                else:
                    resp.text = game_html
                return resp

            mock_get.side_effect = side_effect
            source.fetch_sitemap(db)

        # A-Z page (1) + re-fetch of game page (1) = 2 calls
        assert call_count == 2, f"Expected 2 HTTP requests, got {call_count}"

        # The entry should now have a magnet
        titles = db.get_all_source_titles("freegog")
        assert len(titles) == 1
        assert titles[0]["magnet"] is not None, "magnet should be re-fetched"
        assert titles[0]["magnet"].startswith("magnet:")

        source.close()
        db.close()

    def test_cache_hit_skips(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from gamarr.database import Database, SourceTitle
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(str(tmp_path / "test.db"))
        source = FreeGOGSource(db=db, cache_pages_hours=6)

        # Pre-populate cache and titles
        db.set_sitemap_cache("freegog")
        with db._session() as session:
            session.add(
                SourceTitle(
                    source="freegog",
                    title="Existing Game",
                    url="https://freegogpcgames.com/00000/existing-game/",
                    magnet=None,
                )
            )
            session.commit()

        with patch("gamarr.sources.freegog.requests.get") as mock_get:
            source.fetch_sitemap(db)
            mock_get.assert_not_called()

        source.close()
        db.close()

    def test_handles_az_failure(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        import requests

        from gamarr.database import Database
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(str(tmp_path / "test.db"))
        source = FreeGOGSource(db=db, cache_pages_hours=0)

        with patch(
            "gamarr.sources.freegog.requests.get",
            side_effect=requests.exceptions.ConnectionError("nope"),
        ):
            source.fetch_sitemap(db)

        # Cache should be set even on failure (prevents retry loop)
        assert db.get_sitemap_cache("freegog", 6) is True
        # No titles should be stored
        assert len(db.get_all_source_titles("freegog")) == 0

        source.close()
        db.close()

    def test_skips_on_cancel(self) -> None:
        import threading
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(":memory:")
        source = FreeGOGSource(db=db, cache_pages_hours=0)

        cancel_event = threading.Event()
        cancel_event.set()

        with patch("gamarr.sources.freegog.requests.get") as mock_get:
            source.fetch_sitemap(db, cancel_event=cancel_event)
            mock_get.assert_not_called()

        source.close()
        db.close()

    def test_accepts_cancel_event_kwarg(self) -> None:
        import threading
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(":memory:")
        source = FreeGOGSource(db=db, cache_pages_hours=0)

        cancel_event = threading.Event()

        with patch("gamarr.sources.freegog.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = ""
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            # Should not raise TypeError
            source.fetch_sitemap(db, cancel_event=cancel_event)

        source.close()
        db.close()

    def test_logs_batch_progress_instead_of_per_letter(self, tmp_path: Path) -> None:
        """Should log batch progress every 500 entries, not per-letter."""
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database, SourceTitle
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(str(tmp_path / "test.db"))
        source = FreeGOGSource(db=db, cache_pages_hours=0)

        # Create 550 known entries across 2 letters (260 in A, 290 in B)
        total_entries = 550
        entries_a = 260
        entries_b = total_entries - entries_a

        def _make_html(letter: str, count: int, start_id: int = 0) -> str:
            section_id = f"gd-az-{letter}"
            links = []
            for i in range(count):
                game_id = start_id + i
                links.append(
                    f'    <a href="https://freegogpcgames.com/{game_id:05d}/game-{i}/">'
                    f"<span>Game {letter.upper()}{i} v1.0</span></a>"
                )
            return (
                f'<section id="{section_id}" class="gd-az-section" data-gd-az-section">\n'
                + "\n".join(links)
                + "\n</section>"
            )

        az_html = _make_html("a", entries_a) + "\n" + _make_html("b", entries_b, start_id=entries_a)

        # Pre-populate all entries as known (with magnets) so they get skipped
        with db._session() as session:
            for letter, count, offset in [("a", entries_a, 0), ("b", entries_b, entries_a)]:
                for i in range(count):
                    game_id = offset + i
                    session.add(
                        SourceTitle(
                            source="freegog",
                            title=f"Game {letter.upper()}{i}",
                            url=f"https://freegogpcgames.com/{game_id:05d}/game-{i}/",
                            magnet=f"magnet:?xt=urn:btih:{game_id:040d}",
                        )
                    )
            session.commit()

        with (
            patch("gamarr.sources.freegog.logger") as mock_logger,
            patch("gamarr.sources.freegog.requests.get") as mock_get,
        ):
            mock_resp = MagicMock()
            mock_resp.text = az_html
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            source.fetch_sitemap(db)

        # Collect all info-level log calls
        info_calls = mock_logger.info.call_args_list

        # ── OLD per-letter messages MUST be absent ──
        for call in info_calls:
            fmt = str(call.args[0])
            assert "letter '" not in fmt, f"Per-letter log should be absent, found: {fmt}"
            assert "complete (" not in fmt, f"Per-letter complete log should be absent, found: {fmt}"

        # ── NEW batch progress messages MUST be absent (removed) ──
        batch_calls = [call for call in info_calls if "entries" in str(call.args[0]) and "of" in str(call.args[0])]
        assert len(batch_calls) == 0, f"Batch progress logs should have been removed, found: {batch_calls}"

        # ── Summary MUST still be present (either "new games found" or "all already known") ──
        summary_calls = [call for call in info_calls if "already known" in str(call.args[0])]
        assert len(summary_calls) == 1, f"Expected one summary log, got: {summary_calls}"

        source.close()
        db.close()
