"""Plain text report layout for terminals and redirected output."""

import shutil
import sys
from collections.abc import Iterable, Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

_CONTROL_ESCAPES = {
    code: f"\\x{code:02x}" for code in (*range(10), *range(11, 32), *range(127, 160))
}


def _literal(value: object) -> str:
    """Make input control characters visible while retaining line breaks."""
    return str(value).translate(_CONTROL_ESCAPES)


def text(message: str) -> None:
    """Wrap ordinary report text without interpreting markup or emoji codes."""
    width = shutil.get_terminal_size((120, 24)).columns if sys.stdout.isatty() else 120
    Console(
        file=sys.stdout,
        width=width,
        force_terminal=False,
        color_system=None,
        highlight=False,
        emoji=False,
    ).print(Text(_literal(message)), overflow="fold")


def section(title: str) -> None:
    title = _literal(title)
    print(f"\n{title}\n{'=' * len(title)}")


def bar(value: float, total: float = 100.0, width: int = 20) -> str:
    """Show a bounded share; callers retain the unbounded numeric value."""
    fraction = max(0.0, min(1.0, value / total)) if total > 0 else 0.0
    return "#" * int(fraction * width)


def table(
    columns: Sequence[str],
    rows: Iterable[Sequence[object]],
    *,
    right: Sequence[int] = (),
    empty: str = "N/A: no matching data.",
) -> None:
    """Wrap complete text cells while keeping every numeric cell intact."""
    columns = [_literal(title) for title in columns]
    cells = [[_literal(value) for value in row] for row in rows]
    if not cells:
        print(_literal(empty))
        return
    numeric_widths = {
        i: max(len(columns[i]), *(len(row[i]) for row in cells)) for i in right
    }
    minimum = sum(numeric_widths.values()) + 8 * (len(columns) - len(right))
    minimum += 2 * (len(columns) - 1)
    width = shutil.get_terminal_size((120, 24)).columns if sys.stdout.isatty() else 120
    report = Table(box=None, pad_edge=False, padding=(0, 1), header_style="")
    for i, title in enumerate(columns):
        report.add_column(
            Text(title + "\n" + "-" * len(title)),
            justify="right" if i in right else "left",
            no_wrap=i in right,
            min_width=numeric_widths.get(i, 8),
            overflow="fold",
        )
    for row in cells:
        report.add_row(*(Text(value) for value in row))
    Console(
        file=sys.stdout,
        width=max(width, minimum),
        force_terminal=False,
        color_system=None,
        highlight=False,
        emoji=False,
    ).print(report)
