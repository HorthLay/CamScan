"""
WebSocket Server for Real-time Camera Control
Handles IP-based camera access with WebSocket connections
"""

import asyncio
import json
import threading
import traceback
from typing import Dict, Set
from fastapi import WebSocket, WebSocketDisconnect

from services.capture_service import (
    start_camera_for_ip,
    stop_camera_for_ip,
    is_ip_allowed,
    get_active_ips,
    get_stopped_ips,
    clear_stopped_ip,
    get_current_stream_source
)

# Global WebSocket connection manager
class WebSocketManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.lock = threading.Lock()

    async def connect(self, websocket: WebSocket, client_ip: str):
        """Add a new WebSocket connection for an IP"""
        await websocket.accept()
        with self.lock:
            if client_ip not in self.active_connections:
                self.active_connections[client_ip] = set()
            self.active_connections[client_ip].add(websocket)
        print(f"[WS] Client connected from IP: {client_ip}")
        
        # Send current status to new client
        try:
            await self.send_to_ip({
                "action": "status_update",
                "ip": client_ip,
                "is_allowed": is_ip_allowed(client_ip),
                "is_active": client_ip in get_active_ips(),
                "is_stopped": client_ip in get_stopped_ips(),
                "active_ips": list(get_active_ips()),
                "stopped_ips": list(get_stopped_ips()),
                "stream_source": get_current_stream_source()
            }, client_ip)
        except Exception as e:
            print(f"[WS] Error sending initial status to {client_ip}: {e}")
        
        # Broadcast new connection to all clients (for admin monitoring)
        try:
            await self.broadcast({
                "action": "ip_connected",
                "ip": client_ip
            }, exclude_ip=client_ip)
        except Exception as e:
            print(f"[WS] Error broadcasting connection for {client_ip}: {e}")

    async def disconnect(self, websocket: WebSocket, client_ip: str):
        """Remove a WebSocket connection"""
        with self.lock:
            if client_ip in self.active_connections:
                self.active_connections[client_ip].discard(websocket)
                if not self.active_connections[client_ip]:
                    del self.active_connections[client_ip]
        print(f"[WS] Client disconnected from IP: {client_ip}")
        
        # Broadcast disconnection (safely)
        try:
            await self.broadcast({
                "action": "ip_disconnected",
                "ip": client_ip,
                "active_ips": list(get_active_ips()),
                "stopped_ips": list(get_stopped_ips())
            })
        except Exception as e:
            print(f"[WS] Error broadcasting disconnect for {client_ip}: {e}")

    async def send_to_ip(self, message: dict, client_ip: str):
        """Send message to all connections from a specific IP"""
        connections_snapshot = []
        with self.lock:
            if client_ip in self.active_connections:
                connections_snapshot = list(self.active_connections[client_ip])
        
        disconnected = set()
        for connection in connections_snapshot:
            try:
                await connection.send_text(json.dumps(message))
            except Exception as e:
                disconnected.add(connection)
                print(f"[WS] Error sending to {client_ip}: {e}")

        # Clean up disconnected
        if disconnected:
            with self.lock:
                if client_ip in self.active_connections:
                    self.active_connections[client_ip] -= disconnected
                    if not self.active_connections[client_ip]:
                        del self.active_connections[client_ip]

    async def broadcast(self, message: dict, exclude_ip: str = None):
        """Broadcast message to all connected clients"""
        connections_snapshot = {}
        with self.lock:
            for ip, conns in self.active_connections.items():
                if exclude_ip and ip == exclude_ip:
                    continue
                connections_snapshot[ip] = list(conns)
        
        for ip, connections in connections_snapshot.items():
            disconnected = set()
            for connection in connections:
                try:
                    await connection.send_text(json.dumps(message))
                except Exception as e:
                    disconnected.add(connection)
                    print(f"[WS] Error broadcasting to {ip}: {e}")

            # Clean up disconnected
            if disconnected:
                with self.lock:
                    if ip in self.active_connections:
                        self.active_connections[ip] -= disconnected
                        if not self.active_connections[ip]:
                            del self.active_connections[ip]

# Global manager instance
manager = WebSocketManager()


def _get_client_ip(websocket: WebSocket) -> str:
    """Safely extract client IP from WebSocket connection."""
    # Try X-Forwarded-For header first (proxy)
    forwarded = websocket.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    
    # Try websocket.client
    if websocket.client and websocket.client.host:
        return websocket.client.host
    
    # Fallback
    return "unknown"


