import pytest

from geodiff.engine.ids import IdError, index_ids, normalize_id
from tests.helpers import pt


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BENCH-0142", "BENCH-0142"),
        ("  BENCH-0142 ", "BENCH-0142"),
        ("0142", "0142"),  # leading zeros are significant
        (142, "142"),
        (142.0, "142"),
        (-7, "-7"),
        ("Café", "Café"),  # NFC
    ],
)
def test_normalize(raw, expected):
    assert normalize_id(raw) == expected


def test_leading_zeros_and_case_are_distinct():
    assert normalize_id("0142") != normalize_id(142)
    assert normalize_id("bench-1") != normalize_id("BENCH-1")


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (None, "null_id"),
        ("", "empty_id"),
        ("   ", "empty_id"),
        (True, "unsupported_id_type"),
        (1.5, "unsupported_id_type"),
        (2.0**60, "unsupported_id_type"),
        ({"a": 1}, "unsupported_id_type"),
        ([1], "unsupported_id_type"),
    ],
)
def test_rejected(raw, code):
    with pytest.raises(IdError) as exc:
        normalize_id(raw)
    assert exc.value.code == code


def test_index_reports_duplicates_ambiguity_and_missing():
    feats = [
        pt(0, 0, "A"),
        pt(0, 0, "A"),
        pt(0, 0, 7),
        pt(0, 0, "7"),
        {"type": "Feature", "geometry": None, "properties": {}},
    ]
    idx, log = index_ids(feats, "asset_id")
    assert set(idx) == {"A", "7"}
    assert log.counts == {"duplicate_id": 1, "ambiguous_id": 1, "missing_id": 1}
