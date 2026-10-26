"""Regression tests for the shared Arc Reactor application icon."""

from pathlib import Path

import icon_art


ROOT = Path(icon_art.__file__).resolve().parent


def test_shared_arc_reactor_svg_source_exists():
    source = ROOT / "assets" / "jarvis-reactor-source.svg"
    assert source.is_file()
    text = source.read_text(encoding="utf-8")
    assert "<svg" in text
    assert "#ffbd4a" in text
    assert "#20e5ff" in text


def test_runtime_icon_renders_from_shared_source():
    source = icon_art._source_art_path()
    assert source is not None
    assert source.name == "jarvis-reactor-source.svg"

    image = icon_art.render_icon(64)
    assert not image.isNull()
    assert image.width() == 64
    assert image.height() == 64


def test_no_legacy_icon_fallback_exists():
    # Missing/invalid canonical artwork must fail rather than show the old logo.
    assert not hasattr(icon_art, "draw_icon")
    source = (ROOT / "icon_art.py").read_text(encoding="utf-8")
    assert "Refusing to display a legacy icon." in source
