"""
Raw SNMPv2c GET for sysDescr.0 — no pysnmp needed.
Community: 10.100.0.2   Target: 10.100.0.2:161
"""
import socket

# Pre-built SNMPv2c GET packet:
#   community = "10.100.0.2", OID = 1.3.6.1.2.1.1.1.0 (sysDescr.0)
packet = bytes.fromhex(
    "302a"          # SEQUENCE (42 bytes)
    "020101"        # INTEGER 1  (version = SNMPv2c)
    "040a"          # OCTET STRING (10 bytes) = community
    "31302e3130302e302e32"  # "10.100.0.2"
    "a019"          # GetRequest-PDU (25 bytes)
    "020101"        # request-id = 1
    "020100"        # error-status = 0
    "020100"        # error-index  = 0
    "300e"          # VarBindList
    "300c"          # VarBind
    "06082b06010201010100"  # OID 1.3.6.1.2.1.1.1.0
    "0500"          # NULL
)

target = ("10.100.0.2", 161)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(5)

try:
    s.sendto(packet, target)
    print(f"Sent {len(packet)}-byte SNMP GET to {target[0]}:{target[1]}")
    data, addr = s.recvfrom(4096)
    print(f"Got {len(data)}-byte response from {addr[0]}:{addr[1]}")
    print(f"Raw hex: {data.hex()}")
    # Try to extract a readable string from the response
    try:
        text = data[data.index(b'\x04', 20):]
        length = text[1]
        print(f"Payload string: {text[2:2+length].decode('utf-8', errors='replace')}")
    except Exception:
        pass
except socket.timeout:
    print("TIMEOUT — no response from snmpsim within 5 seconds")
except Exception as e:
    print(f"Error: {e}")
finally:
    s.close()
