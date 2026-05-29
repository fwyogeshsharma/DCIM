"""
Verdigris EV2 BACnet/IP Device.

One EV2BACnetDevice instance per simulated device IP.
Handles BACnet/IP service requests:
  Who-Is  → I-Am
  ReadProperty
  ReadPropertyMultiple
  SubscribeCOV  → SimpleAck + async COV notifications

No external libraries required — pure Python UDP socket + raw BACnet encoding.
"""
from __future__ import annotations

import collections
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from core.bacnet_object_model import (
    BACnetObject,
    OBJ_ANALOG_INPUT, OBJ_BINARY_INPUT, OBJ_DEVICE,
    PROP_OBJECT_IDENTIFIER, PROP_OBJECT_NAME, PROP_OBJECT_TYPE,
    PROP_PRESENT_VALUE, PROP_DESCRIPTION, PROP_UNITS,
    PROP_STATUS_FLAGS, PROP_RELIABILITY, PROP_OUT_OF_SERVICE,
    PROP_COV_INCREMENT, PROP_MIN_PRES_VALUE, PROP_MAX_PRES_VALUE,
    PROP_RESOLUTION, PROP_ALL,
    SVC_WHO_IS, SVC_READ_PROPERTY, SVC_READ_PROPERTY_MULTIPLE,
    SVC_SUBSCRIBE_COV, SVC_I_AM,
    SVC_UNCONFIRMED_COV_NOTIFICATION,
    PDU_SIMPLE_ACK, PDU_COMPLEX_ACK, PDU_ERROR, PDU_UNCONFIRMED_REQUEST,
    ERR_CLASS_OBJECT, ERR_CLASS_PROPERTY,
    ERR_CODE_UNKNOWN_OBJECT, ERR_CODE_UNKNOWN_PROPERTY,
    VERDIGRIS_VENDOR_ID, VERDIGRIS_VENDOR_NAME, VERDIGRIS_MODEL_NAME,
    VERDIGRIS_FIRMWARE_REVISION, VERDIGRIS_APP_SW_VERSION,
    BACNET_PORT, MAX_APDU_LENGTH,
    SEGMENTATION_NO_SEGMENTATION, DEVICE_STATUS_OPERATIONAL,
    RELIABILITY_NO_FAULT_DETECTED,
    PROP_VENDOR_NAME, PROP_VENDOR_IDENTIFIER, PROP_MODEL_NAME,
    PROP_FIRMWARE_REVISION, PROP_APPLICATION_SOFTWARE_VERSION,
    PROP_PROTOCOL_VERSION, PROP_PROTOCOL_REVISION,
    PROP_MAX_APDU_LENGTH_ACCEPTED, PROP_SEGMENTATION_SUPPORTED,
    PROP_SYSTEM_STATUS, PROP_OBJECT_LIST,
    # Encoding
    enc_app_oid, enc_app_charstr, enc_app_uint, enc_app_enum,
    enc_app_bool, enc_app_real, enc_app_bitstr,
    enc_ctx_oid, enc_ctx_uint, enc_ctx_open, enc_ctx_close,
    build_bvll, build_npdu, build_iam, build_iam_unicast,
    build_simple_ack, build_error, build_reject,
    encode_property_value, encode_device_property_value,
    # Decoding
    decode_whois, decode_read_property,
    decode_read_property_multiple, decode_subscribe_cov,
)
from core.bacnet_ev2_generator import (
    build_ev2_object_tree, get_panel_instance_map,
    DEFAULT_CIRCUITS,
)

log = logging.getLogger(__name__)

# BACnet/IP max APDU length accepted (1476 bytes, Ethernet-safe)
_MAX_APDU = MAX_APDU_LENGTH


# ─────────────────────────────────────────────────────────────────
#  COV subscription tracking
# ─────────────────────────────────────────────────────────────────

@dataclass
class COVSubscription:
    """Active COV subscription from a remote subscriber."""
    subscriber_addr: Tuple[str, int]   # (ip, port)
    process_id:      int
    obj_type:        int
    obj_instance:    int
    confirmed:       bool
    lifetime:        int               # seconds; 0 = indefinite
    expiry:          float             # monotonic time; 0.0 = never expires


# ─────────────────────────────────────────────────────────────────
#  Required properties for all non-device objects (ordered list)
# ─────────────────────────────────────────────────────────────────

