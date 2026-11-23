"""The periodic scan-progress line must not name a mode it is not running."""

from __future__ import annotations

from gamarr.metacritic import _log_batch_progress


class TestScanProgressLine:
    def test_the_progress_line_does_not_claim_backlog_mode(self) -> None:
        from loguru import logger

        lines: list[str] = []
        sink_id = logger.add(lambda msg: lines.append(str(msg)), level="INFO", format="{message}")
        try:
            _log_batch_progress(100, 5000, True)
        finally:
            logger.remove(sink_id)
        assert any("page 100" in line for line in lines), lines
        assert not any("Backlog" in line for line in lines), lines
