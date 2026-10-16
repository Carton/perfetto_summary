"""Plain report output keeps literal names and complete numeric values."""

import contextlib
import io
import os
import unittest
from unittest.mock import patch

from perfetto_summary.output import bar, section, table, text


class OutputTests(unittest.TestCase):
    def test_input_control_characters_are_visible_and_inert(self) -> None:
        output = io.StringIO()
        name = "abc\x1b[31mred\x1b[0m\r\b\x9b"
        with contextlib.redirect_stdout(output):
            section(name)
            text(name)
            table(("Name",), [(name,)])
        rendered = output.getvalue()
        self.assertFalse(any(ord(c) < 32 and c != "\n" for c in rendered))
        self.assertNotIn("\x9b", rendered)
        self.assertIn(r"abc\x1b[31mred\x1b[0m\x0d\x08\x9b", rendered)

    def test_redirected_report_is_literal_without_ansi(self) -> None:
        output = io.StringIO()
        name = "[bold]literal[/bold]:smile:" + "中文长进程名_long_process_name_" * 20
        with contextlib.redirect_stdout(output):
            section("CPU work")
            table(
                ("Name", "Cycles", "Share"),
                [(name, "12345678901234567890.123", "125.7%")],
                right=(1, 2),
            )
        rendered = output.getvalue()
        self.assertNotIn("\x1b", rendered)
        self.assertIn("[bold]literal[/bold]:smile:", rendered)
        self.assertIn("12345678901234567890.123", rendered)
        self.assertIn("125.7%", rendered)
        name_output = rendered.replace("12345678901234567890.123", "").replace(
            "125.7%", ""
        )
        self.assertIn(name, "".join(name_output.split()))

    def test_narrow_terminal_preserves_numeric_cells_and_all_text(self) -> None:
        output = io.StringIO()
        name = "com.example.very_long_process_name" * 3
        number = "987654321098765432109876543210.123"
        with (
            contextlib.redirect_stdout(output),
            patch.object(output, "isatty", return_value=True),
            patch(
                "perfetto_summary.output.shutil.get_terminal_size",
                return_value=os.terminal_size((30, 24)),
            ),
        ):
            table(
                ("Name", "Runtime(ms)", "Count"),
                [(name, number, 999999999999999)],
                right=(1, 2),
            )
        rendered = output.getvalue()
        self.assertIn(number, rendered)
        self.assertIn("999999999999999", rendered)
        name_output = rendered.replace(number, "").replace("999999999999999", "")
        self.assertIn(name, "".join(name_output.split()))
        self.assertNotIn("\x1b", rendered)

    def test_unavailable_and_bounded_share_bar(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            table(("Name", "Value"), [])
        self.assertEqual(output.getvalue().strip(), "N/A: no matching data.")
        self.assertEqual(bar(50), "#" * 10)
        self.assertEqual(bar(150), "#" * 20)
        self.assertEqual(bar(-1), "")
        self.assertEqual(bar(1, 0), "")
