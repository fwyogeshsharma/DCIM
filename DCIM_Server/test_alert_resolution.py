#!/usr/bin/env python3
"""Test Alert Resolution API"""

import requests
import json
import urllib3

# Disable SSL warnings for testing
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL = "https://localhost:8443/api/v1/alerts"
CERT_DIR = r"C:\Anupam\Faber\Projects\DCIM\DCIM_Server\certs\agents\Aman-PC-UI"

def test_get_alert(alert_id):
    """Test GET /api/v1/alerts/{id}"""
    print(f"\n{'='*60}")
    print(f"TEST 1: Get Alert {alert_id}")
    print('='*60)

    response = requests.get(
        f"{URL}/{alert_id}",
        cert=(f"{CERT_DIR}\\client.crt", f"{CERT_DIR}\\client.key"),
        verify=False
    )

    print(f"Status: {response.status_code}")
    print(json.dumps(response.json(), indent=2))
    return response.json()

def test_resolve_alert(alert_id):
    """Test PUT /api/v1/alerts/{id}/resolve"""
    print(f"\n{'='*60}")
    print(f"TEST 2: Resolve Alert {alert_id}")
    print('='*60)

    resolve_data = {
        "resolved_by": "john.doe@company.com",
        "resolution_action": "Replaced condenser pump",
        "resolution_notes": "Pump motor was seized. Replaced with spare unit from inventory. System tested - temperatures normal."
    }

    print("\nRequest Body:")
    print(json.dumps(resolve_data, indent=2))

    response = requests.put(
        f"{URL}/{alert_id}/resolve",
        json=resolve_data,
        cert=(f"{CERT_DIR}\\client.crt", f"{CERT_DIR}\\client.key"),
        verify=False
    )

    print(f"\nStatus: {response.status_code}")
    print(json.dumps(response.json(), indent=2))
    return response.json()

def test_resolve_already_resolved(alert_id):
    """Test resolving already resolved alert (should get 409)"""
    print(f"\n{'='*60}")
    print(f"TEST 3: Try Resolving Already Resolved Alert")
    print('='*60)

    resolve_data = {
        "resolved_by": "another.user@company.com",
        "resolution_action": "Another attempt",
        "resolution_notes": "This should fail"
    }

    response = requests.put(
        f"{URL}/{alert_id}/resolve",
        json=resolve_data,
        cert=(f"{CERT_DIR}\\client.crt", f"{CERT_DIR}\\client.key"),
        verify=False
    )

    print(f"Status: {response.status_code} (Expected: 409 Conflict)")
    print(json.dumps(response.json(), indent=2))

def test_resolve_missing_fields(alert_id):
    """Test resolving without required fields (should get 400)"""
    print(f"\n{'='*60}")
    print(f"TEST 4: Resolve Without Required Fields")
    print('='*60)

    # Missing resolution_action
    resolve_data = {
        "resolved_by": "test@company.com"
    }

    response = requests.put(
        f"{URL}/{alert_id}/resolve",
        json=resolve_data,
        cert=(f"{CERT_DIR}\\client.crt", f"{CERT_DIR}\\client.key"),
        verify=False
    )

    print(f"Status: {response.status_code} (Expected: 400 Bad Request)")
    print(json.dumps(response.json(), indent=2))

if __name__ == "__main__":
    # Change this to an actual alert ID in your database
    alert_id = 1

    print("="*60)
    print("ALERT RESOLUTION API TESTS")
    print("="*60)

    # Test 1: Get alert details
    alert_data = test_get_alert(alert_id)

    # Test 2: Resolve the alert
    if alert_data.get("success"):
        test_resolve_alert(alert_id)

    # Test 3: Try resolving again (should fail with 409)
    test_resolve_already_resolved(alert_id)

    # Test 4: Try resolving with missing fields
    test_resolve_missing_fields(alert_id + 1)

    print(f"\n{'='*60}")
    print("TESTS COMPLETE")
    print('='*60)
