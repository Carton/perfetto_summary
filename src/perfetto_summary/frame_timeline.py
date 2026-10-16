"""Layer-scoped FrameTimeline outcomes, separate from submission cadence."""

from collections import defaultdict
from typing import Any


def frame_timeline_rows(tp: Any, pid: int | None = None) -> list[Any]:
    """Aggregate actual timeline labels without inventing display timestamps."""
    predicate = "" if pid is None else f"WHERE process.pid = {int(pid)}"
    return list(
        tp.query(f"""
        WITH identified AS (
            SELECT *,
                CASE WHEN layer_name IS NOT NULL OR surface_frame_token > 0 THEN 'App'
                     WHEN coalesce(surface_frame_token, 0) = 0 AND display_frame_token > 0 THEN 'SF'
                     ELSE 'Unknown' END AS kind,
                CASE WHEN upid IS NOT NULL AND nullif(layer_name, '') IS NOT NULL
                          AND surface_frame_token > 0 THEN 1 ELSE 0 END AS keyed
            FROM actual_frame_timeline_slice
        ), ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY upid, layer_name, surface_frame_token,
                             CASE WHEN keyed THEN NULL ELSE id END
                ORDER BY (dur >= 0) DESC, id DESC) AS rank
            FROM identified
        )
        SELECT kind, ranked.upid, process.pid, process.name, layer_name,
               jank_tag, jank_type, present_type,
               sum(CASE WHEN rank = 1 AND dur >= 0 THEN 1 ELSE 0 END) AS complete,
               sum(CASE WHEN dur < 0 THEN 1 ELSE 0 END) AS incomplete,
               sum(CASE WHEN rank > 1 THEN 1 ELSE 0 END) AS duplicates,
               sum(CASE WHEN rank = 1 AND dur >= 0 AND kind = 'App'
                             AND NOT keyed THEN 1 ELSE 0 END) AS unkeyed
        FROM ranked LEFT JOIN process USING (upid)
        {predicate}
        GROUP BY kind, ranked.upid, layer_name, jank_tag, jank_type, present_type
        ORDER BY kind, ranked.upid, layer_name, jank_tag, jank_type, present_type
    """)
    )


def show_frame_timeline(tp: Any, pid: int | None = None) -> None:
    """Print native outcome labels; stuffing alone is not tagged jank."""
    print("\n------------------------\nFrameTimeline outcomes:")
    rows = frame_timeline_rows(tp, pid)
    if not rows:
        print("N/A: no actual FrameTimeline data for this selection.")
        return
    groups: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
    for row in rows:
        groups[(row.kind, row.upid, row.pid, row.name, row.layer_name)].append(row)
    known_tags = {
        "Self Jank",
        "Other Jank",
        "No Jank",
        "Buffer Stuffing",
        "Dropped Frame",
        "SurfaceFlinger Stuffing",
        "Non-perceivable Jank",
    }
    for (kind, upid, process_id, name, layer), outcomes in groups.items():
        total = sum(r.complete for r in outcomes)
        known = sum(r.complete for r in outcomes if r.jank_tag in known_tags)
        jank = sum(
            r.complete for r in outcomes if r.jank_tag in {"Self Jank", "Other Jank"}
        )
        late = sum(r.complete for r in outcomes if r.present_type == "Late Present")
        dropped = sum(r.complete for r in outcomes if r.present_type == "Dropped Frame")
        ratio = f"{100.0 * jank / known:.2f}%" if known else "N/A"
        print(
            f"{kind} PID={process_id} UPID={upid} {name or ''} Layer={layer if layer is not None else '(unavailable)'}"
        )
        print(
            f"  Complete={total} Tagged jank={jank}/{known} ({ratio}) Late={late} Dropped={dropped} Unknown tag={total - known}"
        )
        print(
            f"  Incomplete records={sum(r.incomplete for r in outcomes)} Duplicate app records={sum(r.duplicates for r in outcomes)} Unkeyed app records={sum(r.unkeyed for r in outcomes)}"
        )
        for r in outcomes:
            if r.complete:
                print(
                    f"    {r.complete:6}  {r.present_type or '(unknown present)'} | {r.jank_tag or '(unknown tag)'} | {r.jank_type or '(unknown cause)'}"
                )
    print(
        "Jank/late/drop can overlap. SF counts are timeline records; displays are not merged or deduplicated by token."
    )
    print(
        "App timeline completion is not screen presentation; these counts do not define displayed FPS."
    )
