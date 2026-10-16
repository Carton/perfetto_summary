"""Check processor selection after installing the src-layout package."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from perfetto_summary import cli


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.trace = Path(self.temp.name) / "capture.pftrace"
        self.trace.touch()

    def run_cli(self, *options: str) -> str | None:
        with (
            patch("sys.argv", ["perfetto_summary", *options, str(self.trace)]),
            patch.object(cli, "PerfettoParser") as parser,
        ):
            cli.main()
            parser.return_value.__enter__.return_value.parse.assert_called_once_with(
                str(self.trace)
            )
            return parser.call_args.args[0]

    def test_default_uses_official_processor_selection(self) -> None:
        self.assertIsNone(self.run_cli())

    def test_default_parser_leaves_binary_selection_to_official_client(self) -> None:
        parser = cli.PerfettoParser()
        with (
            patch.object(cli, "TraceProcessor") as processor,
            patch.object(parser, "parse_basic_info", return_value=False),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with parser:
                parser.parse(str(self.trace))
            processor.assert_called_once_with(trace=str(self.trace))
            processor.return_value.close.assert_called_once_with()

    def test_explicit_binary_is_preserved(self) -> None:
        binary = Path(self.temp.name) / "custom_processor"
        binary.touch()
        self.assertEqual(self.run_cli("--parser", str(binary)), str(binary))

    def test_remote_default_is_localhost(self) -> None:
        self.assertEqual(self.run_cli("--remote"), "localhost:9001")

    def test_explicit_remote_address(self) -> None:
        self.assertEqual(
            self.run_cli("--remote", "--parser", "127.0.0.1:1234"), "127.0.0.1:1234"
        )

    def test_recent_cpu_profiles_are_accepted(self) -> None:
        for cpu_type in ("sm8550", "sm8650", "sm8750", "sm8850"):
            with self.subTest(cpu_type=cpu_type):
                self.run_cli("--cputype", cpu_type)
                self.assertEqual(cli.args.cpu_type, cpu_type)

    def test_unknown_cpu_profile_is_rejected(self) -> None:
        with (
            patch(
                "sys.argv",
                ["perfetto_summary", "--cputype", "unknown", str(self.trace)],
            ),
            self.assertRaises(SystemExit) as error,
            patch("sys.stderr"),
        ):
            cli.main()
        self.assertEqual(error.exception.code, 2)
