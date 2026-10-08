"""Store/wire byte-parity harness -- a refactor must change names, not bytes.

Characterization tests: reconstruct a representative order for every codec branch via the
current order API and assert the on-disk fingerprint bytes and the outgoing wire params match
the golden literals captured from this same code. Green here is expected (same code produced the
goldens); the harness becomes load-bearing under any refactor that moves or renames the
construction while these goldens stay fixed -- equal output then proves the refactor changed
names, not the bytes that reach disk or the wire.

The fixtures module is imported by bare name: with no ``__init__.py`` under ``tests/`` the
pytest ``prepend`` import mode puts this test's directory (``tests/parity``) on ``sys.path``, so
its sibling ``golden_fingerprints`` resolves directly.
"""

from __future__ import annotations

import pytest
from golden_fingerprints import (
    GOLDEN_FINGERPRINT,
    GOLDEN_WIRE,
    LEGACY_ROWS,
    ORDER_MATRIX,
    build_order,
    build_wire,
)

from kis_trader.order import decode_fingerprint, encode_fingerprint


def test_matrix_and_goldens_cover_the_same_labels() -> None:
    """Every matrix lane has a captured fingerprint and wire golden (and vice versa) -- so a lane
    added to the matrix without re-capturing (or a stale golden) fails loudly."""
    labels = [label for label, _ in ORDER_MATRIX]
    assert labels == list(GOLDEN_FINGERPRINT)
    assert labels == list(GOLDEN_WIRE)
    assert len(labels) == len(set(labels))  # no duplicate lane labels


@pytest.mark.parametrize("label", list(GOLDEN_FINGERPRINT))
def test_fingerprint_bytes_match_golden(label: str) -> None:
    assert tuple(encode_fingerprint(build_order(label))) == GOLDEN_FINGERPRINT[label]


@pytest.mark.parametrize("label", list(GOLDEN_WIRE))
def test_wire_params_match_golden(label: str) -> None:
    assert build_wire(label) == GOLDEN_WIRE[label]


@pytest.mark.parametrize("label", list(GOLDEN_FINGERPRINT))
def test_decode_roundtrip_stable(label: str) -> None:
    """Decoding a golden row and re-encoding yields the byte-identical row (codec is a stable
    round-trip for the canonical 19-slot form)."""
    row = list(GOLDEN_FINGERPRINT[label])
    assert tuple(encode_fingerprint(decode_fingerprint(row))) == tuple(row)


@pytest.mark.parametrize("label", list(LEGACY_ROWS))
def test_legacy_decode_normalizes(label: str) -> None:
    """Short legacy-width rows (8/13/14/16 slots) decode and re-encode to the canonical 19-slot
    form. Legacy domestic-stock MICs normalize to ``XKRX``; slot-12 ``UN`` decodes to the in-memory
    order exchange ``SOR`` and re-encodes as ``UN``."""
    row = LEGACY_ROWS[label]
    fingerprint = decode_fingerprint(row)
    out = encode_fingerprint(fingerprint)
    assert len(out) == 19
    assert len(out) != len(row)                              # padded, not byte-identical
    assert out[7] == ("XKRX" if row[7] in ("XKOS", "NXTE") else str(row[7]))
    if label == "v9_xkos_16slot":
        assert fingerprint.exchange == "SOR"
        assert out[12] == "UN"
