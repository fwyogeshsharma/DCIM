"""sysDescr and sysObjectID must name the equipment that answers.

A DCIM classifies a discovered responder by regexing sysDescr and by matching
sysObjectID against a vendor table - that is what real discovery tools do, and it
is what this project's own collector does. So the wrong equipment CLASS on those
two leaves is not a cosmetic defect: it is filed into the asset record and nothing
downstream questions it.

The bug that prompted this module: every Eaton switchgear, MCC and panelboard fell
through to the vendor fallback and announced itself as an "Eaton 9PX UPS" - a
1-3 kVA rack UPS - while its sysObjectID pointed at the Powerware UPS sub-arc. A
sweep of the electrical room filed a 4000 A switchgear lineup as a small rack UPS.
"""

from __future__ import annotations

import json

import pytest

from core.device_manager import DeviceType, Vendor
from core.snmprec_generator import SNMPRecGenerator
from core.topology_engine import TopologyEngine

TOPOLOGY = "topologies/dual_dc_enterprise.json"

# Gear that answers a sweep but is not a UPS, and still says it is. Listed rather
# than skipped so the test FAILS if the list grows; shrinking it is the fix.
#
# Empty, and it should stay that way. Eaton switchgear, Vertiv CRAHs and the APC
# probes were all here; each was a vendor fallback ending in the name of one
# specific UPS, which every device without a MODEL_SYSDESCR row then claimed to be.
KNOWN_UPS_MISDESCRIBED: set[str] = set()


@pytest.fixture(scope="module")
def estate():
    engine = TopologyEngine()
    engine.from_dict(json.load(open(TOPOLOGY, encoding="utf-8")))
    devices = engine.get_all_devices()
    assert len(devices) > 600, "topology looks truncated; do not trust this check"
    return devices


def _type(device) -> str:
    return getattr(device.device_type, "value", device.device_type)


def _answers_snmp(device) -> bool:
    """Does this device SERVE its sysDescr, or merely record it?

    Asks the generator, because the generator decides. The first version of this
    excluded only _NO_SNMP_TYPES and got it wrong: it counted all 52 environmental
    sensors as answering when not one of them has an SNMP address - they are probes
    on PDU sensor ports and gateway-attached plant instruments, read through their
    host. So it reported 20 APC probes as misdescribed ON THE WIRE when their string
    never reaches a wire at all.

    The distinction matters in both directions. A wrong string on an addressless
    device is a wrong RECORD, which the topology export carries into a DCIM and which
    is worth fixing - but it is not something a sweep can be fooled by, and a test
    that cannot tell the two apart will send somebody chasing a sweep result that
    does not exist.
    """
    return bool(SNMPRecGenerator.snmp_bind_ips(device))


# ------------------------------------------------------------------- the fix

_EATON_FACILITY = (DeviceType.SWITCHGEAR, DeviceType.MCC, DeviceType.MPP)


def test_eaton_switchgear_does_not_announce_itself_as_a_ups(estate):
    """Switchgear, an MCC and a panelboard are not a rack UPS."""
    offenders = [
        f"{d.name} ({_type(d)}): {d.sys_descr}"
        for d in estate
        if d.device_type in _EATON_FACILITY and d.vendor is Vendor.EATON
        and "ups" in d.sys_descr.lower()
    ]
    assert not offenders, "\n".join(offenders)


def test_eaton_facility_gear_is_not_on_the_powerware_ups_arc(estate):
    """sysObjectID is the leaf a discovery tool keys on FIRST.

    1.3.6.1.4.1.534.2 is Eaton's Powerware UPS branch. Fixing only the sysDescr
    would have left the machine-readable half of the identity lying, and vendor
    tooling matching on the OID would have gone and read the UPS MIB.
    """
    offenders = [
        f"{d.name} ({_type(d)}): {d.sys_oid}"
        for d in estate
        if d.device_type in _EATON_FACILITY and d.vendor is Vendor.EATON
        and d.sys_oid.startswith("1.3.6.1.4.1.534.2")
    ]
    assert not offenders, "\n".join(offenders)


