"""Write the sample log to a file Ocelescope can import.

    python examples/generate_sample_log.py [path]

Defaults to ``examples/sample_logistics_qel.jsonocel``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sample_log import build_ocel, write_ocel2_json  # noqa: E402


def main() -> None:
    target = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else Path(__file__).parent / "sample_logistics_qel.jsonocel"
    )
    ocel = build_ocel()
    write_ocel2_json(target)
    print(
        f"wrote {target} — "
        f"{ocel.events.count} events, {ocel.objects.count} objects, "
        f"item types: {', '.join(ocel.quantities.item_types)}"
    )


if __name__ == "__main__":
    main()
