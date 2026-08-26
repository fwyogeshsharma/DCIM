"""Network gear publishes its control-plane CPU where its vendor really does.

A switch is not a host. HOST-RESOURCES is a HOST MIB, the network agents on
this plane did not carry it, and a poller asking a spine for hrProcessorLoad
got noSuchInstance - verified against the running plane before this was
written. So control-plane load, the number that decides whether the box still
answers a keepalive, reached an NMS only as a trap: nothing to graph, nothing
to threshold, and on the 12 firewalls and 4 load balancers - which speak no
gNMI either, because PAN-OS and F5 TMOS genuinely do not - nothing at all.

Two families, because two kinds of NOS:

* CISCO-PROCESS-MIB and CISCO-MEMORY-POOL-MIB for IOS and NX-OS, which is what
  every Cisco-aware tool polls;
* HOST-RESOURCES and UCD for the Linux-based ones - Dell OS10, PAN-OS, F5
  TMOS, Arista EOS - which serve the host MIBs because underneath they are
  Linux.

The value comes from the same `device.cpu_usage` the ticker walks, so SNMP and
gNMI cannot disagree about the same box. That is not hypothetical: this fleet
spent days with gNMI serving a frozen dataset while nothing else published the
number at all.
"""

import pytest

from core.device_manager import Device, DeviceType, Vendor
from core.snmprec_generator import (
    CISCO_CPU_1MIN,
    CISCO_CPU_5MIN,
    CISCO_MEM_FREE,
    CISCO_MEM_USED,
    HR_LOAD,
    UCD_CPU,
    UCD_MEM,
    SNMPRecGenerator,
)


def _dev(dtype, vendor, cpu=57, mem_total=8 * 1024 ** 3, mem_used=3 * 1024 ** 3):
    d = Device(name=f"{dtype.value}-1", device_type=dtype, vendor=vendor,
               ip_address="10.50.0.9", mgmt_ip="10.51.0.9")
    d.cpu_usage = cpu
    d.memory_total = mem_total
    d.memory_used = mem_used
    return d


@pytest.fixture
def gen(tmp_path):
    return SNMPRecGenerator(str(tmp_path))


def _oids(entries):
    """An OidEntry is a (oid, type, value) tuple on this plane."""
    return {oid: value for oid, _typ, value in entries}


# ------------------------------------------------------------------- Cisco


def test_cisco_switch_publishes_the_process_mib(gen):
    """cpmCPUTotal5minRev is the object an NMS graphs on Cisco gear."""
    oids = _oids(gen._network_cpu_entries(_dev(DeviceType.SWITCH,
                                               Vendor.CISCO_SYSTEMS, cpu=57)))
    assert oids[f"{CISCO_CPU_5MIN}.1"] == "57"
    assert oids[f"{CISCO_CPU_1MIN}.1"] == "57"


def test_cisco_publishes_the_memory_pool_in_bytes(gen):
    """ciscoMemoryPoolUsed/Free are BYTES, and they must sum to the total.

    A pool whose used and free do not add up is how a utilisation gauge ends up
    over 100% on a chassis nobody has touched.
    """
    dev = _dev(DeviceType.ROUTER, Vendor.CISCO_SYSTEMS,
               mem_total=8 * 1024 ** 3, mem_used=3 * 1024 ** 3)
    oids = _oids(gen._network_cpu_entries(dev))
    used = int(oids[f"{CISCO_MEM_USED}.1"])
    free = int(oids[f"{CISCO_MEM_FREE}.1"])
    assert used == 3 * 1024 ** 3
    assert used + free == 8 * 1024 ** 3


def test_cisco_does_not_pretend_to_be_a_host(gen):
    """No host MIBs on IOS. Publishing them would make the fiction convincing
    enough for a poller to prefer them over the vendor objects."""
    oids = _oids(gen._network_cpu_entries(_dev(DeviceType.SWITCH,
                                               Vendor.CISCO_SYSTEMS)))
    assert not any(o.startswith(HR_LOAD) for o in oids)
    assert not any(o.startswith(UCD_MEM) for o in oids)


# ------------------------------------------------- Linux-based NOS (Dell, PAN, F5)


@pytest.mark.parametrize("dtype,vendor", [
    (DeviceType.SWITCH, Vendor.DELL),
    (DeviceType.FIREWALL, Vendor.PALO_ALTO_NETWORKS),
    (DeviceType.LOAD_BALANCER, Vendor.F5_NETWORKS),
])
def test_linux_nos_publishes_the_host_mibs(gen, dtype, vendor):
    oids = _oids(gen._network_cpu_entries(_dev(dtype, vendor, cpu=42)))
    assert oids[f"{HR_LOAD}.1"] == "42"
    assert oids[f"{UCD_CPU}.9.0"] == "42"
    assert int(oids[f"{UCD_MEM}.5.0"]) > 0


def test_the_firewall_and_load_balancer_have_no_other_way_to_say_it(gen):
    """The reason this matters more for these two than for a spine.

    PAN-OS and F5 TMOS do not serve gNMI - the simulator deliberately does not
    generate datasets for them - so SNMP is not a second source here, it is the
    only one. Before this, a CPU fault injected on a firewall was visible as a
    trap and nowhere else.
    """
    from core.gnmi_data_generator import _GNMI_TYPES

    assert DeviceType.FIREWALL not in _GNMI_TYPES
    assert DeviceType.LOAD_BALANCER not in _GNMI_TYPES

    for dtype, vendor in ((DeviceType.FIREWALL, Vendor.PALO_ALTO_NETWORKS),
                          (DeviceType.LOAD_BALANCER, Vendor.F5_NETWORKS)):
        oids = _oids(gen._network_cpu_entries(_dev(dtype, vendor)))
        assert f"{HR_LOAD}.1" in oids


def test_the_published_cpu_is_the_ticker_s_own_number(gen):
    """SNMP and gNMI read one source or they will disagree about one box."""
    dev = _dev(DeviceType.SWITCH, Vendor.DELL, cpu=88)
    oids = _oids(gen._network_cpu_entries(dev))
    assert oids[f"{HR_LOAD}.1"] == str(dev.cpu_usage)


def test_cpu_is_clamped_to_a_percentage(gen):
    """A gauge that leaves 0-100 is a poller bug hunt, not a fault."""
    dev = _dev(DeviceType.SWITCH, Vendor.DELL)
    dev.cpu_usage = 140
    assert _oids(gen._network_cpu_entries(dev))[f"{HR_LOAD}.1"] == "100"
    dev.cpu_usage = -5
    assert _oids(gen._network_cpu_entries(dev))[f"{HR_LOAD}.1"] == "0"