def test_the_gateway_is_named_before_the_equipment_it_fronts(estate):
    """What answers is a comms gateway, not the switchgear.

    A Magnum DS breaker carries a Digitrip trip unit on INCOM and a Freedom 2100
    carries C441 relays on Modbus; neither speaks Ethernet. A sysDescr that named
    only the equipment would tell a DCIM it can poll the breaker directly, which is
    the assumption this simulator exists to stop anybody making.
    """
    for device in estate:
        if device.device_type not in _EATON_FACILITY or device.vendor is not Vendor.EATON:
            continue
        descr = device.sys_descr
        assert descr.startswith("Eaton Power Xpert Gateway"), f"{device.name}: {descr}"
        # And it still says what it fronts, or the record loses the equipment.
        assert any(w in descr for w in ("switchgear", "motor control center",
                                        "panelboard")), f"{device.name}: {descr}"


_VERTIV_COOLING = (DeviceType.CRAH,)


def test_a_chilled_water_crah_does_not_announce_itself_as_a_ups(estate):
    """28 air handlers were being suggested to an operator as UPSs.

    They fell through to the Vertiv fallback, which named a Liebert GXT5 - a
    5-10 kVA rack UPS - and the DCIM's classifier matches the UPS pattern before it
    matches the CRAH one. These are the machines the cooling alarms hang off, so a wrong
    equipment class on them is a wrong class on the estate's thermal model.
    """
    offenders = [
        f"{d.name}: {d.sys_descr}"
        for d in estate
        if d.device_type in _VERTIV_COOLING and d.vendor is Vendor.VERTIV
        and "ups" in d.sys_descr.lower()
    ]
    assert not offenders, "; ".join(offenders)


def test_the_crah_says_what_it_is_so_a_sweep_can_classify_it(estate):
    """Not circular, unlike the Eaton gateways.

    An IntelliSlot card reads the identity of the ONE unit it is fitted to off the
    iCOM controller, so a real PCW does name itself on the wire - which is why a
    DCIM classifying this row as a CRAH is reading evidence rather than a string
    this repo wrote. The Eaton gateways front a whole INCOM segment and genuinely
    cannot say which device a walk is about, which is why they get no type hint.
    """
    for device in estate:
        if device.device_type not in _VERTIV_COOLING or device.vendor is not Vendor.VERTIV:
            continue
        descr = device.sys_descr
        # The card first - it is what answers - then the unit it is fitted to.
        assert descr.startswith("Liebert IntelliSlot"), f"{device.name}: {descr}"
        assert "CRAH" in descr, f"{device.name}: {descr}"


def test_a_probe_publishes_only_the_channels_it_has():
    """A thermistor must not report humidity.

    Only reachable by calling the generator directly: no sensor in the shipped
    topology has an SNMP address, so generate_device returns before this code runs.
    It is worth pinning anyway, because the same probe read through its HOST strip
    already respects its channels - so publishing humidity here made one machine give
    two different answers about what it can measure depending on which agent you
    asked, and a DCIM polling both had no way to tell which was real.
    """
    from core.device_manager import Device
    from core.snmprec_generator import _APC_NETBOTZ

    gen = SNMPRecGenerator(output_dir="datasets/_test_identity")
    expected = {"APC AP9335T": ["Temperature"],
                "APC AP9335TH": ["Temperature", "Humidity"]}
    for model, want in expected.items():
        device = Device(name="SEN", device_type=DeviceType.SENSOR, vendor=Vendor.APC,
                        ip_address="10.0.0.9", model_name=model,
                        inlet_temp=23.4, humidity=41.0, airflow=1.2)
        labels = [value for oid, _t, value in gen._sensor_entries(device)
                  if oid.startswith(_APC_NETBOTZ + ".2.")]
        assert labels == want, f"{model} published {labels}"
        # Contiguous indices, the way a real sensor table is walked: a hole where an
        # absent channel would have sat ends a walk early on some pollers.
        idx = sorted(int(oid.rsplit(".", 1)[1])
                     for oid, _t, _v in gen._sensor_entries(device)
                     if oid.startswith(_APC_NETBOTZ + ".2."))
        assert idx == list(range(1, len(want) + 1)), f"{model} indices {idx}"


