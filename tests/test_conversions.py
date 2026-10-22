"""Converts a generated sample of every input format to every available target.

Run with:  python -m pytest tests   (or simply:  python tests/test_conversions.py)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import localconvert.converters  # noqa: F401,E402
from localconvert.core import registry  # noqa: E402
from localconvert.core.jobs import combine_to_pdf, convert_one  # noqa: E402
from localconvert.core.registry import Options  # noqa: E402
from tests.fixtures import make_fixtures  # noqa: E402

SKIP = {("gif_anim", None)}


def run_matrix(verbose: bool = True):
    tmp = Path(tempfile.mkdtemp(prefix="lc-test-"))
    fixtures = make_fixtures(tmp / "in")
    failures, count = [], 0
    for key, src in fixtures.items():
        ext = key if key in registry.all_sources() else src.name.split(".", 1)[1]
        for target, info in registry.targets_for(ext).items():
            if not info["available"]:
                continue
            count += 1
            try:
                outs = convert_one(src, target, tmp / "out" / key, Options())
                assert outs and all(o.stat().st_size > 0 for o in outs), "empty output"
                if verbose:
                    print(f"ok   {key:8} -> {target:6} {', '.join(o.name for o in outs)}")
            except Exception as e:
                failures.append((key, target, repr(e)))
                print(f"FAIL {key:8} -> {target:6} {e!r}")
    combined = combine_to_pdf([fixtures["jpg"], fixtures["png"], fixtures["docx"], fixtures["pdf"]],
                              tmp / "out" / "combined", Options())
    assert combined.stat().st_size > 0
    print(f"\n{count} conversions, {len(failures)} failures. Output: {tmp}")
    return failures


def test_all_conversions():
    assert run_matrix(verbose=False) == []


if __name__ == "__main__":
    sys.exit(1 if run_matrix() else 0)
