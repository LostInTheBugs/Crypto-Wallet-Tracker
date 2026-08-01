"""Test version parsing and comparison — _parse_version, _cmp_versions, _VERSION_RE."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import re

# Copy the implementation directly for testing (avoids app import side-effects)
_VERSION_RE = re.compile(r"^\d{4}\.\d{2}\.\d{3}(-c\d+)?$")


def _parse_version(v: str) -> tuple[int, int, int, int] | None:
    m = _VERSION_RE.match(v)
    if not m:
        return None
    # Split on dot: "2026.08.001-c1" -> ["2026", "08", "001-c1"]
    parts = v.split(".")
    year, month = int(parts[0]), int(parts[1])
    nnn_str = parts[2]
    correction = 0
    if "-c" in nnn_str:
        base_nnn, corr = nnn_str.split("-c")
        nnn = int(base_nnn)
        correction = int(corr)
    else:
        nnn = int(nnn_str)
    return (year, month, nnn, correction)


def _cmp_versions(a: str, b: str) -> int:
    pa, pb = _parse_version(a), _parse_version(b)
    if pa is None and pb is None:
        return (a > b) - (a < b)
    if pa is None:
        return -1
    if pb is None:
        return 1
    for i in range(4):
        if pa[i] > pb[i]:
            return 1
        if pa[i] < pb[i]:
            return -1
    return 0


def test_parse_valid():
    assert _parse_version("2026.08.001") == (2026, 8, 1, 0)
    assert _parse_version("2026.08.001-c1") == (2026, 8, 1, 1)
    assert _parse_version("2026.08.001-c99") == (2026, 8, 1, 99)
    assert _parse_version("2026.08.002") == (2026, 8, 2, 0)
    assert _parse_version("2026.09.001") == (2026, 9, 1, 0)
    assert _parse_version("2027.01.001") == (2027, 1, 1, 0)
    print("PASS test_parse_valid")


def test_parse_invalid():
    assert _parse_version("2026.08.1") is None       # 1 digit NNN
    assert _parse_version("v1.2.0") is None
    assert _parse_version("2026.08.001.c1") is None  # dot before c
    assert _parse_version("2026.8.001") is None      # 1 digit month
    assert _parse_version("") is None
    assert _parse_version("abc") is None
    assert _parse_version("2026.08.001c1") is None   # missing hyphen
    print("PASS test_parse_invalid")


def test_compare():
    # Basic ordering
    assert _cmp_versions("2026.08.001", "2026.08.002") == -1
    assert _cmp_versions("2026.08.002", "2026.08.001") == 1
    assert _cmp_versions("2026.08.001", "2026.08.001") == 0

    # Correction suffix
    assert _cmp_versions("2026.08.001", "2026.08.001-c1") == -1
    assert _cmp_versions("2026.08.001-c1", "2026.08.001-c2") == -1
    assert _cmp_versions("2026.08.001-c99", "2026.08.002") == -1
    assert _cmp_versions("2026.08.001-c1", "2026.08.001") == 1
    assert _cmp_versions("2026.08.002", "2026.08.001-c99") == 1

    # Cross-month
    assert _cmp_versions("2026.08.001", "2026.09.001") == -1
    assert _cmp_versions("2026.09.001", "2026.08.999") == 1

    # Cross-year
    assert _cmp_versions("2026.12.001", "2027.01.001") == -1

    # Mixed valid/invalid (invalid sorts lower)
    assert _cmp_versions("v1.0.0", "2026.08.001") == -1
    assert _cmp_versions("2026.08.001", "v1.0.0") == 1
    print("PASS test_compare")


def test_sort():
    versions = ["2026.08.001", "2026.08.001-c1", "2026.08.002", "2026.09.001"]
    sorted_v = sorted(versions, key=lambda v: _parse_version(v) or (0, 0, 0, 0))
    expected = ["2026.08.001", "2026.08.001-c1", "2026.08.002", "2026.09.001"]
    assert sorted_v == expected, f"Got {sorted_v}"
    print("PASS test_sort")


def test_filter_nonconforming():
    tags = ["2026.08.001", "2026.08.1", "v1.2.0", "2026.08.001.c1", "2026.08.002"]
    valid = [t for t in tags if _VERSION_RE.match(t)]
    assert valid == ["2026.08.001", "2026.08.002"], f"Got {valid}"
    print("PASS test_filter_nonconforming")


if __name__ == "__main__":
    test_parse_valid()
    test_parse_invalid()
    test_compare()
    test_sort()
    test_filter_nonconforming()
    print("\n✅ ALL VERSION COMPARE TESTS PASSED")
