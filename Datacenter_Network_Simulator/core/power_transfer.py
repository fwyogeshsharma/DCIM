"""
Utility → generator transfer sequence, per datacenter.

Models the electrical upstream of a Tier III site the way the real gear behaves:

    UTIL ──> SWGR (utility main) ──┐
                                   ├──> ATS-A ──> UPS-A ──> RPP-A ──> racks
    GEN1 ─┐                        │         └──> MCC-A ──> chillers/pumps/CRAH (A)
    GEN2 ─┴──> SWGR (paralleling) ─┘
                                   └──> ATS-B ──> UPS-B ──> RPP-B ──> racks
                                             └──> MCC-B ──> chillers/pumps/CRAH (B)

Sequence of events on a utility failure, and why each delay exists:

  t+0.0   Utility lost. The ATS's normal source dies. IT rides through on the
          UPS batteries; the MCCs go dead, so every chiller, pump and CRAH stops.
          The chilled-water loop coasts on its own thermal mass.
  t+3.0   ATS time-delay-normal-to-emergency (TDNE) expires. This delay exists so
          a momentary sag or a reclose doesn't crank the gensets. Engine start
          signal goes out over the ATS's start contacts.
  t+10.0  Gensets reach rated voltage and frequency. NFPA 110 Level 1 Type 10
          requires the emergency source to be available within 10 s of the outage,
          which is where the "Type 10" designation comes from.
  t+12.0  ATS transfers to the emergency source (open transition — a short dead
          time as the contacts break before make).
  t+17.0  Load block 1: chilled- and condenser-water pumps.
  t+27.0  Load block 2: chillers and cooling towers.
  t+37.0  Load block 3: CRAHs, control valves, CDUs.

The blocks exist because a genset cannot absorb its full mechanical load in one
step — a large motor start collapses the bus voltage and can stall the engine.
Real sequencers stage mechanical load 5–10 s apart.

On utility return the ATS waits out a long time-delay-emergency-to-normal (TDEN)
before transferring back, so a flapping utility doesn't beat the load up. After
retransfer the gensets keep running unloaded for a cooldown period, then stop.

Vendors: ASCO 7000/4000 series, Eaton ATC-900, Generac PSTS, Russelectric.
The transfer switch is the SNMP-manageable object that owns this sequence; the
gensets only see a dry start contact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Set


# ── Timings (seconds) ─────────────────────────────────────────────────────────
# TDNE and the crank time are field-adjustable on real gear; these are the
# defaults that satisfy NFPA 110 Level 1 Type 10 (emergency source available
# within 10 s of the outage).
TDNE_S         = 3.0     # time delay normal→emergency, before the engine start signal
GEN_START_S    = 7.0     # crank → rated voltage & frequency (3.0 + 7.0 = the 10 s)
ATS_XFER_S     = 2.0     # open-transition dead time while the contacts move
UPS_ACCEPT_S   = 3.0     # UPS rectifier qualifies the genset source and comes back online

# Mechanical load-add steps, measured from the instant the ATS closes onto
# emergency. Staged so no single step is a large fraction of genset capacity.
MECH_BLOCK_S = (5.0, 15.0, 25.0)
MECH_BLOCKS = (
    frozenset({"pump"}),                        # block 1 — CHW + CW pumps
    frozenset({"chiller", "cooling_tower"}),    # block 2 — compressors + heat rejection
    frozenset({"crah", "valve"}),               # block 3 — air handlers + control valves
)
# NOTE: CDUs are deliberately absent. An in-rack coolant distribution unit is
# dual-corded off the rack PDUs, so it is UPS-backed and never sees the transfer
# gap — unlike the bulk plant on the MCC.

# Retransfer + cooldown. Real ATS ships with TDEN at 30 min; compressed here so a
# simulated outage is observable end-to-end inside a session.
TDEN_S      = 300.0      # time delay emergency→normal, after the utility returns
COOLDOWN_S  = 300.0      # unloaded genset run after retransfer, before shutdown

# Genset states exposed to the generator telemetry step.
GEN_STANDBY  = "standby"
GEN_CRANKING = "cranking"
GEN_RUNNING  = "running"
GEN_COOLDOWN = "cooldown"


@dataclass
class ATSStatus:
    """What the transfer switch is doing right now, per datacenter."""
    state: str = "utility"          # see TransferController.step
    source: str = "normal"          # "normal" | "emergency" | "none" (mid-transfer)
    source_live: bool = True        # is the ATS output bus energized at all
    gen_demand: bool = False        # start contact closed
    gen_at_voltage: bool = False    # gensets qualified, ready to accept load
    gen_status: str = GEN_STANDBY
    ups_input_ok: bool = True       # UPS rectifier sees a qualified source
    mech_blocks_on: int = 3         # how many of the 3 mech load blocks are energized
    t: float = 0.0                  # seconds elapsed in the current state
    since_transfer: float = -1.0    # seconds since the ATS closed onto emergency

    def mech_types_on(self) -> Set[str]:
        """Plant device types currently energized. Empty while the bus is dead."""
        if not self.source_live:
            return set()
        out: Set[str] = set()
        for i in range(self.mech_blocks_on):
            out |= MECH_BLOCKS[i]
        return out


@dataclass
class TransferController:
    """Per-DC utility/generator transfer sequencer. Drive with step() once a tick."""
    _dcs: Dict[str, ATSStatus] = field(default_factory=dict)

    def status(self, dc: str) -> ATSStatus:
        return self._dcs.setdefault(dc, ATSStatus())

    def all_status(self) -> Dict[str, ATSStatus]:
        return dict(self._dcs)

    def step(self, dc: str, utility_ok: bool, gens_startable: bool,
             dt: float) -> ATSStatus:
        """Advance datacenter *dc* by *dt* seconds.

        utility_ok      — the utility feed is present and within tolerance.
        gens_startable  — at least one genset has fuel and is not faulted. If no
                          genset can start, the emergency source never qualifies
                          and the site rides the UPS batteries down to nothing,
                          which is exactly what a failed start does in the field.
        """
        st = self.status(dc)
        st.t += dt
        if st.since_transfer >= 0.0:
            st.since_transfer += dt

        # A state change must publish the NEW state's outputs on the same tick.
        # Otherwise a caller reading st right after the transition sees, say,
        # state="emergency" while source is still "none" — the ATS reporting a
        # dead bus one tick after it closed. Re-dispatch until the state settles;
        # a fresh state has t=0, so it cannot immediately transition again except
        # through a zero-length state, hence the small bound.
        for _ in range(3):
            if not self._dispatch(st, utility_ok, gens_startable):
                break
        return st

    def _dispatch(self, st: ATSStatus, utility_ok: bool, gens_startable: bool) -> bool:
        """Run the branch for the current state. True if it changed state."""
        prev = st.state

        if st.state == "utility":
            # Normal source live. Nothing to do until the utility drops.
            if not utility_ok:
                self._enter(st, "sense")
            else:
                self._on_normal(st)

        elif st.state == "sense":
            # Riding the TDNE timer. A utility that comes back inside the delay
            # never cranks the engines — the whole point of the delay.
            self._dead_bus(st)
            if utility_ok:
                self._enter(st, "utility")
            elif st.t >= TDNE_S:
                self._enter(st, "crank")

        elif st.state == "crank":
            self._dead_bus(st)
            st.gen_demand = True
            st.gen_status = GEN_CRANKING
            if not gens_startable:
                # Failed start (no fuel / genset faulted). The bus stays dead and
                # the UPS keeps discharging. Real controllers re-crank on a cycle
                # rather than latching, so restart the timer: a genset that gets
                # fuel back still has to spin up before it can accept load.
                st.gen_status = GEN_STANDBY
                st.t = 0.0
                if utility_ok:
                    self._enter(st, "utility")
            elif st.t >= GEN_START_S:
                st.gen_at_voltage = True
                self._enter(st, "transfer")

        elif st.state == "transfer":
            # Open transition: contacts break normal before they make emergency,
            # so the output bus is momentarily dead even though the gensets are up.
            self._dead_bus(st)
            st.gen_demand = True
            st.gen_at_voltage = True
            st.gen_status = GEN_RUNNING
            if st.t >= ATS_XFER_S:
                st.since_transfer = 0.0
                self._enter(st, "emergency")

        elif st.state == "emergency":
            st.source = "emergency"
            st.source_live = True
            st.gen_demand = True
            st.gen_at_voltage = True
            st.gen_status = GEN_RUNNING
            # UPS rectifier needs a few seconds of stable source before it stops
            # discharging and returns to double-conversion.
            st.ups_input_ok = st.since_transfer >= UPS_ACCEPT_S
            st.mech_blocks_on = sum(1 for b in MECH_BLOCK_S if st.since_transfer >= b)
            if utility_ok:
                self._enter(st, "retransfer", keep_transfer=True)

        elif st.state == "retransfer":
            # Utility is back but we stay on the genset until TDEN expires, so a
            # flapping feed doesn't transfer the load twice in a minute.
            st.source = "emergency"
            st.source_live = True
            st.gen_demand = True
            st.gen_at_voltage = True
            st.gen_status = GEN_RUNNING
            st.ups_input_ok = True
            st.mech_blocks_on = 3
            if not utility_ok:
                self._enter(st, "emergency", keep_transfer=True)
            elif st.t >= TDEN_S:
                self._enter(st, "retransfer_xfer")

        elif st.state == "retransfer_xfer":
            self._dead_bus(st)
            st.gen_demand = True
            st.gen_at_voltage = True
            st.gen_status = GEN_RUNNING
            if st.t >= ATS_XFER_S:
                self._enter(st, "cooldown")

        elif st.state == "cooldown":
            # Back on utility; gensets run unloaded to bring block temperatures
            # down evenly before shutdown. They carry no load here.
            self._on_normal(st)
            st.gen_demand = True
            st.gen_status = GEN_COOLDOWN
            if not utility_ok:
                # Utility dropped again mid-cooldown. Engines are already turning,
                # so the only wait is the transfer itself — no crank delay.
                self._enter(st, "transfer")
            elif st.t >= COOLDOWN_S:
                self._enter(st, "utility")

        return st.state != prev

    # ── helpers ──────────────────────────────────────────────────────────────
    def _enter(self, st: ATSStatus, state: str, keep_transfer: bool = False) -> None:
        st.state = state
        st.t = 0.0
        if not keep_transfer and state in ("utility", "cooldown"):
            st.since_transfer = -1.0

    @staticmethod
    def _on_normal(st: ATSStatus) -> None:
        st.source = "normal"
        st.source_live = True
        st.gen_demand = False
        st.gen_at_voltage = False
        st.gen_status = GEN_STANDBY
        st.ups_input_ok = True
        st.mech_blocks_on = 3

    @staticmethod
    def _dead_bus(st: ATSStatus) -> None:
        """Output bus de-energized: mech is dark, UPS is on battery, IT rides."""
        st.source = "none"
        st.source_live = False
        st.ups_input_ok = False
        st.mech_blocks_on = 0
