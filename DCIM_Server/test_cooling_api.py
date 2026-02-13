#!/usr/bin/env python3
"""Test Cooling Metrics API with mTLS"""

import json
import requests
import urllib3

# Disable SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration
URL = "https://localhost:8443/api/v1/cooling-metrics"
AGENT_ID = "Aman-PC-UI"
CERT_DIR = r"C:\Anupam\Faber\Projects\DCIM\DCIM_Server\certs\agents\Aman-PC-UI"
PAYLOAD_FILE = r"C:\Anupam\Faber\Projects\DCIM\DCIM_Server\test_payloads\new_structure_normal.json"

# Load test payload
with open(PAYLOAD_FILE, 'r') as f:
    payload = json.load(f)

print("=" * 60)
print("Testing Cooling Metrics API")
print("=" * 60)
print(f"URL: {URL}")
print(f"Agent ID: {AGENT_ID}")
print(f"Payload: {PAYLOAD_FILE}")
print()

try:
    # Make request with client certificates
    response = requests.post(
        URL,
        json=payload,
        headers={
            "X-Agent-ID": AGENT_ID,
            "Content-Type": "application/json"
        },
        cert=(
            f"{CERT_DIR}\\client.crt",
            f"{CERT_DIR}\\client.key"
        ),
        verify=False  # Skip server cert verification for self-signed certs
    )

    print("[SUCCESS]")
    print(f"Status Code: {response.status_code}")
    print()
    print("Response:")
    print(json.dumps(response.json(), indent=2))

except requests.exceptions.SSLError as e:
    print(f"[FAILED] SSL Error: {e}")
    print("\nNote: Server requires client certificates (mTLS)")

except requests.exceptions.ConnectionError as e:
    print(f"[FAILED] Connection Error: {e}")
    print("\nIs the server running on port 8443?")

except Exception as e:
    print(f"[FAILED] Error: {e}")
    if hasattr(e, 'response'):
        print(f"\nServer Response:")
        try:
            print(json.dumps(e.response.json(), indent=2))
        except:
            print(e.response.text)
