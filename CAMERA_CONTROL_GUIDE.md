# IP-based Camera Control Implementation Guide

## Overview

This implementation adds IP-based camera access control to the CamScan system. It ensures that:

1. **Multiple IPs can use the camera simultaneously** - Each IP that starts the camera can use it independently
2. **IP-specific stop** - When an IP stops the camera, it only stops for that specific IP
3. **Blocked reopen** - An IP that has stopped the camera cannot restart it while other IPs are still using it
4. **Automatic release** - The camera hardware is automatically released when the last IP stops using it

## Changes Made

### 1. `services/capture_service.py`

Added IP-based tracking to the camera service:

- **New Global Variables:**
  - `_active_ips: Set[str]` - Tracks which IPs currently have the camera open
  - `_stopped_ips: Set[str]` - Tracks which IPs have explicitly stopped the camera
  - `_camera_lock: threading.Lock()` - Thread-safe lock for IP operations

- **New Functions:**
  - `start_camera_for_ip(ip: str) -> bool` - Start camera access for a specific IP
  - `stop_camera_for_ip(ip: str) -> bool` - Stop camera access for a specific IP
  - `is_ip_allowed(ip: str) -> bool` - Check if an IP can use the camera
  - `get_active_ips() -> Set[str]` - Get all currently active IPs
  - `get_stopped_ips() -> Set[str]` - Get all stopped IPs
  - `clear_stopped_ip(ip: str)` - Clear stopped status for an IP

- **Modified Functions:**
  - `release_camera(force: bool = False)` - Now checks both `_active_streams` and `_active_ips` before releasing

### 2. `main.py`

- Imported new IP-based camera control functions
- Modified `/video_feed` endpoint to:
  - Check if requesting IP is allowed to use camera
  - Start camera for the requesting IP
  - Return 403 Forbidden if IP is not allowed

### 3. `routers/registration.py`

- Added new endpoints for IP-based camera control:
  - `POST /register/camera/start` - Start camera for requesting IP
  - `POST /register/camera/stop` - Stop camera for requesting IP
  - `GET /register/camera/status` - Check camera access status for requesting IP
  - `POST /register/camera/clear-stop` - Clear stopped status for requesting IP
  - `GET /register/camera/active-ips` - List all active and stopped IPs

- Modified existing endpoints to use IP-based control:
  - `POST /register/search` - Now starts camera for requesting IP and stops when done
  - `GET /register/preview` - Now checks IP permission and tracks usage

## How It Works

### State Tracking

The system maintains two sets of IP addresses:

1. **`_active_ips`** - IPs that currently have the camera open and are using it
2. **`_stopped_ips`** - IPs that have explicitly stopped the camera

### Logic Flow

#### Starting Camera for an IP
```
1. Check if IP is in _stopped_ips
2. If yes, check if other IPs are active (_active_ips - {this_ip})
3. If other IPs are active, return False (blocked)
4. If no other IPs are active, remove from _stopped_ips and continue
5. Add IP to _active_ips
6. Ensure camera is initialized
7. Return True (success)
```

#### Stopping Camera for an IP
```
1. Remove IP from _active_ips
2. Add IP to _stopped_ips
3. If _active_ips is empty, release camera hardware
4. Return True (success)
```

#### Checking if IP is Allowed
```
1. If IP is in _stopped_ips
2. Check if other IPs are active
3. If other IPs are active, return False (not allowed)
4. Otherwise, return True (allowed)
```

## API Usage Examples

### 1. Start Camera for Current IP

```bash
curl -X POST http://127.0.0.1:8001/register/camera/start
```

Response (success):
```json
{
  "success": true,
  "message": "Camera started for IP 192.168.1.100",
  "ip": "192.168.1.100",
  "camera_active": true
}
```

Response (blocked):
```json
{
  "success": false,
  "message": "IP 192.168.1.100 is not allowed to use camera (another IP is using it)",
  "ip": "192.168.1.100",
  "camera_active": false
}
```

### 2. Stop Camera for Current IP

```bash
curl -X POST http://127.0.0.1:8001/register/camera/stop
```

Response:
```json
{
  "success": true,
  "message": "Camera stopped for IP 192.168.1.100",
  "ip": "192.168.1.100",
  "active_ips": ["192.168.1.101"]
}
```

### 3. Check Camera Status

```bash
curl http://127.0.0.1:8001/register/camera/status
```

Response:
```json
{
  "ip": "192.168.1.100",
  "is_allowed": false,
  "is_active": false,
  "is_stopped": true,
  "active_ips": ["192.168.1.101"],
  "stopped_ips": ["192.168.1.100"]
}
```

### 4. List All Active IPs

```bash
curl http://127.0.0.1:8001/register/camera/active-ips
```

Response:
```json
{
  "active_ips": ["192.168.1.100", "192.168.1.101"],
  "stopped_ips": ["192.168.1.102"]
}
```

### 5. Clear Stopped Status

```bash
curl -X POST http://127.0.0.1:8001/register/camera/clear-stop
```

Response:
```json
{
  "success": true,
  "message": "Stopped status cleared for IP 192.168.1.100",
  "ip": "192.168.1.100"
}
```

## Integration with Laravel

### Frontend (Blade/JavaScript)

