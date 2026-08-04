"""write_receipt_to + format_receipt: pure file/string logic, no DB."""

import sill


PLACEHOLDER = "Stored: MINT-PENDING — no receipt yet"


def test_placeholder_uses_em_dash():
    assert "—" in sill.RECEIPT_PLACEHOLDER
    assert sill.RECEIPT_PLACEHOLDER == PLACEHOLDER


def test_replaces_exact_anchor_preserving_indent_and_eol(tmp_path):
    f = tmp_path / "j.md"
    f.write_text("head\n  " + PLACEHOLDER + "\ntail\n")
    msg = sill.write_receipt_to(str(f), "Stored: abc123 [2 tags]")
    assert "line 2" in msg
    assert f.read_text() == "head\n  Stored: abc123 [2 tags]\ntail\n"


def test_replaces_final_line_without_trailing_newline(tmp_path):
    f = tmp_path / "j.md"
    f.write_text("head\n" + PLACEHOLDER)
    sill.write_receipt_to(str(f), "Stored: abc123")
    assert f.read_text() == "head\nStored: abc123"


def test_backticked_placeholder_is_mention_not_anchor(tmp_path):
    f = tmp_path / "j.md"
    f.write_text("`" + PLACEHOLDER + "`\n")
    msg = sill.write_receipt_to(str(f), "Stored: abc123")
    assert "NOT written" in msg and "longer line" in msg
    assert PLACEHOLDER in f.read_text()


def test_zero_and_multi_anchor_messages(tmp_path):
    f0 = tmp_path / "none.md"
    f0.write_text("no anchor here\n")
    assert "no placeholder anchor" in sill.write_receipt_to(str(f0), "Stored: x")
    f2 = tmp_path / "two.md"
    f2.write_text(PLACEHOLDER + "\n" + PLACEHOLDER + "\n")
    msg = sill.write_receipt_to(str(f2), "Stored: x")
    assert "2 placeholder anchors" in msg and "NOT written" in msg


def test_missing_file_never_raises(tmp_path):
    msg = sill.write_receipt_to(str(tmp_path / "absent.md"), "Stored: x")
    assert "not found" in msg


def test_format_receipt_branches():
    assert sill.format_receipt("abc", ["a", "b"], "Ada", "assertive") == \
        "Stored: abc [2 tags] [Ada/assertive]"
    no_tags = sill.format_receipt("abc", [], "Ada", None)
    assert "WARNING: no concept tags" in no_tags and "[Ada/untagged]" in no_tags