#: Vendors with no IANA enterprise number in this repo, whose gear therefore keeps
#: the "1.3.6.1.4.1.0.0" fallback. Enterprise 0 is not assignable, so it identifies
#: nothing - but a GUESSED PEN is worse, because vendor tooling matches on this leaf
#: and would go and read the wrong MIB. Listed so the list cannot grow quietly, and
#: so sourcing one is a visible piece of work rather than a vague intention.
KNOWN_UNASSIGNED_SYSOID = {
    "ASCO 7000 Series 4000A",
    "ASCO 7000 Paralleling Switchgear",
    "CoolIT CHx80",
    "LOYTEC LINX-151",
    # Not plant gear, and not found by looking for it - this test found it. A CAT
    # EMCP reaches a network through a gateway whose PEN could not be sourced;
    # F5's (3375) and Palo Alto's (25461) were not in doubt and are now set.
    "Caterpillar 3516B",
}

_UNASSIGNED = "1.3.6.1.4.1.0.0"


def test_nothing_on_the_wire_answers_as_a_generic_device(estate):
    """"Generic Device" tells a DCIM nothing - not even the vendor.

    22 devices answered with it: the ASCO transfer switches and paralleling
    switchgear, the CoolIT CDUs, the LOYTEC BACnet routers and the Moxa Modbus
    gateways. Every one of those vendors was simply absent from VENDOR_SYSDESCR, so
    a sweep of the plant returned rows an operator could do nothing with.
    """
    offenders = [f"{d.name} ({_type(d)}): {d.model_name}"
                 for d in estate
                 if _answers_snmp(d) and d.sys_descr == "Generic Device"]
    assert not offenders, "; ".join(offenders[:10])


def test_a_gateway_says_it_is_a_gateway_and_which_protocol(estate):
    """The two devices whose whole job is to stand in front of something else.

    Both serve ONLY MIB-II, no enterprise tree, and that is correct rather than
    incomplete: the agent answers "the gateway is up" and says nothing about the
    field devices behind it. The 12 plant instruments behind the MGates have no
    addresses at all, so a DCIM that read a reachable gateway as evidence those
    instruments are healthy would be wrong in the most expensive direction.
    """
    wanted = {
        DeviceType.MODBUS_GATEWAY: ("Modbus", "gateway"),
        DeviceType.BACNET_ROUTER: ("BACnet", "router"),
    }
    found = set()
    for device in estate:
        words = wanted.get(device.device_type)
        if words is None or not _answers_snmp(device):
            continue
        found.add(device.device_type)
        for word in words:
            assert word in device.sys_descr, f"{device.name}: {device.sys_descr}"
    assert found == set(wanted), f"types not exercised: {set(wanted) - found}"


def test_an_unidentifiable_sysobjectid_is_declared_not_invented(estate):
    """The debt that is left, pinned from both ends.

    A new model must not quietly join the unassigned list, and a model that gains a
    real enterprise arc must be removed from it - otherwise the list stops describing
    the estate and starts excusing it.
    """
    on_wire = [d for d in estate if _answers_snmp(d)]
    unassigned = {d.model_name for d in on_wire if d.sys_oid == _UNASSIGNED}

    new = unassigned - KNOWN_UNASSIGNED_SYSOID
    assert not new, ("models with no sysObjectID that were not already declared: "
                     + ", ".join(sorted(new))
                     + " - source the vendor's PEN, or add it here deliberately")
    stale = KNOWN_UNASSIGNED_SYSOID - unassigned
    assert not stale, ("these now have a real arc, so remove them: "
                       + ", ".join(sorted(stale)))

    # And the one that IS sourced stays sourced. Moxa's MIBs hang off 8691.
    moxa = [d for d in on_wire if d.model_name == "Moxa MGate MB3480"]
    assert moxa, "no Moxa gateway on the wire; this check is not exercising anything"
    for device in moxa:
        assert device.sys_oid == "1.3.6.1.4.1.8691", device.sys_oid


