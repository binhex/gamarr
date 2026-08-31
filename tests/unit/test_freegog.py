"""Tests for gamarr FreeGOG source."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

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
        from unittest.mock import patch

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

        with (
            patch("gamarr.sources.freegog._sb_fetch_with_browser") as mock_get,
            patch("gamarr.sources.freegog._fetch_freegog_az_entries", return_value=None),
            patch(
                "gamarr.sources.freegog._default_sb_factory",
                lambda: _FakeSession(_FakeBrowser(az_html=az_html)),
            ),
        ):

            def side_effect(_sb: object, url: str, **kwargs: object) -> str:
                if "game-list" in url:
                    return az_html
                return game_html

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
        from unittest.mock import patch

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

        with (
            patch("gamarr.sources.freegog._sb_fetch_with_browser") as mock_get,
            patch("gamarr.sources.freegog._fetch_freegog_az_entries", return_value=None),
            patch(
                "gamarr.sources.freegog._default_sb_factory",
                lambda: _FakeSession(_FakeBrowser(az_html=az_html)),
            ),
        ):
            call_count = 0

            def side_effect(_sb: object, url: str, **kwargs: object) -> str:
                nonlocal call_count
                call_count += 1
                if "game-list" in url:
                    return az_html
                return game_html

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
        from unittest.mock import patch

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

        with (
            patch("gamarr.sources.freegog._sb_fetch_with_browser") as mock_get,
            patch("gamarr.sources.freegog._fetch_freegog_az_entries", return_value=None),
            patch(
                "gamarr.sources.freegog._default_sb_factory",
                lambda: _FakeSession(_FakeBrowser(az_html=az_html)),
            ),
        ):
            call_count = 0

            def side_effect(_sb: object, url: str, **kwargs: object) -> str:
                nonlocal call_count
                call_count += 1
                if "game-list" in url:
                    return az_html
                return game_html

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

        with patch("gamarr.sources.freegog._sb_fetch_with_browser") as mock_get:
            source.fetch_sitemap(db)
            mock_get.assert_not_called()

        source.close()
        db.close()

    def test_handles_az_failure(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(str(tmp_path / "test.db"))
        source = FreeGOGSource(db=db, cache_pages_hours=0)

        with (
            patch(
                "gamarr.sources.freegog._sb_fetch_with_browser",
                side_effect=Exception("Connection error"),
            ),
            patch("gamarr.sources.freegog._fetch_freegog_az_entries", return_value=None),
            patch(
                "gamarr.sources.freegog._default_sb_factory",
                lambda: _FakeSession(_FakeBrowser(az_html="")),
            ),
        ):
            source.fetch_sitemap(db)

        # Cache should NOT be set on failure — transient errors should retry next cycle.
        assert db.get_sitemap_cache("freegog", 6) is False
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

        with patch("gamarr.sources.freegog._sb_fetch_with_browser") as mock_get:
            source.fetch_sitemap(db, cancel_event=cancel_event)
            mock_get.assert_not_called()

        source.close()
        db.close()

    def test_accepts_cancel_event_kwarg(self) -> None:
        import threading
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(":memory:")
        source = FreeGOGSource(db=db, cache_pages_hours=0)

        cancel_event = threading.Event()

        with (
            patch("gamarr.sources.freegog._sb_fetch_with_browser") as mock_get,
            patch("gamarr.sources.freegog._fetch_freegog_az_entries", return_value=None),
            patch(
                "gamarr.sources.freegog._default_sb_factory",
                lambda: _FakeSession(_FakeBrowser(az_html="")),
            ),
        ):
            mock_get.return_value = ""
            # Should not raise TypeError
            source.fetch_sitemap(db, cancel_event=cancel_event)

        source.close()
        db.close()

    def test_logs_batch_progress_instead_of_per_letter(self, tmp_path: Path) -> None:
        """Should log batch progress every 500 entries, not per-letter."""
        from unittest.mock import patch

        from gamarr.database import Database, SourceTitle
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(str(tmp_path / "test.db"))
        source = FreeGOGSource(db=db, cache_pages_hours=0)

        # The HTML fixture contains KNOWN_ENTRIES known entries plus
        # NEW_ENTRIES new ones (553 links in total) so the progress-log call
        # site is actually reached.
        known_entries = 550
        new_entries = 3
        entries_a = 260
        entries_b = known_entries - entries_a + new_entries

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

        # Pre-populate all but the last NEW_ENTRIES entries as known (with
        # magnets) so they get skipped; the new ones exercise the fetch path.
        with db._session() as session:
            for letter, count, offset in [
                ("a", entries_a, 0),
                ("b", entries_b - new_entries, entries_a),
            ]:
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
            patch("gamarr.sources.freegog._sb_fetch_with_browser") as mock_get,
            patch("gamarr.sources.freegog._fetch_freegog_az_entries", return_value=None),
            patch(
                "gamarr.sources.freegog._default_sb_factory",
                lambda: _FakeSession(_FakeBrowser(az_html=az_html)),
            ),
        ):
            mock_get.return_value = az_html

            source.fetch_sitemap(db)

        # Collect all info-level log calls
        info_calls = mock_logger.info.call_args_list

        # ── OLD per-letter messages MUST be absent ──
        for call in info_calls:
            fmt = str(call.args[0])
            assert "letter '" not in fmt, f"Per-letter log should be absent, found: {fmt}"
            assert "complete (" not in fmt, f"Per-letter complete log should be absent, found: {fmt}"

        # ── Summary MUST still be present (either "new games indexed" or "all already known") ──
        summary_calls = [
            call
            for call in info_calls
            if "already known" in str(call.args[0]) or "new games indexed" in str(call.args[0])
        ]
        assert len(summary_calls) == 1, f"Expected one summary log, got: {summary_calls}"

        source.close()
        db.close()


class _FakeDriver:
    """Minimal driver stub for asserting page-load timeout wiring."""

    def __init__(self) -> None:
        self.page_load_timeouts: list[float] = []

    def set_page_load_timeout(self, seconds: float) -> None:
        self.page_load_timeouts.append(seconds)


class _FakeBrowser:
    """SeleniumBase-SB-shaped fake used to exercise fetch/recycle logic."""

    def __init__(
        self,
        *,
        az_html: str,
        fail_game_pages: int = 0,
        hang: bool = False,
        hang_az: bool = False,
    ) -> None:
        self.az_html = az_html
        self.fail_game_pages = fail_game_pages
        self.hang = hang
        self.hang_az = hang_az
        self.driver = _FakeDriver()
        self.quit_called = False
        self.game_page_gets = 0
        self.game_html = "<html>no magnet</html>"
        self._html = az_html

    def get(self, url: str) -> None:
        if url.endswith("/game-list/"):
            return
        self.game_page_gets += 1
        if self.hang:
            import time

            time.sleep(5)  # simulate a wedged WebDriver command
        if self.game_page_gets <= self.fail_game_pages:
            raise RuntimeError("simulated page fetch failure")
        self._html = self.game_html

    def uc_open_with_reconnect(self, url: str, count: int) -> None:
        del count
        if url.endswith("/game-list/"):
            if self.hang_az:
                import time

                time.sleep(5)  # simulate a wedged A-Z page navigation
            return
        self.game_page_gets += 1

    def uc_gui_click_captcha(self) -> None:
        pass

    def get_page_source(self) -> str:
        return self._html

    def quit(self) -> None:
        self.quit_called = True


class _FakeSession:
    """Context-manager wrapper around _FakeBrowser (mimics `with SB() as sb:`)."""

    def __init__(self, browser: _FakeBrowser) -> None:
        self.browser = browser
        self.exited = False

    def __enter__(self) -> _FakeBrowser:
        return self.browser

    def __exit__(self, *exc: object) -> None:
        self.exited = True


def _az_html_for(entries: list[tuple[str, str]]) -> str:
    """Build A-Z page HTML parseable by _parse_freegog_az_page."""
    links = "".join(f'<a href="{url}"><span>{title}</span></a>' for title, url in entries)
    return f'<section id="gd-az-a" class="gd-az-section">{links}</section>'


class TestFreeGOGFetchHardening:
    """Hardening against a wedged browser freezing the whole pipeline.

    Regression: on 2026-08-15 a single hung FreeGOG page fetch
    (sb.get() with no timeout) blocked the acquisition job forever;
    max_instances=1 froze acquisition for 13 days.
    """

    def test_hung_game_page_fetch_returns_timeout_status(
        self, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            monkeypatch.setattr(freegog, "_FETCH_TIMEOUT_SECONDS", 0.3)
            browser = _FakeBrowser(az_html="", hang=True)
            entry = {"title": "Hung Game", "url": "https://freegogpcgames.com/999/hung-game/"}

            import time

            start = time.monotonic()
            source = freegog.FreeGOGSource(db=db)
            outcome = source._process_az_entry(db, entry, browser, {})
            elapsed = time.monotonic() - start

            assert outcome == "timeout", "hung page fetch must be classified as a timeout"
            assert elapsed < 5, f"fetch did not abort promptly, took {elapsed:.1f}s"
            assert db.get_all_source_titles("freegog") == [], "nothing should be stored for a hung page"
        finally:
            db.close()

    def test_fetch_and_store_game_success_with_magnet(self, tmp_db_path: Path) -> None:
        import base64

        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            magnet = "magnet:?xt=urn:btih:deadbeef"
            encoded = base64.urlsafe_b64encode(magnet.encode()).decode().rstrip("=")
            browser = _FakeBrowser(az_html="")
            browser.game_html = f'<a href="https://gdl.freegogpcgames.xyz/download-gen.php?url=v1.{encoded}.sig">dl</a>'

            entry = {"title": "Good Game", "url": "https://freegogpcgames.com/100/good-game/"}
            result = freegog.FreeGOGSource._fetch_and_store_game(db, entry, browser, timeout_seconds=5)

            assert result is True
            rows = db.get_all_source_titles("freegog")
            assert len(rows) == 1
            assert rows[0]["magnet"] == magnet
        finally:
            db.close()

    def test_session_recycled_after_consecutive_failures(self, tmp_db_path: Path) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            entries = [
                ("Game One", "https://freegogpcgames.com/101/game-one/"),
                ("Game Two", "https://freegogpcgames.com/102/game-two/"),
                ("Game Three", "https://freegogpcgames.com/103/game-three/"),
                ("Game Four", "https://freegogpcgames.com/104/game-four/"),
            ]
            az_html = _az_html_for(entries)

            sessions: list[_FakeSession] = []

            def factory() -> _FakeSession:
                browser = _FakeBrowser(
                    az_html=az_html,
                    fail_game_pages=2 if len(sessions) == 0 else 0,
                )
                session = _FakeSession(browser)
                sessions.append(session)
                return session

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory)

            # Two sessions total: first wedges after 2 failures, second finishes.
            assert len(sessions) == 2, f"expected session recycle, got {len(sessions)} sessions"
            assert sessions[0].exited is True
            assert sessions[1].exited is True

            # The first session must have actually quit before the next was opened.
            assert sessions[0].browser.quit_called is True

            # Remaining pages must have been indexed via the fresh session.
            rows = db.get_all_source_titles("freegog")
            assert len(rows) == 2, f"expected 2 successes, got {len(rows)}"

            assert sessions[1].browser.game_page_gets == 2
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is True
        finally:
            db.close()

    def test_page_load_timeout_applied_per_session(self, tmp_db_path: Path) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            entries = [("Only Game", "https://freegogpcgames.com/105/only-game/")]
            az_html = _az_html_for(entries)
            sessions: list[_FakeSession] = []

            def factory() -> _FakeSession:
                browser = _FakeBrowser(az_html=az_html)
                session = _FakeSession(browser)
                sessions.append(session)
                return session

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory)

            assert len(sessions) == 1
            driver = sessions[0].browser.driver
            assert driver.page_load_timeouts, "page-load timeout must be set on the browser driver"
            assert driver.page_load_timeouts[0] > 0
        finally:
            db.close()

    def test_cancel_event_stops_indexing_early(self, tmp_db_path: Path) -> None:
        import threading

        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            entries = [("Game One", f"https://freegogpcgames.com/{i}/slug-{i}/") for i in range(110, 125)]
            az_html = _az_html_for(entries)
            sessions: list[_FakeSession] = []

            def factory() -> _FakeSession:
                browser = _FakeBrowser(az_html=az_html)
                session = _FakeSession(browser)
                sessions.append(session)
                return session

            cancelled = threading.Event()
            cancelled.set()  # signal cancellation before indexing starts

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory, cancel_event=cancelled)

            assert len(sessions) == 1
            assert sessions[0].browser.game_page_gets == 0, "no game pages should be fetched when cancelled"
            assert db.get_all_source_titles("freegog") == []
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is False, (
                "a cancelled run must not mark the sitemap cache valid"
            )
            # The session must still be torn down even though the loop stopped early.
            assert sessions[0].browser.quit_called is True, "the session must be quit on cancellation"
            assert sessions[0].exited is True, "the session context must be exited on cancellation"
        finally:
            db.close()

    def test_timeout_recycles_session_immediately(self, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            monkeypatch.setattr(freegog, "_FETCH_TIMEOUT_SECONDS", 0.3)
            entries = [
                ("Game One", "https://freegogpcgames.com/126/game-one/"),
                ("Game Two", "https://freegogpcgames.com/127/game-two/"),
                ("Game Three", "https://freegogpcgames.com/128/game-three/"),
            ]
            az_html = _az_html_for(entries)
            sessions: list[_FakeSession] = []

            def factory() -> _FakeSession:
                browser = _FakeBrowser(az_html=az_html, hang=len(sessions) == 0)
                session = _FakeSession(browser)
                sessions.append(session)
                return session

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory)

            # A single fetch timeout must recycle the session immediately so the
            # zombie worker cannot race the next fetch on the shared driver.
            assert len(sessions) == 2, f"expected immediate recycle on timeout, got {len(sessions)} sessions"
            assert sessions[0].browser.quit_called is True
            rows = db.get_all_source_titles("freegog")
            assert len(rows) == 2, f"remaining pages must be indexed via the fresh session, got {len(rows)}"
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is True
        finally:
            db.close()

    def test_cancel_event_stops_indexing_mid_loop(self, tmp_db_path: Path) -> None:
        import threading

        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            cancelled = threading.Event()
            entries = [("Game One", f"https://freegogpcgames.com/{i}/slug-{i}/") for i in range(129, 134)]
            az_html = _az_html_for(entries)
            sessions: list[_FakeSession] = []

            class _CancelAfterFirstGet(_FakeBrowser):
                def get(self, url: str) -> None:
                    super().get(url)
                    if not url.endswith("/game-list/"):
                        cancelled.set()

            def factory() -> _FakeSession:
                browser = _CancelAfterFirstGet(az_html=az_html)
                session = _FakeSession(browser)
                sessions.append(session)
                return session

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory, cancel_event=cancelled)

            assert len(sessions) == 1
            assert sessions[0].browser.game_page_gets == 1, (
                "only the first entry is fetched before mid-loop cancellation"
            )
            assert len(db.get_all_source_titles("freegog")) == 1, "the first entry is processed; the rest are not"
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is False, (
                "a cancelled run must not mark the sitemap cache valid"
            )
        finally:
            db.close()

    def test_upsert_failure_is_treated_as_page_failure(
        self, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:

            def boom_upsert(**kwargs: object) -> None:
                del kwargs
                raise RuntimeError("database is locked")

            monkeypatch.setattr(db, "upsert_source_title", boom_upsert)
            browser = _FakeBrowser(az_html="")
            entry = {"title": "Unstorable Game", "url": "https://freegogpcgames.com/135/unstorable-game/"}

            source = freegog.FreeGOGSource(db=db)
            outcome = source._process_az_entry(db, entry, browser, {})

            # A DB failure must be treated as a page failure, not abort the
            # whole index loop via an uncaught exception.
            assert outcome == "failed"
            assert db.get_all_source_titles("freegog") == []
        finally:
            db.close()

    def test_consecutive_timeouts_abort_indexing(self, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            monkeypatch.setattr(freegog, "_FETCH_TIMEOUT_SECONDS", 0.3)
            entries = [
                ("Game One", "https://freegogpcgames.com/136/game-one/"),
                ("Game Two", "https://freegogpcgames.com/137/game-two/"),
                ("Game Three", "https://freegogpcgames.com/138/game-three/"),
            ]
            az_html = _az_html_for(entries)
            sessions: list[_FakeSession] = []

            def factory() -> _FakeSession:
                browser = _FakeBrowser(az_html=az_html, hang=True)
                session = _FakeSession(browser)
                sessions.append(session)
                return session

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory)

            # Timeout #1 recycles the session; timeout #2 aborts the index run
            # instead of recycling forever on an all-timeout site.
            assert len(sessions) == 2, f"expected one recycle then abort, got {len(sessions)} sessions"
            assert sessions[0].browser.quit_called is True
            assert db.get_all_source_titles("freegog") == []
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is False, (
                "an aborted index must not mark the cache valid"
            )
        finally:
            db.close()

    def test_hanging_browser_teardown_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import time

        from gamarr.sources import freegog

        class _HangQuit(_FakeBrowser):
            def quit(self) -> None:
                time.sleep(5)  # simulate a wedged teardown channel

        monkeypatch.setattr(freegog, "_SESSION_IO_TIMEOUT_SECONDS", 0.3)
        browser = _HangQuit(az_html="")
        session = _FakeSession(browser)

        start = time.monotonic()
        freegog._close_browser_session(session, browser)
        elapsed = time.monotonic() - start

        assert elapsed < 5, f"teardown watchdog did not bound a hung quit(), took {elapsed:.1f}s"
        assert session.exited is True, "context exit must still run after the hung quit"

    def test_az_page_fetch_timeout_closes_session_and_skips_cache(
        self, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            monkeypatch.setattr(freegog, "_FETCH_TIMEOUT_SECONDS", 0.3)
            monkeypatch.setattr(freegog, "_fetch_freegog_az_entries", lambda: None)
            sessions: list[_FakeSession] = []

            def factory() -> _FakeSession:
                browser = _FakeBrowser(az_html="", hang_az=True)
                session = _FakeSession(browser)
                sessions.append(session)
                return session

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory)

            # The A-Z timeout must recycle the session (zombie safety) before
            # the fallback runs; both sessions must be torn down.
            assert len(sessions) == 2, f"expected recycle after A-Z timeout, got {len(sessions)} sessions"
            assert all(s.browser.quit_called for s in sessions), "both sessions must be quit"
            assert all(s.exited for s in sessions)
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is False, (
                "a failed A-Z fetch must not mark the cache valid"
            )
        finally:
            db.close()


class TestLogAzProgressBranches:
    """Direct branch coverage for _log_az_progress (both message formats)."""

    def test_first_fetch_logs_initial_message(self) -> None:
        from loguru import logger

        from gamarr.sources.freegog import FreeGOGSource

        captured: list[str] = []
        sink_id = logger.add(lambda msg: captured.append(str(msg)), level="INFO", format="{message}")
        try:
            FreeGOGSource._log_az_progress(new_count=0, missing_magnet_count=0, total_entries=550, known_count=0)
        finally:
            logger.remove(sink_id)

        assert any("FreeGOG: fetching 550 game pages" in m for m in captured), captured

    def test_batch_progress_logs_every_500(self) -> None:
        from loguru import logger

        from gamarr.sources.freegog import FreeGOGSource

        captured: list[str] = []
        sink_id = logger.add(lambda msg: captured.append(str(msg)), level="INFO", format="{message}")
        try:
            FreeGOGSource._log_az_progress(
                new_count=250,
                missing_magnet_count=250,
                total_entries=550,
                known_count=50,
            )
        finally:
            logger.remove(sink_id)

        assert any("FreeGOG progress: 500/500 games fetched" in m for m in captured), captured

    def test_open_failure_tears_down_half_opened_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import time

        from gamarr.sources import freegog

        exited: list[bool] = []

        class _HangEnter:
            def __enter__(self) -> None:
                time.sleep(5)  # simulate a wedged driver startup

            def __exit__(self, *exc: object) -> None:
                exited.append(True)

        monkeypatch.setattr(freegog, "_SESSION_IO_TIMEOUT_SECONDS", 0.3)
        ctx = _HangEnter()

        with pytest.raises(freegog.TimeoutExceededError):
            freegog._open_browser_session(lambda: ctx)

        assert exited == [True], "a half-opened context must be torn down on startup failure"

    def test_batch_progress_emitted_every_500_fetches(self, tmp_db_path: Path) -> None:
        from loguru import logger

        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            entries = [(f"Game {i}", f"https://freegogpcgames.com/{200 + i}/game-{i}/") for i in range(501)]
            az_html = _az_html_for(entries)
            captured: list[str] = []
            sink_id = logger.add(lambda msg: captured.append(str(msg)), level="INFO", format="{message}")
            try:
                source = freegog.FreeGOGSource(db=db)
                source._index_az_page(db, sb_factory=lambda: _FakeSession(_FakeBrowser(az_html=az_html)))
            finally:
                logger.remove(sink_id)

            batch = [m for m in captured if "FreeGOG progress: 500/" in m]
            assert len(batch) == 1, captured
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is True
        finally:
            db.close()

    def test_known_entry_does_not_reset_timeout_count(self, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from gamarr.database import Database, SourceTitle
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            monkeypatch.setattr(freegog, "_FETCH_TIMEOUT_SECONDS", 0.3)
            with db._session() as session:
                session.add(
                    SourceTitle(
                        source="freegog",
                        title="Known Game",
                        url="https://freegogpcgames.com/150/known-game/",
                        magnet="magnet:?xt=urn:btih:known",
                    )
                )
                session.commit()

            entries = [
                ("Hang One", "https://freegogpcgames.com/151/hang-one/"),
                ("Known Game", "https://freegogpcgames.com/150/known-game/"),
                ("Hang Two", "https://freegogpcgames.com/152/hang-two/"),
            ]
            az_html = _az_html_for(entries)
            sessions: list[_FakeSession] = []

            def factory() -> _FakeSession:
                browser = _FakeBrowser(az_html=az_html, hang=True)
                session = _FakeSession(browser)
                sessions.append(session)
                return session

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory)

            # Timeout on hang-one (count 1) recycles the session; the known
            # entry must NOT reset the timeout count; timeout on hang-two
            # (count 2) aborts the index run.
            assert len(sessions) == 2, f"expected recycle then abort, got {len(sessions)} sessions"
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is False, (
                "an aborted index must not mark the cache valid"
            )
        finally:
            db.close()


class TestParseFreeGOGAZNDJSON:
    """Parse the FreeGOG A-Z data endpoint (NDJSON) added after the
    2026-08-31 site redesign, when the HTML A-Z sections were replaced by
    an AJAX directory backed by an NDJSON endpoint."""

    def test_parse_real_endpoint_format(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_ndjson

        ndjson = (
            '{"format":1,"total":3,"counts":{"A":1,"B":1,"#":1}}\n'
            '["A","Gothic 1 Remake v1.0.2a","https://freegogpcgames.com/33511/gothic-1-remake/"]\n'
            '["B","Sea of Stars: Sunset Edition v1.0","https://freegogpcgames.com/11111/sea-of-stars/"]\n'
            '["#","Bad Dream: Fever (v26985)","https://freegogpcgames.com/6723/bad-dream-fever/"]\n'
        )
        entries = _parse_freegog_az_ndjson(ndjson)
        assert len(entries) == 3
        assert entries[0] == {
            "title": "Gothic 1 Remake",
            "url": "https://freegogpcgames.com/33511/gothic-1-remake/",
            "letter": "a",
        }
        assert entries[1]["title"] == "Sea of Stars", "edition/version suffixes must be cleaned"
        assert entries[2]["letter"] == "#", "numeric-section letter must survive"

    def test_dedups_by_url(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_ndjson

        ndjson = (
            '["A","Gothic 1 Remake v1.0.2a","https://freegogpcgames.com/33511/gothic-1-remake/"]\n'
            '["A","Gothic 1 Remake v1.0.0","https://freegogpcgames.com/33511/gothic-1-remake/"]\n'
        )
        entries = _parse_freegog_az_ndjson(ndjson)
        assert len(entries) == 1, "duplicate URLs must be deduplicated"

    def test_skips_header_and_malformed_lines(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_ndjson

        ndjson = (
            '{"format":1,"total":1,"counts":{}}\n'
            "not json at all\n"
            '["A","Gothic 1 Remake v1.0.2a","https://freegogpcgames.com/33511/gothic-1-remake/"]\n'
            '["A","only-two" ]\n'
            "[]\n"
        )
        entries = _parse_freegog_az_ndjson(ndjson)
        assert len(entries) == 1, "header objects and malformed lines must be skipped"
        assert entries[0]["title"] == "Gothic 1 Remake"

    def test_empty_and_whitespace_input(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_ndjson

        assert _parse_freegog_az_ndjson("") == []
        assert _parse_freegog_az_ndjson("\n\n   \n") == []


class TestFreeGOGDataEndpointFallback:
    """_index_az_page falls back to the NDJSON data endpoint when the HTML
    A-Z page parses to no entries (2026-08-31 site redesign)."""

    def test_index_az_page_falls_back_to_data_endpoint(
        self, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            # New-design page: HTML parses to zero entries.
            az_html = (
                '<section class="gd-az-search-results" data-gd-az-remote-results hidden>'
                '<div class="gd-az-directory-letter">A</div>'
                "</section>"
            )
            sessions: list[_FakeSession] = []

            def factory() -> _FakeSession:
                session = _FakeSession(_FakeBrowser(az_html=az_html))
                sessions.append(session)
                return session

            monkeypatch.setattr(
                freegog,
                "_fetch_freegog_az_entries",
                lambda: [
                    {
                        "title": "Gothic 1 Remake",
                        "url": "https://freegogpcgames.com/33511/gothic-1-remake/",
                        "letter": "a",
                    },
                    {"title": "Sea of Stars", "url": "https://freegogpcgames.com/11111/sea-of-stars/", "letter": "b"},
                ],
            )

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory)

            assert len(sessions) == 1
            rows = db.get_all_source_titles("freegog")
            assert len(rows) == 2, f"data endpoint entries must be indexed, got {len(rows)}"
            assert {r["title"] for r in rows} == {"Gothic 1 Remake", "Sea of Stars"}
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is True
        finally:
            db.close()

    def test_fetch_freegog_az_entries_returns_none_on_http_failure(self) -> None:
        from unittest.mock import patch

        import requests as requests_module

        from gamarr.sources import freegog

        with patch(
            "gamarr.sources.freegog.requests.get",
            side_effect=requests_module.exceptions.ConnectionError("down"),
        ):
            entries = freegog._fetch_freegog_az_entries()

        assert entries is None, "a failed data-endpoint request must degrade to None"

    def test_fetch_freegog_az_entries_parses_successful_response(self) -> None:
        from unittest.mock import patch

        from gamarr.sources import freegog

        class _FakeResponse:
            headers = {"Content-Type": "application/x-ndjson"}

            def __init__(self, text: str) -> None:
                self.text = text
                self.content = text.encode("utf-8")

            def __enter__(self) -> _FakeResponse:
                return self

            def __exit__(self, *exc: object) -> None:
                pass

            def raise_for_status(self) -> None:
                pass

        ndjson = (
            '{"format":1,"total":1,"counts":{}}\n'
            '["A","Gothic 1 Remake v1.0.2a","https://freegogpcgames.com/33511/gothic-1-remake/"]\n'
        )
        with patch("gamarr.sources.freegog.requests.get", return_value=_FakeResponse(ndjson)):
            entries = freegog._fetch_freegog_az_entries()

        assert entries == [
            {
                "title": "Gothic 1 Remake",
                "url": "https://freegogpcgames.com/33511/gothic-1-remake/",
                "letter": "a",
            }
        ]

    def test_fetch_freegog_az_entries_returns_none_on_http_error_status(self) -> None:
        from unittest.mock import patch

        import requests as requests_module

        from gamarr.sources import freegog

        class _ErrorResponse:
            def __enter__(self) -> _ErrorResponse:
                return self

            def __exit__(self, *exc: object) -> None:
                pass

            def raise_for_status(self) -> None:
                raise requests_module.exceptions.HTTPError("403")

        with patch("gamarr.sources.freegog.requests.get", return_value=_ErrorResponse()):
            entries = freegog._fetch_freegog_az_entries()

        assert entries is None, "a non-2xx response must degrade to None"

    def test_html_entries_do_not_trigger_data_endpoint(
        self, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            entries = [("Gothic 1 Remake v1.0.2a", "https://freegogpcgames.com/33511/gothic-1-remake/")]
            az_html = _az_html_for(entries)
            sessions: list[_FakeSession] = []
            endpoint_calls: list[bool] = []

            def factory() -> _FakeSession:
                session = _FakeSession(_FakeBrowser(az_html=az_html))
                sessions.append(session)
                return session

            def fail_if_called() -> None:
                endpoint_calls.append(True)
                raise AssertionError("data endpoint must not be consulted when HTML has entries")

            monkeypatch.setattr(freegog, "_fetch_freegog_az_entries", fail_if_called)

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory)

            assert endpoint_calls == [], "the data endpoint must not be called for a non-empty HTML parse"
            rows = db.get_all_source_titles("freegog")
            assert len(rows) == 1, "HTML entries must be indexed"
        finally:
            db.close()

    def test_fallback_failure_leaves_cache_unset(self, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            monkeypatch.setattr(freegog, "_fetch_freegog_az_entries", lambda: None)
            az_html = '<section class="gd-az-search-results" hidden></section>'
            sessions: list[_FakeSession] = []

            def factory() -> _FakeSession:
                session = _FakeSession(_FakeBrowser(az_html=az_html))
                sessions.append(session)
                return session

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory)

            assert len(sessions) == 1
            assert db.get_all_source_titles("freegog") == []
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is False, (
                "a failed fallback must not mark the cache valid"
            )
        finally:
            db.close()

    def test_html_fetch_failure_falls_back_to_data_endpoint(
        self, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            monkeypatch.setattr(
                freegog,
                "_fetch_freegog_az_entries",
                lambda: [
                    {"title": "Sea of Stars", "url": "https://freegogpcgames.com/11111/sea-of-stars/", "letter": "b"}
                ],
            )
            sessions: list[_FakeSession] = []

            class _FailingAZGet(_FakeBrowser):
                def uc_open_with_reconnect(self, url: str, count: int) -> None:
                    del url, count
                    raise RuntimeError("browser A-Z fetch failed")

            def factory() -> _FakeSession:
                session = _FakeSession(_FailingAZGet(az_html=""))
                sessions.append(session)
                return session

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory)

            rows = db.get_all_source_titles("freegog")
            assert len(rows) == 1, "the data endpoint must be consulted when the HTML fetch fails"
            assert rows[0]["title"] == "Sea of Stars"
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is True
        finally:
            db.close()

    def test_ndjson_skips_foreign_and_malformed_records(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_ndjson

        ndjson = (
            '["A","Good Game","https://freegogpcgames.com/1/good-game/"]\n'
            '["A","Evil Game","https://evil.com/1/evil-game/"]\n'
            '["A","No Protocol","freegogpcgames.com/2/no-proto/"]\n'
            '["A", null, "https://freegogpcgames.com/3/null-title/"]\n'
            '["A", 42, "https://freegogpcgames.com/4/num-title/"]\n'
            '["A","","https://freegogpcgames.com/5/empty-title/"]\n'
        )
        entries = _parse_freegog_az_ndjson(ndjson)
        assert len(entries) == 1, f"only the well-formed record must parse, got {entries}"
        assert entries[0]["title"] == "Good Game"

    def test_ndjson_preserves_unicode_titles(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_ndjson

        ndjson = '["#","ΔV: Rings of Saturn","https://freegogpcgames.com/11393/%ce%b4v-rings-of-saturn/"]\n'
        entries = _parse_freegog_az_ndjson(ndjson)
        assert len(entries) == 1
        assert entries[0]["title"] == "ΔV: Rings of Saturn"
        assert entries[0]["url"] == "https://freegogpcgames.com/11393/%ce%b4v-rings-of-saturn/"

    def test_alternating_timeouts_hit_total_abort_bound(
        self, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            monkeypatch.setattr(freegog, "_FETCH_TIMEOUT_SECONDS", 0.3)
            monkeypatch.setattr(freegog, "_fetch_freegog_az_entries", lambda: None)
            entries = [(f"Game {i}", f"https://freegogpcgames.com/{160 + i}/game-{i}/") for i in range(10)]
            az_html = _az_html_for(entries)
            sessions: list[_FakeSession] = []

            def factory() -> _FakeSession:
                # Even sessions hang (timeout), odd sessions fail EVERY game
                # page, so no two timeouts are ever consecutive: only the
                # total-timeout bound can trigger the abort.
                idx = len(sessions)
                browser = _FakeBrowser(
                    az_html=az_html,
                    hang=idx % 2 == 0,
                    fail_game_pages=1_000_000 if idx % 2 == 1 else 0,
                )
                session = _FakeSession(browser)
                sessions.append(session)
                return session

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory)

            assert db.get_all_source_titles("freegog") == []
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is False, (
                "an index aborted by the total-timeout bound must not mark the cache valid"
            )
            assert len(sessions) >= 5, f"expected repeated recycles before the total abort, got {len(sessions)}"
        finally:
            db.close()

    def test_az_timeout_with_successful_fallback_indexes_entries(
        self, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            monkeypatch.setattr(freegog, "_FETCH_TIMEOUT_SECONDS", 0.3)
            monkeypatch.setattr(
                freegog,
                "_fetch_freegog_az_entries",
                lambda: [
                    {
                        "title": "Gothic 1 Remake",
                        "url": "https://freegogpcgames.com/33511/gothic-1-remake/",
                        "letter": "a",
                    }
                ],
            )
            sessions: list[_FakeSession] = []

            def factory() -> _FakeSession:
                session = _FakeSession(_FakeBrowser(az_html="", hang_az=True))
                sessions.append(session)
                return session

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory)

            # The zombie-carrying session must be recycled before the fallback
            # entries are fetched on a fresh session.
            assert len(sessions) == 2, f"expected recycle after A-Z timeout, got {len(sessions)} sessions"
            rows = db.get_all_source_titles("freegog")
            assert len(rows) == 1, "fallback entries must be indexed on the recycled session"
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is True
        finally:
            db.close()

    def test_fetch_freegog_az_entries_rejects_unexpected_content_type(self) -> None:
        from unittest.mock import patch

        from gamarr.sources import freegog

        class _HtmlResponse:
            headers = {"Content-Type": "text/html"}
            content = b"<!doctype html><title>challenge</title>"

            def __enter__(self) -> _HtmlResponse:
                return self

            def __exit__(self, *exc: object) -> None:
                pass

            def raise_for_status(self) -> None:
                pass

        with patch("gamarr.sources.freegog.requests.get", return_value=_HtmlResponse()):
            entries = freegog._fetch_freegog_az_entries()

        assert entries is None, "a challenge/HTML response must degrade to None"

    def test_fetch_freegog_az_entries_accepts_capitalised_content_type(self) -> None:
        from unittest.mock import patch

        from gamarr.sources import freegog

        ndjson = '{"format":1,"total":1,"counts":{}}\n["A","Good Game","https://freegogpcgames.com/1/good-game/"]\n'

        class _CapitalisedResponse:
            headers = {"Content-Type": "Application/JSON"}

            def __init__(self, text: str) -> None:
                self.content = text.encode("utf-8")

            def __enter__(self) -> _CapitalisedResponse:
                return self

            def __exit__(self, *exc: object) -> None:
                pass

            def raise_for_status(self) -> None:
                pass

        with patch("gamarr.sources.freegog.requests.get", return_value=_CapitalisedResponse(ndjson)):
            entries = freegog._fetch_freegog_az_entries()

        assert entries is not None and len(entries) == 1, "MIME types are case-insensitive"

    def test_fetch_freegog_az_entries_returns_none_on_timeout(self) -> None:
        from unittest.mock import patch

        import requests as requests_module

        from gamarr.sources import freegog

        with patch(
            "gamarr.sources.freegog.requests.get",
            side_effect=requests_module.exceptions.Timeout("slow"),
        ):
            entries = freegog._fetch_freegog_az_entries()

        assert entries is None

    def test_fetch_freegog_az_entries_returns_none_on_invalid_utf8(self) -> None:
        from unittest.mock import patch

        from gamarr.sources import freegog

        class _BadUtf8Response:
            headers = {"Content-Type": "application/x-ndjson"}
            content = b"\xff\xfe\x00not utf-8"

            def __enter__(self) -> _BadUtf8Response:
                return self

            def __exit__(self, *exc: object) -> None:
                pass

            def raise_for_status(self) -> None:
                pass

        with patch("gamarr.sources.freegog.requests.get", return_value=_BadUtf8Response()):
            entries = freegog._fetch_freegog_az_entries()

        assert entries is None, "an undecodable body must degrade to None"

    def test_fetch_warns_when_parsed_less_than_announced_total(self) -> None:
        from unittest.mock import patch

        from loguru import logger

        from gamarr.sources import freegog

        ndjson = '{"format":1,"total":10,"counts":{}}\n["A","Good Game","https://freegogpcgames.com/1/good-game/"]\n'

        class _OkResponse:
            headers = {"Content-Type": "application/x-ndjson"}

            def __init__(self, text: str) -> None:
                self.content = text.encode("utf-8")

            def __enter__(self) -> _OkResponse:
                return self

            def __exit__(self, *exc: object) -> None:
                pass

            def raise_for_status(self) -> None:
                pass

        captured: list[str] = []
        sink_id = logger.add(lambda msg: captured.append(str(msg)), level="INFO", format="{message}")
        try:
            with patch("gamarr.sources.freegog.requests.get", return_value=_OkResponse(ndjson)):
                entries = freegog._fetch_freegog_az_entries()
        finally:
            logger.remove(sink_id)

        assert entries is not None and len(entries) == 1
        assert any("parsed 1 of 10 announced entries" in m for m in captured), captured


class TestFreeGOGAZNDJSONTotal:
    """Direct coverage for the NDJSON header 'total' extraction."""

    def test_valid_header_total(self) -> None:
        from gamarr.sources.freegog import _freegog_az_ndjson_total

        assert _freegog_az_ndjson_total('{"format":1,"total":5534,"counts":{}}\n["A",...]\n') == 5534

    def test_empty_text(self) -> None:
        from gamarr.sources.freegog import _freegog_az_ndjson_total

        assert _freegog_az_ndjson_total("") is None

    def test_malformed_header(self) -> None:
        from gamarr.sources.freegog import _freegog_az_ndjson_total

        assert _freegog_az_ndjson_total("not json") is None
        assert _freegog_az_ndjson_total('["A","T","https://freegogpcgames.com/1/t/"]') is None

    def test_header_without_total_or_non_int_total(self) -> None:
        from gamarr.sources.freegog import _freegog_az_ndjson_total

        assert _freegog_az_ndjson_total('{"format":1,"counts":{}}') is None
        assert _freegog_az_ndjson_total('{"format":1,"total":"5534"}') is None

    def test_blank_lines_before_header(self) -> None:
        from gamarr.sources.freegog import _freegog_az_ndjson_total

        assert _freegog_az_ndjson_total('\n\n{"format":1,"total":42,"counts":{}}\n') == 42

    def test_bom_before_header(self) -> None:
        from gamarr.sources.freegog import _freegog_az_ndjson_total

        assert _freegog_az_ndjson_total('\ufeff{"format":1,"total":42,"counts":{}}\n') == 42

    def test_session_open_failure_consults_data_endpoint_and_retries(
        self, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gamarr.database import Database
        from gamarr.sources import freegog

        db = Database(tmp_db_path)
        try:
            monkeypatch.setattr(
                freegog,
                "_fetch_freegog_az_entries",
                lambda: [
                    {"title": "Sea of Stars", "url": "https://freegogpcgames.com/11111/sea-of-stars/", "letter": "b"}
                ],
            )
            sessions: list[_FakeSession] = []
            open_attempts: list[bool] = []

            class _RaisingSession(_FakeSession):
                def __enter__(self) -> _FakeBrowser:
                    open_attempts.append(True)
                    raise RuntimeError("browser startup wedged")

            def factory() -> _FakeSession:
                if len(sessions) == 0:
                    session: _FakeSession = _RaisingSession(_FakeBrowser(az_html=""))
                else:
                    session = _FakeSession(_FakeBrowser(az_html=""))
                sessions.append(session)
                return session

            source = freegog.FreeGOGSource(db=db)
            source._index_az_page(db, sb_factory=factory)

            # First open fails -> endpoint consulted -> one open retry succeeds.
            assert len(open_attempts) == 1, "the first open must fail exactly once"
            assert len(sessions) == 2, f"expected a retry session, got {len(sessions)} sessions"
            rows = db.get_all_source_titles("freegog")
            assert len(rows) == 1, "endpoint entries must be indexed via the retried session"
            assert db.get_sitemap_cache("freegog", ttl_hours=6) is True
        finally:
            db.close()

    def test_ndjson_titles_are_html_unescaped(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_ndjson

        ndjson = '["A","Tom &amp; Jerry","https://freegogpcgames.com/1/tom-jerry/"]\n'
        entries = _parse_freegog_az_ndjson(ndjson)
        assert len(entries) == 1
        assert entries[0]["title"] == "Tom & Jerry", "HTML entities must be decoded like the HTML parser does"
