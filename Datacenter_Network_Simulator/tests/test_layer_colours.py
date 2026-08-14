"""Every topology layer must be visually distinct, and the legend must agree
with the lines.

Two failure modes, both silent — no error, just an unreadable canvas:
  * two layers sharing a hex value, so their links are indistinguishable;
  * a layer missing from LAYER_COLOR, so LinkEdge falls back to LAYER_DEFAULT
    (production blue) while the filter button's swatch reads the CSS var and
    shows something else entirely.

Both shipped when the fieldbus layer was added.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

WEBUI = Path(__file__).resolve().parents[1] / "webui" / "src"
CSS = WEBUI / "index.css"
THEME = WEBUI / "theme.ts"
TOPOLOGY = Path(__file__).resolve().parents[1] / "topologies" / "dual_dc_enterprise.json"


def _css_layer_colours() -> dict:
    text = CSS.read_text(encoding="utf-8")
    return {m.group(1): m.group(2).lower()
            for m in re.finditer(r"--layer-([a-z]+):\s*(#[0-9a-fA-F]{3,8})", text)}


def _layer_color_keys() -> set:
    body = THEME.read_text(encoding="utf-8").split("LAYER_COLOR", 1)[1]
    body = body.split("}", 1)[0]
    return set(re.findall(r"^\s*([a-z_]+):", body, re.M))


def test_no_two_layers_share_a_colour():
    colours = _css_layer_colours()
    assert colours, "no --layer-* colours found"
    seen: dict = {}
    for name, hexv in colours.items():
        assert hexv not in seen, (
            f"--layer-{name} and --layer-{seen[hexv]} are both {hexv} — their "
            f"links would be indistinguishable on the canvas")
        seen[hexv] = name


def test_every_layer_in_use_has_a_registered_colour():
    """LinkEdge does LAYER_COLOR[layer] || LAYER_DEFAULT. An unregistered layer
    renders as production blue while its filter swatch shows its own colour."""
    data = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    used = {e.get("layer") for e in data["edges"] if e.get("layer")}
    registered = _layer_color_keys()
    missing = used - registered
    assert not missing, f"layers on the canvas with no LAYER_COLOR entry: {missing}"


def test_registered_layers_resolve_to_a_real_css_var():
    css = _css_layer_colours()
    alias = {"production": "prod", "management": "mgmt"}
    for key in _layer_color_keys():
        var = alias.get(key, key)
        assert var in css, f"LAYER_COLOR['{key}'] points at --layer-{var}, which is not defined"


def test_fieldbus_is_not_the_management_colour():
    """A fieldbus drop is precisely what is NOT on the management plane; showing
    them in one colour defeats the reason the layer was split out."""
    css = _css_layer_colours()
    assert css.get("fieldbus") and css.get("mgmt")
    assert css["fieldbus"] != css["mgmt"]