def test_a_router_and_a_switch_do_not_answer_with_the_same_sysdescr(estate):
    """The defect underneath "every Cisco router classifies as a switch".

    An ASR 1001-X, an ISR 4431 and a Catalyst 9300-48T all served

        Cisco IOS XE Software, Version 17.9.4a, RELEASE SOFTWARE (fc3)

    byte for byte. No classifier can separate those, so the DCIM's IOS heuristic was
    not really wrong - it was guessing, because the simulator had removed the
    evidence. A real IOS-XE sysDescr names the image, and the image names the
    platform.
    """
    routers = {d.sys_descr for d in estate
               if d.device_type is DeviceType.ROUTER and _answers_snmp(d)}
    switches = {d.sys_descr for d in estate
                if d.device_type in (DeviceType.SWITCH, DeviceType.OOB_SWITCH)
                and _answers_snmp(d)}
    shared = routers & switches
    assert not shared, ("a router and a switch answer identically, so nothing on the "
                        "wire can tell them apart: " + "; ".join(sorted(shared)))


def test_a_cisco_sysdescr_names_its_image(estate):
    """The image is the evidence, so it has to be there.

    Cisco puts the image in sysDescr and the image names the platform - "ISR
    Software", "ASR1000 Software", "Catalyst L3 Switch Software (CAT9K_IOSXE)",
    "C2960X Software". Without it the string is just an OS version shared across the
    whole catalogue, which is what it had become.
    """
    markers = ("ISR Software", "ASR1000 Software", "ASR9K", "Catalyst L3 Switch",
               "C2960X Software", "C1000 Software", "NX-OS")
    offenders = [f"{d.model_name}: {d.sys_descr}"
                 for d in estate
                 if _answers_snmp(d) and str(d.model_name).startswith("Cisco")
                 and not any(m in d.sys_descr for m in markers)]
    assert not offenders, "; ".join(sorted(set(offenders)))


def test_a_firewall_says_it_is_a_firewall(estate):
    """PAN-OS names the model and the equipment class, so the string should too.

    It used to read "Palo Alto Networks PAN-OS, Version 11.0.2" - the OS and nothing
    else - so a sweep could tell the vendor and not the role. A real PA-5220 answers
    "Palo Alto Networks PA-5220 series firewall".
    """
    seen = 0
    for device in estate:
        if device.device_type is not DeviceType.FIREWALL or not _answers_snmp(device):
            continue
        seen += 1
        assert "firewall" in device.sys_descr.lower(), \
            f"{device.name}: {device.sys_descr}"
    assert seen, "no firewall on the wire; this check is not exercising anything"


def test_the_f5_deliberately_does_not_name_itself(estate):
    """The awkward truth, pinned so nobody tidies it away.

    TMOS runs on a Linux host and BIG-IP answers sysDescr with that host's uname: no
    "BIG-IP", no "load balancer", nothing about what the box is for. That is why F5
    monitoring reads sysObjectID and the F5-BIGIP-SYSTEM-MIB instead, and modelling
    it honestly is what makes this simulator exercise that path.

    Writing a friendlier string here would make the DCIM's job easier and teach a
    collector something false - that sysDescr is always enough. The enterprise OID
    carries the answer and must therefore be right.
    """
    seen = 0
    for device in estate:
        if device.device_type is not DeviceType.LOAD_BALANCER:
            continue
        if not _answers_snmp(device):
            continue
        seen += 1
        descr = device.sys_descr.lower()
        assert "big-ip" not in descr and "load balanc" not in descr, (
            f"{device.name} names its product in sysDescr; a real BIG-IP does not, "
            f"and the OID is what identifies it: {device.sys_descr}")
        assert device.sys_oid.startswith("1.3.6.1.4.1.3375"), (
            f"{device.name}: sysDescr says nothing, so the OID is the only thing "
            f"that can - and it is {device.sys_oid}")
    assert seen, "no load balancer on the wire; this check is not exercising anything"