_STANDARD_AI_PROPS = [
    PROP_OBJECT_IDENTIFIER, PROP_OBJECT_NAME, PROP_OBJECT_TYPE,
    PROP_PRESENT_VALUE, PROP_DESCRIPTION, PROP_UNITS,
    PROP_STATUS_FLAGS, PROP_RELIABILITY, PROP_OUT_OF_SERVICE,
    PROP_COV_INCREMENT,
]
_OPTIONAL_AI_PROPS = [PROP_MIN_PRES_VALUE, PROP_MAX_PRES_VALUE, PROP_RESOLUTION]

_STANDARD_BI_PROPS = [
    PROP_OBJECT_IDENTIFIER, PROP_OBJECT_NAME, PROP_OBJECT_TYPE,
    PROP_PRESENT_VALUE, PROP_DESCRIPTION,
    PROP_STATUS_FLAGS, PROP_RELIABILITY, PROP_OUT_OF_SERVICE,
]

_DEVICE_PROPS = [
    PROP_OBJECT_IDENTIFIER, PROP_OBJECT_NAME, PROP_OBJECT_TYPE,
    PROP_DESCRIPTION, PROP_VENDOR_NAME, PROP_VENDOR_IDENTIFIER,
    PROP_MODEL_NAME, PROP_FIRMWARE_REVISION,
    PROP_APPLICATION_SOFTWARE_VERSION,
    PROP_PROTOCOL_VERSION, PROP_PROTOCOL_REVISION,
    PROP_MAX_APDU_LENGTH_ACCEPTED, PROP_SEGMENTATION_SUPPORTED,
    PROP_SYSTEM_STATUS, PROP_OBJECT_LIST,
]