async def websocket_camera_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time camera control.
    
    Message format:
    {
        "action": "start_camera" | "stop_camera" | "get_status" | "clear_stop" | "get_active_ips",
        "stream_url": "optional RTSP/HTTP URL"
    }
    
    Response format:
    {
        "action": "response",
        "request_id": "unique_id",
        "success": true/false,
        "data": { ... }
    }
    """
    # Get client IP safely
    client_ip = _get_client_ip(websocket)
    
    try:
        # Accept connection and add to manager
        await manager.connect(websocket, client_ip)
        
        # Process incoming messages
        request_id = 0
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                request_id += 1
                
                action = message.get("action", "")
                req_id = message.get("request_id", request_id)
                
                print(f"[WS] Received from {client_ip}: action={action}")
                
                # Handle different action types
                if action == "start_camera":
                    stream_url = message.get("stream_url")  # Optional RTSP/HTTP URL
                    try:
                        from starlette.concurrency import run_in_threadpool
                        success = await run_in_threadpool(start_camera_for_ip, client_ip, stream_url)
                        await manager.send_to_ip({
                            "action": "response",
                            "request_id": req_id,
                            "success": success,
                            "message": "Camera started" if success else "Camera access denied (another IP is using it)",
                            "ip": client_ip,
                            "is_allowed": is_ip_allowed(client_ip),
                            "is_active": client_ip in get_active_ips(),
                            "stream_source": get_current_stream_source()
                        }, client_ip)
                        
                        # Broadcast status change to all clients
                        await manager.broadcast({
                            "action": "ip_status_changed",
                            "ip": client_ip,
                            "status": "active" if success else "blocked",
                            "active_ips": list(get_active_ips()),
                            "stopped_ips": list(get_stopped_ips()),
                            "stream_source": get_current_stream_source()
                        }, exclude_ip=client_ip)
                    except Exception as cam_err:
                        print(f"[WS] Camera start error for {client_ip}: {cam_err}")
                        await manager.send_to_ip({
                            "action": "response",
                            "request_id": req_id,
                            "success": False,
                            "message": f"Camera error: {str(cam_err)}",
                            "ip": client_ip
                        }, client_ip)

                elif action == "stop_camera":
                    try:
                        from starlette.concurrency import run_in_threadpool
                        await run_in_threadpool(stop_camera_for_ip, client_ip)
                        await manager.send_to_ip({
                            "action": "response",
                            "request_id": req_id,
                            "success": True,
                            "message": "Camera stopped",
                            "ip": client_ip,
                            "is_allowed": is_ip_allowed(client_ip),
                            "is_active": client_ip in get_active_ips()
                        }, client_ip)
                        
                        # Broadcast status change to all clients
                        await manager.broadcast({
                            "action": "ip_status_changed",
                            "ip": client_ip,
                            "status": "stopped",
                            "active_ips": list(get_active_ips()),
                            "stopped_ips": list(get_stopped_ips())
                        }, exclude_ip=client_ip)
                    except Exception as stop_err:
                        print(f"[WS] Camera stop error for {client_ip}: {stop_err}")
                        await manager.send_to_ip({
                            "action": "response",
                            "request_id": req_id,
                            "success": True,  # Still report success — camera is considered stopped
                            "message": f"Camera stopped (with warning: {str(stop_err)})",
                            "ip": client_ip
                        }, client_ip)

                elif action == "get_status":
                    await manager.send_to_ip({
                        "action": "response",
                        "request_id": req_id,
                        "success": True,
                        "ip": client_ip,
                        "is_allowed": is_ip_allowed(client_ip),
                        "is_active": client_ip in get_active_ips(),
                        "is_stopped": client_ip in get_stopped_ips(),
                        "active_ips": list(get_active_ips()),
                        "stopped_ips": list(get_stopped_ips()),
                        "stream_source": get_current_stream_source()
                    }, client_ip)

                elif action == "clear_stop":
                    clear_stopped_ip(client_ip)
                    await manager.send_to_ip({
                        "action": "response",
                        "request_id": req_id,
                        "success": True,
                        "message": "Stopped status cleared",
                        "ip": client_ip,
                        "is_allowed": is_ip_allowed(client_ip),
                        "is_active": client_ip in get_active_ips()
                    }, client_ip)
                    
                    await manager.broadcast({
                        "action": "ip_status_changed",
                        "ip": client_ip,
                        "status": "cleared",
                        "active_ips": list(get_active_ips()),
                        "stopped_ips": list(get_stopped_ips())
                    }, exclude_ip=client_ip)

                elif action == "get_active_ips":
                    await manager.send_to_ip({
                        "action": "response",
                        "request_id": req_id,
                        "success": True,
                        "active_ips": list(get_active_ips()),
                        "stopped_ips": list(get_stopped_ips())
                    }, client_ip)

                elif action == "ping":
                    await manager.send_to_ip({
                        "action": "pong",
                        "timestamp": message.get("timestamp")
                    }, client_ip)

                else:
                    await manager.send_to_ip({
                        "action": "error",
                        "request_id": req_id,
                        "error": f"Unknown action: {action}",
                        "received_action": action
                    }, client_ip)

            except json.JSONDecodeError as e:
                try:
                    await manager.send_to_ip({
                        "action": "error",
                        "error": "Invalid JSON",
                        "details": str(e)
                    }, client_ip)
                except Exception:
                    pass
            except WebSocketDisconnect:
                raise  # Re-raise to be caught by outer handler
            except Exception as e:
                print(f"[WS] Error processing message from {client_ip}: {e}")
                traceback.print_exc()
                try:
                    await manager.send_to_ip({
                        "action": "error",
                        "error": str(e)
                    }, client_ip)
                except Exception:
                    pass

    except WebSocketDisconnect:
        print(f"[WS] Client {client_ip} disconnected normally")
        await manager.disconnect(websocket, client_ip)
    except Exception as e:
        print(f"[WS] WebSocket error for {client_ip}: {e}")
        traceback.print_exc()
        await manager.disconnect(websocket, client_ip)
