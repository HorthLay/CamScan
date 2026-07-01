"""
Test script for IP-based camera control functionality.
This script tests the camera access control system.
"""

import asyncio
import aiohttp
import json

API_BASE = "http://127.0.0.1:8001"


async def test_camera_control():
    print("Testing IP-based Camera Control System")
    print("=" * 50)
    
    # Simulate different IPs using headers (in real scenario, these would be actual client IPs)
    ip1 = "192.168.1.100"
    ip2 = "192.168.1.101"
    
    async with aiohttp.ClientSession() as session:
        
        # Test 1: IP1 starts camera
        print(f"\n[Test 1] IP {ip1} starts camera")
        async with session.post(f"{API_BASE}/register/camera/start", 
                               headers={"X-Forwarded-For": ip1}) as resp:
            data = await resp.json()
            print(f"  Response: {json.dumps(data, indent=2)}")
            assert data["success"] == True, "IP1 should be able to start camera"
        
        # Test 2: Check status for IP1
        print(f"\n[Test 2] Check status for IP {ip1}")
        async with session.get(f"{API_BASE}/register/camera/status",
                              headers={"X-Forwarded-For": ip1}) as resp:
            data = await resp.json()
            print(f"  Response: {json.dumps(data, indent=2)}")
            assert data["is_allowed"] == True, "IP1 should be allowed"
            assert data["is_active"] == True, "IP1 should be active"
        
        # Test 3: IP2 tries to start camera (should work)
        print(f"\n[Test 3] IP {ip2} tries to start camera")
        async with session.post(f"{API_BASE}/register/camera/start",
                               headers={"X-Forwarded-For": ip2}) as resp:
            data = await resp.json()
            print(f"  Response: {json.dumps(data, indent=2)}")
            assert data["success"] == True, "IP2 should be able to start camera"
        
        # Test 4: IP1 stops camera
        print(f"\n[Test 4] IP {ip1} stops camera")
        async with session.post(f"{API_BASE}/register/camera/stop",
                               headers={"X-Forwarded-For": ip1}) as resp:
            data = await resp.json()
            print(f"  Response: {json.dumps(data, indent=2)}")
            assert data["success"] == True, "IP1 should be able to stop camera"
        
        # Test 5: IP1 tries to start camera again (should fail because IP2 is still using it)
        print(f"\n[Test 5] IP {ip1} tries to start camera again (should fail)")
        async with session.post(f"{API_BASE}/register/camera/start",
                               headers={"X-Forwarded-For": ip1}) as resp:
            data = await resp.json()
            print(f"  Response: {json.dumps(data, indent=2)}")
            assert data["success"] == False, "IP1 should NOT be able to restart while IP2 is using it"
        
        # Test 6: Check status for IP1
        print(f"\n[Test 6] Check status for IP {ip1}")
        async with session.get(f"{API_BASE}/register/camera/status",
                              headers={"X-Forwarded-For": ip1}) as resp:
            data = await resp.json()
            print(f"  Response: {json.dumps(data, indent=2)}")
            assert data["is_allowed"] == False, "IP1 should not be allowed while IP2 is using it"
        
        # Test 7: IP2 stops camera
        print(f"\n[Test 7] IP {ip2} stops camera")
        async with session.post(f"{API_BASE}/register/camera/stop",
                               headers={"X-Forwarded-For": ip2}) as resp:
            data = await resp.json()
            print(f"  Response: {json.dumps(data, indent=2)}")
            assert data["success"] == True, "IP2 should be able to stop camera"
        
        # Test 8: IP1 tries to start camera again (should work now that no one is using it)
        print(f"\n[Test 8] IP {ip1} tries to start camera again (should work)")
        async with session.post(f"{API_BASE}/register/camera/start",
                               headers={"X-Forwarded-For": ip1}) as resp:
            data = await resp.json()
            print(f"  Response: {json.dumps(data, indent=2)}")
            assert data["success"] == True, "IP1 should be able to restart when no one is using it"
        
        # Test 9: Clear stopped status for IP1
        print(f"\n[Test 9] Clear stopped status for IP {ip1}")
        async with session.post(f"{API_BASE}/register/camera/clear-stop",
                               headers={"X-Forwarded-For": ip1}) as resp:
            data = await resp.json()
            print(f"  Response: {json.dumps(data, indent=2)}")
            assert data["success"] == True, "Should be able to clear stopped status"
        
        # Test 10: Check active IPs
        print(f"\n[Test 10] List all active IPs")
        async with session.get(f"{API_BASE}/register/camera/active-ips") as resp:
            data = await resp.json()
            print(f"  Response: {json.dumps(data, indent=2)}")
        
        print("\n" + "=" * 50)
        print("All tests passed!")


if __name__ == "__main__":
    asyncio.run(test_camera_control())
