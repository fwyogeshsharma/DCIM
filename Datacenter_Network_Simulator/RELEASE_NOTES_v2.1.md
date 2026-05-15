# Datacenter Network Simulator — Release Notes v2.1

**Release Date:** May 11, 2026
**Version:** 2.1.0

---

## Overview

Version 2.1 is a major feature release focused on infrastructure fidelity and event-driven observability. The simulator now models a datacenter at three distinct network layers — production, management, and power — with a fully configurable rule engine that drives realistic SNMP trap generation across all device types.

---

## What's New

### 1. Rule-Based SNMP Trap Engine

A new rule engine drives all SNMP trap generation. Rules are evaluated every simulation tick against a `DeviceFact` snapshot of each device's live metrics, and matching rules fire SNMPv2c traps to a configurable receiver.

**Five rule types are supported:**

| Rule Type | Description |
|-----------|-------------|
| **Threshold** | Fires when a metric crosses a value, optionally sustained for a duration (e.g., CPU > 90% for 5 minutes) |
| **State Change** | Fires on metric transitions, e.g., interface status `up → down` |
| **Temporal** | Fires when an event occurs N times within a sliding time window (e.g., link flap 3× in 60 s) |
| **Composite** | Fires when a logical AND or OR of multiple sub-conditions is met |
| **Rack Failure** | Fires when a configurable number of devices within the same rack become impaired simultaneously |

Recovery rules are also supported — a rule can declare itself the recovery counterpart of another rule and fire when the alert condition clears.

**14 trap types are shipped out of the box**, covering interface state, CPU, memory, temperature, BGP sessions, UPS battery status, environmental sensors (humidity, dewpoint, airflow), link flap, and rack failure. Each trap type carries semantically correct SNMPv2c varbinds (interface index, gauge values, battery state OIDs, peer IP, etc.).

Rules are fully configurable via `trap_rules.json` or the in-app Rules Panel:

- Per-rule enable/disable toggle
- Priority ordering
- Device type filtering (e.g., apply a rule only to UPS devices)
- Custom OIDs for integration with third-party NMS platforms

---

### 2. Separated Production and Management Infrastructure

The simulator now models out-of-band (OOB) management as a dedicated, isolated network layer.

**Production network** (`10.0.0.0/8`) carries simulated data-plane traffic and is used for topology visualization, ICMP reachability, and interface state tracking.

**Management network** (`192.168.0.0/16`) carries SNMP, gNMI, and telemetry traffic over a separate OOB path. Every device can carry a distinct `mgmt_ip` in addition to its production address. SNMP polling, gNMI subscriptions, and trap delivery all operate over this address.

**New management device types:**

| Device Type | Example Models | Role |
|-------------|---------------|------|
| **OOB Switch** | Cisco Catalyst 1000, HPE Aruba 2530, Dell N1148T | Aggregates all OOB management connections |
| **UPS** | APC, Eaton, Vertiv, Raritan (1.5 kVA–100 kVA) | Power monitoring and battery event generation |
| **Rack PDU** | APC, Eaton, Raritan, Server Technology | Per-outlet metered power distribution |
| **Floor PDU / RPP** | APC Galaxy, Eaton, Vertiv (80–160 kVA) | 3-phase input, branch-circuit output |
| **Environmental Sensor** | Generic temp/humidity/dewpoint/airflow | Ambient datacenter conditions |

Each management device participates in the rule engine and can generate traps appropriate to its type. For example, UPS devices generate `UPS_ON_BATTERY` and `UPS_LOW_BATTERY` traps using standard RFC 1628 MIB OIDs; environmental sensors generate humidity, dewpoint, and airflow alerts.

---

### 3. Initial Power Infrastructure Support

A dedicated **power layer** models the physical power distribution hierarchy within the datacenter. Power topology edges are drawn from downstream devices to their upstream power source, enabling cross-correlation between power events and network impact.

The supported power hierarchy is:

```
Mains / Utility Feed
   └── Floor PDU / RPP  (3-phase distribution, 80–160 kVA)
         └── Rack PDU   (metered, switched outlets)
               └── UPS  (battery backup, per-rack)
                     └── Network devices / Servers
```

Power infrastructure participates in the **Rack Failure** trap rule: when a PDU or UPS becomes unreachable, the rule engine can detect the correlated loss of downstream devices in the same rack and fire a single aggregated rack failure trap, rather than flooding the receiver with per-device alerts.

---

### 4. Physical Location Polling via SNMP

