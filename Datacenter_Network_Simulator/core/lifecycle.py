"""What a device's lifecycle state means ON THE WIRE.

This is NOT telemetry, and no real device reports it. A switch does not announce
"I am racked but not yet accepted"; lifecycle lives in the DCIM, entered by people
and moved by process. What this module models is the other half - the physical
consequence of that state, which is what a monitoring system actually sees.

So the simulator carries the field for one reason: to decide what answers. A
DCIM pointed at this simulator should still keep its own lifecycle record and
still require an operator to move it, exactly as it would against real metal.
It must not import this field and call it inventory; if it did, the one thing
worth testing - a DCIM whose record disagrees with the floor - becomes
impossible to reproduce.

What each state looks like to a poller
--------------------------------------

planned
in_stock        Nothing. No address bound, no SNMP dataset, in no protocol
                server's registry. Hardware that does not exist yet, or sits
                boxed on a shelf, does not answer ICMP either.

installed       Racked, cabled, powered, and not yet handed over. What answers
                depends on what kind of box it is, and the difference is real:

                  a SERVER answers on its BMC and nowhere else. You rack it,
                  patch the iDRAC/iLO/XCC into the OOB switch, and the
                  controller is up long before an OS exists - so Redfish and the
                  BMC's own SNMP agent respond while the production NIC has no
                  agent listening at all. This is the state a real commissioning
                  window spends hours in, and it is the one a DCIM most often
                  gets wrong: a Redfish endpoint ONLINE and an SNMP endpoint
                  OFFLINE on the same device is correct here, not a fault.

                  a SWITCH, ROUTER or piece of facility gear answers normally.
                  It has been through ZTP or had its point list mapped; it is
                  configured and reachable, and only the cut-over is pending.
                  On the wire it is indistinguishable from in_service, and
                  pretending otherwise would be inventing a difference that
                  does not exist. The distinction for these is a DCIM fact -
                  suppress the alarms, do not expect silence.

in_service      Everything.

maintenance     Everything. Real gear does not stop reporting because a ticket
                says somebody is working on it - that is the whole reason alarm
                shelving exists as a separate idea from reachability. A device
                that went quiet during planned work would be indistinguishable
                from one that died during it.

decommissioned
retired         Nothing, as for planned - unracked or disposed of.

Being off the wire this way is the SECOND reason a device can be dark; the first
is having no live power cord (see core.dark_firewall). They are deliberately
separate causes with one shared effect, so every consumer asks one question
(`is_offline`) and no consumer has to remember there are two answers.
"""
from __future__ import annotations

from typing import Iterable

#: Every state, in the order a device passes through them. Mirrors the DCIM's
#: `lifecycle_t`, minus nothing: a state the DCIM can hold is a state the
#: simulator has to be able to put a device into, or the pair cannot be tested
#: against each other.
STATES: tuple[str, ...] = (
    "planned", "in_stock", "installed", "in_service", "maintenance",
    "decommissioned", "retired",
)

DEFAULT = "in_service"

#: States where the box is physically present, powered and cabled. Everything
#: else is either not built yet or already gone, and answers nothing.
_ON_WIRE = frozenset({"installed", "in_service", "maintenance"})

#: The one state where a server's OS agent is not up yet. Kept as a set rather
#: than an `== "installed"` so the reason is named at the point of use.
_NO_OS_AGENT = frozenset({"installed"})


def state_of(device) -> str:
    """This device's state, defaulting for a topology written before the field.

    Read with getattr rather than an attribute so the dataset generators - which
    are handed plain objects in some tests - do not need the real dataclass.
    """
    s = getattr(device, "lifecycle", "") or DEFAULT
    return s if s in STATES else DEFAULT


def normalise(state: str | None) -> str:
    """A state string as it will be stored, or DEFAULT for anything unknown."""
    s = (state or "").strip().lower()
    return s if s in STATES else DEFAULT


def is_valid(state: str | None) -> bool:
    return (state or "").strip().lower() in STATES


def on_wire(device) -> bool:
    """Is this box physically present, powered and reachable at all."""
    return state_of(device) in _ON_WIRE


def is_offline(device) -> bool:
    """The inverse, named for what a poller experiences rather than for a state.

    This is the question every consumer should ask. It says nothing about POWER -
    a device can be on the wire by lifecycle and dark because its cords are
    switched off, and `core.device_state_store` combines the two.
    """
    return not on_wire(device)


def os_agent_up(device) -> bool:
    """Is there an agent on this device's PRODUCTION address.

    False for a server that is racked but not yet built: the OS is not installed,
    so nothing is listening on the production NIC even though the chassis is
    powered and its BMC is answering. True for network and facility gear in the
    same state, which is configured and serving its own agent.
    """
    if not on_wire(device):
        return False
    if state_of(device) not in _NO_OS_AGENT:
        return True
    return not _is_server(device)


def bmc_up(device) -> bool:
    """Is this device's management controller answering.

    A BMC runs on chassis standby power and comes up with the cords, before any
    OS and independently of one. So it follows presence and nothing else.
    """
    return on_wire(device)


def _is_server(device) -> bool:
    # By value rather than by importing DeviceType, which would make
    # core.device_manager unable to import this module.
    dt = getattr(device, "device_type", None)
    return getattr(dt, "value", dt) == "server"


def offline_names(devices: Iterable) -> set:
    """Names of every device held off the wire by its lifecycle.

    The shape `core.device_state_store` publishes to the generators, which have
    no device manager reference of their own.
    """
    return {d.name for d in devices if is_offline(d)}
