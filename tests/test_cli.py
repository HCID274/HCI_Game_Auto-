"""Tests for the single public command-line entry point."""

import unittest
from unittest.mock import patch

from starrail_auto.cli import main


class CliTests(unittest.TestCase):
    def test_daily_delegates_to_daily_workflow(self) -> None:
        with patch("starrail_auto.cli.run_daily", return_value=23) as run:
            self.assertEqual(main(["daily", "--timeout", "900"]), 23)
        run.assert_called_once_with(900)

    def test_cleanup_delegates_to_cleanup_workflow(self) -> None:
        with patch("starrail_auto.cli.execute_cleanup", return_value=0) as cleanup:
            self.assertEqual(main(["cleanup", "--delay", "10"]), 0)
        cleanup.assert_called_once_with(10, None)


if __name__ == "__main__":
    unittest.main()