Devices now carry a `rack_id` metadata field that maps them to a physical rack location. This is surfaced in two ways:

**SNMP discovery correlation:** The discovery engine walks LLDP-MIB (and Cisco CDP) neighbor tables on both the production and management layers. When a discovered neighbor's management IP matches a device with a known rack assignment, the correlation is surfaced in the discovery results view, enabling NMS tools to infer physical location from SNMP adjacency data alone.

**Rack failure rule:** The `rack_id` field is the grouping key used by the Rack Failure rule type. When ≥ N devices sharing a rack ID become impaired in the same evaluation window, a single `RACK_FAILURE` trap is dispatched, reflecting physical co-location impact.

**Topology-aware LLDP generation:** Each device's `.snmprec` dataset includes populated LLDP neighbor tables derived from the configured topology. This means an SNMP walk against any device returns accurate, topology-consistent LLDP adjacency data that NMS tools can use to reconstruct physical layout.

---

### 5. Three-Layer Topology Visualization

The topology canvas now renders all three network layers simultaneously with distinct visual styles:

| Layer | Color | Line Style | Represents |
|-------|-------|------------|------------|
| **Production** | Gray | Solid, 2 px | Data-plane fabric links |
| **Management** | Cyan | Dashed, 1.5 px | OOB telemetry and SNMP paths |
| **Power** | Orange | Dotted, 1.5 px | Power distribution dependencies |

Each layer can be independently shown or hidden from the view controls. New device types introduced in this release have distinct visual identities:

| Device | Color |
|--------|-------|
| UPS | Gold |
| Rack PDU | Dark red |
| Floor PDU | Magenta |
| OOB Switch | Indigo |
| Environmental Sensor | Cyan |

Topology metadata flags (`has_management_layer`, `has_power_layer`) control which layers are rendered, ensuring backward compatibility with topologies created in earlier versions.

---

## Included Topology Templates

Six pre-built topology files are shipped with this release, all updated to include management and power layers:

| Template | Description |
|----------|-------------|
| `large_datacenter_3tier.json` | Classic core/distribution/access hierarchy |
| `large_datacenter_spine_leaf.json` | Modern Clos fabric with spine and leaf tiers |
| `large_4dc_enterprise.json` | Four-datacenter enterprise with WAN interconnects |
| `large_enterprise_wan.json` | WAN-centric topology with edge routers |
| `large_hyperscale_pod.json` | Hyperscale pod architecture |
| `dual_dc_enterprise.json` | Active/active dual-datacenter setup |

---

## Configuration Reference

### `trap_rules.json`

User-defined rules are loaded from `trap_rules.json` at startup and merged with built-in defaults. The file is a JSON array of rule objects:

```json
{
  "rule_name": "HighCPUSustained",
  "trap_oid": "1.3.6.1.4.1.99999.1.1",
  "severity": "critical",
  "enabled": true,
  "priority": 100,
  "device_types": ["Router", "Switch"],
  "condition": {
    "condition_type": "threshold",
    "metric": "cpu_usage",
    "operator": ">",
    "threshold": 90.0,
    "duration_sec": 300.0
  }
}
```

### Topology JSON — New Fields

| Field | Location | Description |
|-------|----------|-------------|
| `mgmt_ip` | Device node | Management-plane IP for SNMP/gNMI polling |
| `rack_id` | Device node | Physical rack identifier (e.g., `"R1"`) |
| `layer` | Edge | `"production"`, `"management"`, or `"power"` |
| `metadata.has_management_layer` | Top-level | Enables management layer rendering |
| `metadata.has_power_layer` | Top-level | Enables power layer rendering |

---

## Known Limitations

- SNMP simulation is SNMPv2c only; SNMPv3 auth/privacy is not yet supported.
- Power layer is semantic — no actual load calculations or capacity modeling.
- The rack failure rule threshold is currently fixed at ≥ 3 devices; per-rule threshold configuration is planned for v2.2.
- IPv6 is not supported.
- gNMI streaming uses periodic polling; true event-driven path subscriptions are not yet implemented.

---

## Upgrade Notes

Topologies saved with v2.0 remain fully compatible. Devices without a `mgmt_ip` field will not participate in the management layer; devices without a `rack_id` field will not trigger the rack failure rule. No migration is required to open existing topology files.

Custom `trap_rules.json` files from v2.0 are compatible without modification.

---

*Datacenter Network Simulator is an internal simulation platform for testing NMS integrations, SNMP tooling, and datacenter topology modeling.*