class EV2BACnetDevice:
    """
    Single simulated Verdigris EV2 BACnet/IP device.

    Each instance:
      • Owns a UDP socket bound to device_ip:47808 (send) or uses
        the shared socket (receive) — see BACnetController.
      • Maintains a BACnet object dictionary.
      • Has a name→instance lookup for telemetry updates.
      • Tracks active COV subscriptions.
    """

    def __init__(
        self,
        device_ip:       str,
        device_instance: int,
        device_name:     str,
        circuits:        int = DEFAULT_CIRCUITS,
        log_cb:          Optional[Callable[[str, str], None]] = None,
        port:            int = BACNET_PORT,
    ):
        self.device_ip       = device_ip
        self.device_instance = device_instance
        self.device_name     = device_name
        self.circuits        = circuits
        self._log_cb         = log_cb
        self._port           = port

        # Build full object tree
        self._objects: Dict[Tuple[int, int], BACnetObject] = \
            build_ev2_object_tree(device_instance, device_name, circuits)

        # Reverse name lookup for telemetry updates
        _panel_map = get_panel_instance_map()   # name → instance (panel)
        self._name_to_key: Dict[str, Tuple[int, int]] = {}
        for name, inst in _panel_map.items():
            obj = self._objects.get((OBJ_ANALOG_INPUT, inst)) or \
                  self._objects.get((OBJ_BINARY_INPUT,  inst))
            if obj:
                self._name_to_key[name] = (obj.object_type, inst)
        # Circuit names
        for ckt in range(1, circuits + 1):
            label = f"Ckt{ckt:02d}"
            base  = (ckt + 1) * 1000
            for offset, suffix in [(1,'Current'),(2,'kW'),(3,'kWh'),(4,'PF'),(5,'THD')]:
                k = (OBJ_ANALOG_INPUT, base + offset)
                self._name_to_key[f"{label}_{suffix}"] = k

        # COV subscriptions: (obj_type, instance) → list[COVSubscription]
        self._cov_subs: Dict[Tuple[int, int], List[COVSubscription]] = {}

        # COV event log — last 100 dispatched notifications
        self._cov_events: collections.deque = collections.deque(maxlen=100)

        # Send socket (bound to device IP so responses have correct source IP)
        self._send_sock: Optional[socket.socket] = None
        self._init_send_socket()

        self._log(f"[BACnet] Device {device_name} ({device_ip}:{port}) "
                  f"instance={device_instance} circuits={circuits}", "info")

    # ─────────────────────────────────────────────────────────────
    #  Socket setup
    # ─────────────────────────────────────────────────────────────

    def _init_send_socket(self):
        """
        Create a UDP socket bound to device_ip:BACNET_PORT.

        Binding to the BACnet port (not an ephemeral port) enables per-device
        routing in the controller:

          On Windows (and Linux), when both a wildcard socket (0.0.0.0:47808)
          and a specific-IP socket (device_ip:47808) exist with SO_REUSEADDR,
          the OS delivers unicast packets to the MOST SPECIFIC binding.  A
          ReadProperty sent to device_ip:47808 therefore arrives on THIS socket,
          not on the shared recv socket — giving the controller an unambiguous
          device identity without needing IP_PKTINFO / recvmsg.

          Broadcast Who-Is packets (to 255.255.255.255) still arrive on the
          wildcard recv socket; the controller fans them out to all devices.

          The controller's recv thread selects over all device sockets and
          routes each confirmed request directly to the owning device, fixing
          the bug where all devices share identical AI/BI instance numbers and
          the old object-tree search always returned the first device.
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((self.device_ip, self._port))   # own port for clean routing
            self._send_sock = sock
        except OSError as exc:
            self._log(f"[BACnet] {self.device_name}: socket error: {exc}", "warning")
            self._send_sock = None

    def close(self):
        if self._send_sock:
            try:
                self._send_sock.close()
            except Exception:
                pass
            self._send_sock = None

    # ─────────────────────────────────────────────────────────────
    #  Telemetry update
    # ─────────────────────────────────────────────────────────────

    def update_present_values(self, values: Dict[str, float]):
        """
        Apply a tick's telemetry values to BACnet object present_values.

        Called by BACnetController.tick() from the DeviceStateStore thread.
        """
        for name, val in values.items():
            key = self._name_to_key.get(name)
            if key is None:
                continue
            obj = self._objects.get(key)
            if obj is None:
                continue
            obj.present_value = float(val)
            # Update reliability based on alarm sensor fault
            if name == "Alarm_SensorFault" and val > 0.5:
                for o in self._objects.values():
                    if o.reliability == 0:
                        o.reliability = 12   # COMM_FAILURE on fault
            elif name == "Alarm_SensorFault" and val < 0.5:
                for o in self._objects.values():
                    if o.reliability != 0:
                        o.reliability = 0

    def dispatch_cov_notifications(self, send_sock_fallback=None):
        """
        Check all subscribed objects for COV threshold crossings.
        Send UnconfirmedCOVNotification to each subscriber.

        Called from the controller after every tick.
        """
        now = time.monotonic()
        for key, obj in self._objects.items():
            if not obj.should_send_cov():
                continue
            subs = self._cov_subs.get(key, [])
            if not subs:
                continue
            obj.mark_cov_sent()
            pkt = self._build_cov_notification(obj)
            # Purge expired subscriptions
            active = []
            for sub in subs:
                if sub.expiry > 0 and now > sub.expiry:
                    continue
                try:
                    sock = self._send_sock or send_sock_fallback
                    if sock:
                        sock.sendto(pkt, sub.subscriber_addr)
                except Exception:
                    pass
                active.append(sub)
            self._cov_subs[key] = active
            if active:
                self._cov_events.append({
                    "ts":       time.strftime("%H:%M:%S"),
                    "instance": self.device_instance,
                    "obj_type": obj.object_type,
                    "obj_inst": obj.instance,
                    "obj_name": obj.object_name,
                    "value":    obj.present_value,
                    "sent_to":  [s.subscriber_addr for s in active],
                })

    def get_cov_events(self) -> List[dict]:
        return list(self._cov_events)

    def get_all_subscribers(self) -> List[dict]:
        now = time.monotonic()
        result = []
        for (ot, oi), subs in list(self._cov_subs.items()):
            obj = self._objects.get((ot, oi))
            obj_name = obj.object_name if obj else f"{ot}:{oi}"
            for sub in subs:
                if sub.expiry > 0 and now > sub.expiry:
                    continue
                remaining = max(0, int(sub.expiry - now)) if sub.expiry > 0 else 0
                result.append({
                    "subscriber_ip":   sub.subscriber_addr[0],
                    "subscriber_port": sub.subscriber_addr[1],
                    "process_id":      sub.process_id,
                    "obj_name":        obj_name,
                    "lifetime":        sub.lifetime,
                    "remaining":       remaining,
                    "device_instance": self.device_instance,
                })
        return result

    # ─────────────────────────────────────────────────────────────
    #  Incoming packet dispatch
    # ─────────────────────────────────────────────────────────────

    def handle_whois(self, low: Optional[int], high: Optional[int],
                     src_addr: Tuple[str, int]):
        """
        Respond to a Who-Is request with I-Am if our instance is in range.
        """
        if low is not None and high is not None:
            if not (low <= self.device_instance <= high):
                return
        pkt = build_iam(self.device_instance)
        self._send(pkt, src_addr)

    def handle_confirmed(self, invoke_id: int, service: int,
                         data: bytes, src_addr: Tuple[str, int]):
        """Route confirmed-request services."""
        if service == SVC_READ_PROPERTY:
            self._handle_read_property(invoke_id, data, src_addr)
        elif service == SVC_READ_PROPERTY_MULTIPLE:
            self._handle_read_property_multiple(invoke_id, data, src_addr)
        elif service == SVC_SUBSCRIBE_COV:
            self._handle_subscribe_cov(invoke_id, data, src_addr)
        else:
            # Unknown service → Reject
            self._send(build_reject(invoke_id), src_addr)

    # ─────────────────────────────────────────────────────────────
    #  ReadProperty
    # ─────────────────────────────────────────────────────────────

    def _handle_read_property(self, invoke_id: int, data: bytes,
                               src_addr: Tuple[str, int]):
        parsed = decode_read_property(data)
        if parsed is None:
            self._send(build_reject(invoke_id), src_addr)
            return

        obj_type, obj_inst, prop_id, array_index = parsed

        # Device object request
        if obj_type == OBJ_DEVICE:
            if obj_inst != self.device_instance:
                self._send(build_error(invoke_id, SVC_READ_PROPERTY,
                                       ERR_CLASS_OBJECT, ERR_CODE_UNKNOWN_OBJECT),
                           src_addr)
                return
            value_bytes = self._encode_device_prop(prop_id, array_index=array_index)
            if value_bytes is None:
                self._send(build_error(invoke_id, SVC_READ_PROPERTY,
                                       ERR_CLASS_PROPERTY, ERR_CODE_UNKNOWN_PROPERTY),
                           src_addr)
                return
            pkt = self._build_read_property_ack(
                invoke_id, obj_type, obj_inst, prop_id, value_bytes,
                array_index=array_index)
            self._send(pkt, src_addr)
            return

        # Regular object request
        obj = self._objects.get((obj_type, obj_inst))
        if obj is None:
            self._send(build_error(invoke_id, SVC_READ_PROPERTY,
                                   ERR_CLASS_OBJECT, ERR_CODE_UNKNOWN_OBJECT),
                       src_addr)
            return

        if prop_id == PROP_ALL:
            # Return all properties as a sequence
            props = (_STANDARD_BI_PROPS if obj.is_binary else _STANDARD_AI_PROPS)
            if not obj.is_binary:
                props = list(props)
                for p in _OPTIONAL_AI_PROPS:
                    val = encode_property_value(obj, p)
                    if val is not None:
                        props.append(p)
            value_bytes = b''.join(
                encode_property_value(obj, p) or b''
                for p in props
            )
        else:
            value_bytes = encode_property_value(obj, prop_id)
            if value_bytes is None:
                self._send(build_error(invoke_id, SVC_READ_PROPERTY,
                                       ERR_CLASS_PROPERTY, ERR_CODE_UNKNOWN_PROPERTY),
                           src_addr)
                return

        pkt = self._build_read_property_ack(
            invoke_id, obj_type, obj_inst, prop_id, value_bytes)
        self._send(pkt, src_addr)

    def _build_read_property_ack(
        self,
        invoke_id: int,
        obj_type:  int,
        obj_inst:  int,
        prop_id:   int,
        value_bytes: bytes,
        array_index: Optional[int] = None,
    ) -> bytes:
        """
        Build a ComplexAck for ReadProperty.

        Wire format (BACnet clause 15.7.2):
          PDU type=3 (ComplexAck), invoke_id, service=12
          context[0]: object-identifier  (4 bytes)
          context[1]: property-identifier (unsigned)
          context[2]: array-index (unsigned) — ONLY when array_index was requested
          context[3] opening
            <application-tagged value>
          context[3] closing
        """
        apdu = bytes([(PDU_COMPLEX_ACK << 4), invoke_id, SVC_READ_PROPERTY])
        apdu += enc_ctx_oid(0, obj_type, obj_inst)
        apdu += enc_ctx_uint(1, prop_id)
        if array_index is not None:
            apdu += enc_ctx_uint(2, array_index)   # echo array index per spec
        apdu += enc_ctx_open(3)
        apdu += value_bytes
        apdu += enc_ctx_close(3)
        return build_bvll(build_npdu(apdu))

    # ─────────────────────────────────────────────────────────────
    #  ReadPropertyMultiple
    # ─────────────────────────────────────────────────────────────

    def _handle_read_property_multiple(self, invoke_id: int, data: bytes,
                                        src_addr: Tuple[str, int]):
        items = decode_read_property_multiple(data)
        if not items:
            self._send(build_reject(invoke_id), src_addr)
            return

        apdu = bytes([(PDU_COMPLEX_ACK << 4), invoke_id, SVC_READ_PROPERTY_MULTIPLE])

        for item in items:
            obj_type = item['obj_type']
            obj_inst = item['obj_inst']

            # context[0]: object-identifier
            apdu += enc_ctx_oid(0, obj_type, obj_inst)
            # context[1] opening — list of results
            apdu += enc_ctx_open(1)

            if obj_type == OBJ_DEVICE and obj_inst == self.device_instance:
                # Device object
                for prop_id, _array_idx in item['props']:
                    apdu += self._encode_rpm_result(
                        prop_id,
                        self._encode_device_prop(prop_id),
                    )
            elif obj_type == OBJ_DEVICE:
                # Wrong device instance — encode error for all props
                for prop_id, _array_idx in item['props']:
                    apdu += self._encode_rpm_error(
                        prop_id, ERR_CLASS_OBJECT, ERR_CODE_UNKNOWN_OBJECT)
            else:
                obj = self._objects.get((obj_type, obj_inst))
                if obj is None:
                    for prop_id, _array_idx in item['props']:
                        apdu += self._encode_rpm_error(
                            prop_id, ERR_CLASS_OBJECT, ERR_CODE_UNKNOWN_OBJECT)
                else:
                    for prop_id, _array_idx in item['props']:
                        if prop_id == PROP_ALL:
                            props = (_STANDARD_BI_PROPS if obj.is_binary
                                     else _STANDARD_AI_PROPS)
                            value_bytes = b''.join(
                                encode_property_value(obj, p) or b''
                                for p in props
                            )
                            apdu += self._encode_rpm_result(prop_id, value_bytes)
                        else:
                            val_bytes = encode_property_value(obj, prop_id)
                            if val_bytes is None:
                                apdu += self._encode_rpm_error(
                                    prop_id, ERR_CLASS_PROPERTY,
                                    ERR_CODE_UNKNOWN_PROPERTY)
                            else:
                                apdu += self._encode_rpm_result(prop_id, val_bytes)

            apdu += enc_ctx_close(1)

        self._send(build_bvll(build_npdu(apdu)), src_addr)

    @staticmethod
    def _encode_rpm_result(prop_id: int, value_bytes: Optional[bytes]) -> bytes:
        """
        Encode one property result entry in ReadPropertyMultiple ACK.

        context[2] opening
          context[0]: property-identifier
          context[4] opening (property-value)
            <value>
          context[4] closing
        context[2] closing
        """
        chunk = enc_ctx_open(2)
        chunk += enc_ctx_uint(0, prop_id)
        chunk += enc_ctx_open(4)
        chunk += value_bytes or b''
        chunk += enc_ctx_close(4)
        chunk += enc_ctx_close(2)
        return chunk

    @staticmethod
    def _encode_rpm_error(prop_id: int,
                           err_class: int, err_code: int) -> bytes:
        """
        Encode one property error entry in ReadPropertyMultiple ACK.

        context[2] opening
          context[0]: property-identifier
          context[5] opening (property-access-error)
            error-class (enumerated)
            error-code  (enumerated)
          context[5] closing
        context[2] closing
        """
        chunk = enc_ctx_open(2)
        chunk += enc_ctx_uint(0, prop_id)
        chunk += enc_ctx_open(5)
        chunk += enc_app_enum(err_class)
        chunk += enc_app_enum(err_code)
        chunk += enc_ctx_close(5)
        chunk += enc_ctx_close(2)
        return chunk

    # ─────────────────────────────────────────────────────────────
    #  SubscribeCOV
    # ─────────────────────────────────────────────────────────────

    def _handle_subscribe_cov(self, invoke_id: int, data: bytes,
                               src_addr: Tuple[str, int]):
        parsed = decode_subscribe_cov(data)
        if parsed is None:
            self._send(build_reject(invoke_id), src_addr)
            return

        key = (parsed['obj_type'], parsed['obj_inst'])
        obj = self._objects.get(key)
        if obj is None and not (
            parsed['obj_type'] == OBJ_DEVICE and
            parsed['obj_inst'] == self.device_instance
        ):
            self._send(build_error(invoke_id, SVC_SUBSCRIBE_COV,
                                   ERR_CLASS_OBJECT, ERR_CODE_UNKNOWN_OBJECT),
                       src_addr)
            return

        # Lifetime=0 with no confirmed flag = cancellation
        if parsed['lifetime'] == 0 and not parsed['confirmed']:
            subs = self._cov_subs.get(key, [])
            self._cov_subs[key] = [
                s for s in subs
                if not (s.subscriber_addr == src_addr and
                        s.process_id == parsed['process_id'])
            ]
        else:
            lifetime = parsed['lifetime']
            expiry = (time.monotonic() + lifetime) if lifetime else 0.0
            sub = COVSubscription(
                subscriber_addr=src_addr,
                process_id=parsed['process_id'],
                obj_type=parsed['obj_type'],
                obj_instance=parsed['obj_inst'],
                confirmed=parsed['confirmed'],
                lifetime=lifetime,
                expiry=expiry,
            )
            subs = self._cov_subs.setdefault(key, [])
            # Replace existing subscription for same subscriber+process_id
            subs[:] = [s for s in subs
                       if not (s.subscriber_addr == src_addr and
                               s.process_id == parsed['process_id'])]
            subs.append(sub)
            if obj:
                obj.mark_cov_sent()   # snapshot current value

        self._send(build_simple_ack(invoke_id, SVC_SUBSCRIBE_COV), src_addr)

    # ─────────────────────────────────────────────────────────────
    #  COV Notification builder
    # ─────────────────────────────────────────────────────────────

    def _build_cov_notification(self, obj: BACnetObject) -> bytes:
        """
        Build an UnconfirmedCOVNotification packet for *obj*.

        Wire format:
          UnconfirmedRequest, service=2
          context[0]: subscriber-process-id (use 0 if multiple — caller filters)
          context[1]: initiating-device-id
          context[2]: monitored-object-id
          context[3]: time-remaining (0 = indefinite)
          context[4] opening
            property-value for PRESENT_VALUE
            property-value for STATUS_FLAGS
          context[4] closing
        """
        apdu = bytes([(PDU_UNCONFIRMED_REQUEST << 4), SVC_UNCONFIRMED_COV_NOTIFICATION])
        apdu += enc_ctx_uint(0, 0)  # subscriber-process-id placeholder
        apdu += enc_ctx_oid(1, OBJ_DEVICE, self.device_instance)
        apdu += enc_ctx_oid(2, obj.object_type, obj.instance)
        apdu += enc_ctx_uint(3, 0)   # time-remaining: indefinite
        apdu += enc_ctx_open(4)

        # present-value
        apdu += enc_ctx_open(2)
        apdu += enc_ctx_uint(0, 85)   # property-identifier = PRESENT_VALUE
        pv_bytes = encode_property_value(obj, 85)
        if pv_bytes:
            apdu += enc_ctx_open(4)
            apdu += pv_bytes
            apdu += enc_ctx_close(4)
        apdu += enc_ctx_close(2)

        # status-flags
        apdu += enc_ctx_open(2)
        apdu += enc_ctx_uint(0, 111)   # property-identifier = STATUS_FLAGS
        apdu += enc_ctx_open(4)
        apdu += enc_app_bitstr(obj.status_flags, 4)
        apdu += enc_ctx_close(4)
        apdu += enc_ctx_close(2)

        apdu += enc_ctx_close(4)
        return build_bvll(build_npdu(apdu))

    # ─────────────────────────────────────────────────────────────
    #  Device property encoding
    # ─────────────────────────────────────────────────────────────

    def _encode_device_prop(self, prop_id: int,
                             array_index: Optional[int] = None) -> Optional[bytes]:
        """Encode a device-object property, honouring array_index for array props."""
        return encode_device_property_value(
            prop_id,
            self.device_instance,
            self.device_name,
            list(self._objects.values()),
            array_index=array_index,
        )

    # ─────────────────────────────────────────────────────────────
    #  Send helper
    # ─────────────────────────────────────────────────────────────

    def _send(self, pkt: bytes, addr: Tuple[str, int],
              fallback_sock: Optional[socket.socket] = None):
        sock = self._send_sock or fallback_sock
        if sock is None:
            return
        try:
            sock.sendto(pkt, addr)
        except Exception as exc:
            self._log(f"[BACnet] {self.device_name}: send error → {addr}: {exc}",
                      "warning")

    def _log(self, msg: str, level: str = "info"):
        log.info(msg)
        if self._log_cb:
            try:
                self._log_cb(msg, level)
            except Exception:
                pass