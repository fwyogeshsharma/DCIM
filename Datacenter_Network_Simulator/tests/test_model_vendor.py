"""A part number has one maker.

tools/fill_cold_plate_loops.py re-SKUed air servers to liquid parts and left the
vendor behind: 23 Hall A servers were "HPE"/"Lenovo" on a Supermicro SYS-221H-TNR
LCC, so their BMCs announced iLO/XCC on a Supermicro board and a DCIM sweep read
an identity no real machine has.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.device_models import follow_model_vendor, vendor_of_model

TOPOLOGIES = sorted((Path(__file__).resolve().parents[1] / "topologies").glob("*.json"))


@pytest.mark.parametrize("path", TOPOLOGIES, ids=lambda p: p.name)
def test_every_device_is_made_by_its_models_maker(path):
    nodes = json.loads(path.read_text(encoding="utf-8")).get("nodes") or []
    wrong = []
    for n in nodes:
        d = n.get("device", n)
        maker = vendor_of_model(d.get("model_name") or "")
        if maker is not None and d.get("vendor") != maker.value:
            wrong.append(f"{d['name']}: {d.get('vendor')} on {d.get('model_name')}")
    assert not wrong, f"{len(wrong)} device(s), e.g. " + "; ".join(wrong[:5]) + \
        " - run tools/fix_model_vendor.py"


def test_following_the_model_renames_the_bmc_port_and_keeps_its_index():
    d = {"name": "SRV", "vendor": "Hewlett Packard Enterprise",
         "model_name": "Supermicro SYS-221H-TNR LCC",
         "interfaces": [{"name": "eth1/1"}, {"name": "eth1/2"}, {"name": "iLO"}]}
    assert follow_model_vendor(d) == ("Hewlett Packard Enterprise", "Supermicro")
    assert d["vendor"] == "Supermicro"
    assert [i["name"] for i in d["interfaces"]] == ["eth1/1", "eth1/2", "IPMI"]
    assert follow_model_vendor(d) is None, "a second pass changes nothing"


def test_an_unknown_model_says_nothing_about_its_maker():
    d = {"name": "X", "vendor": "Lenovo", "model_name": "Homebuilt 1U"}
    assert follow_model_vendor(d) is None and d["vendor"] == "Lenovo"