```javascript
// In your Laravel blade template
const API_BASE = 'http://127.0.0.1:8001';

// Start camera for this user's IP
async function startCamera() {
    const response = await fetch(`${API_BASE}/register/camera/start`, {
        method: 'POST',
        credentials: 'include'
    });
    const data = await response.json();
    
    if (data.success) {
        console.log('Camera started for this IP');
        // Show camera feed
        cameraFeed.src = `${API_BASE}/video_feed?ts=${Date.now()}`;
    } else {
        alert(data.message);
    }
}

// Stop camera for this user's IP
async function stopCamera() {
    const response = await fetch(`${API_BASE}/register/camera/stop`, {
        method: 'POST',
        credentials: 'include'
    });
    const data = await response.json();
    
    if (data.success) {
        console.log('Camera stopped for this IP');
        // Hide camera feed
        cameraFeed.src = '';
    }
}

// Check camera availability before showing controls
async function checkCameraAvailable() {
    const response = await fetch(`${API_BASE}/register/camera/status`);
    const data = await response.json();
    
    if (data.is_allowed) {
        // Show camera controls
        startCameraButton.style.display = 'block';
    } else {
        // Show message
        startCameraButton.style.display = 'none';
        alert(`Camera not available: ${data.message || 'Another IP is using it'}`);
    }
}
```

### Backend (PHP Controller)

```php
<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;

class CameraController extends Controller
{
    public function startCamera(Request $request)
    {
        $response = Http::post('http://127.0.0.1:8001/register/camera/start');
        
        if ($response->successful()) {
            $data = $response->json();
            return response()->json($data);
        }
        
        return response()->json(['error' => 'Failed to start camera'], 500);
    }
    
    public function stopCamera(Request $request)
    {
        $response = Http::post('http://127.0.0.1:8001/register/camera/stop');
        
        if ($response->successful()) {
            $data = $response->json();
            return response()->json($data);
        }
        
        return response()->json(['error' => 'Failed to stop camera'], 500);
    }
    
    public function cameraStatus(Request $request)
    {
        $response = Http::get('http://127.0.0.1:8001/register/camera/status');
        
        if ($response->successful()) {
            return $response->json();
        }
        
        return response()->json(['error' => 'Failed to get camera status'], 500);
    }
}
```

## Scenario Examples

### Example 1: Two Users Sharing Camera

1. **User A (IP: 192.168.1.100)** opens the dashboard and clicks "Start Camera"
   - Camera starts for 192.168.1.100
   - `_active_ips = {192.168.1.100}`
   - `_stopped_ips = {}`

2. **User B (IP: 192.168.1.101)** opens the dashboard and clicks "Start Camera"
   - Camera starts for 192.168.1.101
   - `_active_ips = {192.168.1.100, 192.168.1.101}`
   - `_stopped_ips = {}`

3. **User A** clicks "Stop Camera"
   - Camera stops for 192.168.1.100
   - `_active_ips = {192.168.1.101}`
   - `_stopped_ips = {192.168.1.100}`

4. **User A** tries to click "Start Camera" again
   - Request is **blocked** because 192.168.1.101 is still using it
   - User A sees message: "IP 192.168.1.100 is not allowed to use camera (another IP is using it)"

5. **User B** clicks "Stop Camera"
   - Camera stops for 192.168.1.101
   - `_active_ips = {}`
   - `_stopped_ips = {192.168.1.100, 192.168.1.101}`
   - Camera hardware is released

6. **User A** clicks "Start Camera" again
   - Camera starts for 192.168.1.100 (no other IPs are active)
   - `_stopped_ips` is automatically cleared for 192.168.1.100
   - `_active_ips = {192.168.1.100}`

### Example 2: User Reopens After Clear

1. **User A (IP: 192.168.1.100)** stops camera while **User B (IP: 192.168.1.101)** is using it
   - `_active_ips = {192.168.1.101}`
   - `_stopped_ips = {192.168.1.100}`

2. **User A** wants to use camera again but is blocked

3. **User A** calls `/register/camera/clear-stop`
   - `_stopped_ips = {}` (cleared)

4. **User A** tries to start camera
   - Still **blocked** because User B is active
   - The clear-stop only removes the stopped flag, but User A still can't use it while User B is active

5. **User B** stops camera
   - `_active_ips = {}`
   - `_stopped_ips = {}`
   - Camera hardware is released

6. **User A** can now start camera successfully

## Testing

A test script `test_camera_control.py` is provided to verify the IP-based camera control functionality:

```bash
python test_camera_control.py
```

This script tests all the scenarios described above.

## Important Notes

1. **IP Detection**: The system uses `request.client.host` to get the client IP. In production with reverse proxies (Nginx, Apache), you may need to configure the proxy to forward the real client IP in the `X-Forwarded-For` header.

2. **Thread Safety**: All IP operations are protected by `_camera_lock` to ensure thread safety.

3. **Camera Hardware**: The physical camera is only released when both:
   - No streams are active (`_active_streams == 0`)
   - No IPs are using it (`len(_active_ips) == 0`)

4. **Backward Compatibility**: Existing functionality (video_feed, search, preview) now automatically uses IP-based control without requiring changes to existing clients.

5. **Error Handling**: If camera initialization fails, the IP is automatically removed from `_active_ips`.

## Troubleshooting

### Issue: Camera not starting for any IP
- Check if camera is available: `cv2.VideoCapture(0).isOpened()`
- Try different camera index in `capture_service.py`
- Check for other applications using the camera

### Issue: IP is blocked but should be allowed
- Check `/register/camera/active-ips` to see current state
- Use `/register/camera/clear-stop` to reset stopped status
- Verify no other IPs are currently active

### Issue: Camera not releasing after all IPs stop
- Check if any streams are still active (`_active_streams`)
- Call `/register/camera/stop` from all IPs that used the camera
- Force release with `release_camera(force=True)` if needed

## Security Considerations

1. **IP Spoofing**: In a local network, IPs can be spoofed. For production use, consider:
   - Adding authentication tokens
   - Using session-based tracking instead of IP
   - Implementing rate limiting

2. **Camera Access**: The camera endpoints are now protected by IP-based control, but ensure your network firewall also restricts access to trusted IPs only.

3. **HTTPS**: Always use HTTPS in production to prevent man-in-the-middle attacks.