def test_two_models_are_always_distinguishable_on_the_wire(estate):
    """Something has to tell one model from another, or inventory cannot.

    Sharing a sysDescr is legitimate and real: a Catalyst 9300-48P and a 9300-48T run
    the same CAT9K image and report the same string, and the model comes from
    sysObjectID or ENTITY-MIB. What is NOT legitimate is sharing BOTH leaves, which is
    what a missing MODEL_SYSDESCR row produces - the model falls through to the vendor
    fallback and answers as whatever device that string describes.

    It has happened twice: 8 Catalyst 9300-48Ts served the core routers' bytes, and a
    48-port 1G Dell OOB switch announced a 25G leaf's SONiC HwSku.
    """
    by_identity = {}
    for device in estate:
        if not _answers_snmp(device) or not device.model_name:
            continue
        by_identity.setdefault((device.sys_descr, device.sys_oid), set()).add(
            device.model_name)

    clashes = {k: v for k, v in by_identity.items() if len(v) > 1}
    assert not clashes, "models that answer identically on BOTH leaves: " + "; ".join(
        sorted(", ".join(sorted(v)) for v in clashes.values()))


def test_a_model_does_not_answer_with_another_model_name(estate):
    """The symptom that makes a fall-through obvious.

    A Dell N3248TE-ON reporting "HwSku: DellEMC-S5248f" is not a vague string, it is
    the wrong switch - and an operator reading the sweep would record the wrong SKU.
    """
    names = {d.model_name for d in estate if d.model_name}
    # The distinctive part of a SKU, not the vendor prefix every Dell shares.
    def tokens(model):
        return [t for t in str(model).replace("-", " ").split()
                if len(t) >= 5 and any(c.isdigit() for c in t)]

    offenders = []
    for device in estate:
        if not _answers_snmp(device) or not device.model_name:
            continue
        descr = device.sys_descr.lower()
        mine = {t.lower() for t in tokens(device.model_name)}
        for other in names:
            if other == device.model_name:
                continue
            theirs = {t.lower() for t in tokens(other)} - mine
            hit = [t for t in theirs if t in descr]
            if hit:
                offenders.append(f"{device.model_name} answers with {other}'s {hit}")
    assert not offenders, "; ".join(sorted(set(offenders))[:6])


# --------------------------------------------------------------- the rest of it

def test_no_new_gear_claims_to_be_a_ups(estate):
    """The debt, pinned. This fails when somebody adds a new offender."""
    offending_types = {
        _type(d) for d in estate
        if d.device_type is not DeviceType.UPS and _answers_snmp(d)
        and "ups" in d.sys_descr.lower()
    }
    new = offending_types - KNOWN_UPS_MISDESCRIBED
    assert not new, (
        "device types announcing a UPS that were not already known: "
        + ", ".join(sorted(new))
        + " - fix the sysDescr rather than adding it to KNOWN_UPS_MISDESCRIBED")
    # And the known list must not rot: an entry that no longer offends should be
    # deleted, so the list always describes the real state.
    stale = KNOWN_UPS_MISDESCRIBED - offending_types
    assert not stale, ("fixed, so remove from KNOWN_UPS_MISDESCRIBED: "
                       + ", ".join(sorted(stale)))


def test_no_device_records_itself_as_a_ups_either(estate):
    """The record half, which the topology export carries into the DCIM.

    _answers_snmp deliberately excludes the 52 addressless sensors, so the test above
    cannot see them. Their sysDescr is still a field a DCIM reads - the importer takes
    it off the export, not off a sweep - and "APC Smart-UPS" on a temperature probe is
    just as wrong in an asset record as it would be on the wire.
    """
    offenders = [
        f"{d.name} ({_type(d)}): {d.sys_descr}"
        for d in estate
        if d.device_type is not DeviceType.UPS and "ups" in d.sys_descr.lower()
    ]
    assert not offenders, "; ".join(offenders[:8])


def test_nothing_that_answers_snmp_says_it_has_no_snmp_agent(estate):
    """A contradiction a DCIM cannot resolve.

    The Schneider vendor fallback read "passive distribution, no SNMP agent" on a
    device that answered a sweep. Saying so is FINE where it is true - the APC probes
    say "no network interface of its own" and have none - and only a contradiction
    when the device is serving the sentence denying it has an agent.
    """
    offenders = [
        f"{d.name} ({_type(d)}): {d.sys_descr}"
        for d in estate
        if _answers_snmp(d) and "no snmp agent" in d.sys_descr.lower()
    ]
    assert not offenders, "\n".join(offenders)
