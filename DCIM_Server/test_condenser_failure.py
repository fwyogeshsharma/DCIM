#!/usr/bin/env python3
"""Test Condenser Failure Detection"""

import json
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL = "https://localhost:8443/api/v1/cooling-metrics"
AGENT_ID = "Aman-PC-UI"
CERT_DIR = r"C:\Anupam\Faber\Projects\DCIM\DCIM_Server\certs\agents\Aman-PC-UI"
PAYLOAD_FILE = r"C:\Anupam\Faber\Projects\DCIM\DCIM_Server\test_payloads\new_structure_condenser_failure.json"

with open(PAYLOAD_FILE, 'r') as f:
    payload = json.load(f)

print("=" * 60)
print("Testing CONDENSER FAILURE Detection")
print("=" * 60)
print(f"Scenario: Condenser ON but not cooling")
print(f"Expected: CRITICAL alerts for condenser failure")
print()

try:
    response = requests.post(
        URL,
        json=payload,
        headers={"X-Agent-ID": AGENT_ID},
        cert=(f"{CERT_DIR}\\client.crt", f"{CERT_DIR}\\client.key"),
        verify=False
    )

    result = response.json()
    print(f"[SUCCESS] Status Code: {response.status_code}")
    print()
    print(f"Metrics Stored: {result['data']['metrics_stored']}")
    print(f"Alerts Generated: {result['data']['alerts_generated']}")
    print()
    print("Full Response:")
    print(json.dumps(result, indent=2))

except Exception as e:
    print(f"[FAILED] Error: {e}")
