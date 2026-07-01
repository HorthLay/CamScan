"""
WebSocket Server for Real-time Camera Control
Handles IP-based camera access with WebSocket connections
"""

import asyncio
import json
import threading
from typing import Dict, Set
from fastapi import WebSocket, WebSocketDisconnect

from services.capture_service import (
    start_camera_for_ip,
    stop_camera_for_ip,
    is_ip_allowed,
    get_active_ips,
    get_stopped_ips,
    clear_stopped_ip
)

# Global WebSocket connection manager
class WebSocketManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.lock = threading.Lock()

    async def connect(self, websocket: WebSocket, client_ip: str):
        """Add a new WebSocket connection for an IP"""
        with self.lock:
            if client_ip not in self.active_connections:
                self.active_connections[client_ip] = set()
            self.active_connections[client_ip].add(websocket)
        await websocket.accept()
        print(f"[WS] Client connected from IP: {client_ip}")
        
        # Send current status to new client
        await self.send_to_ip({
            "action": "status_update",
            "ip": client_ip,
            "is_allowed": is_ip_allowed(client_ip),
            "is_active": client_ip in get_active_ips(),
            "is_stopped": client_ip in get_stopped_ips(),
            "active_ips": list(get_active_ips()),
            "stopped_ips": list(get_stopped_ips())
        }, client_ip)
        
        # Broadcast new connection to all clients (for admin monitoring)
        await self.broadcast({
            "action": "ip_connected",
            "ip": client_ip
        }, exclude_ip=client_ip)

    def disconnect(self, websocket: WebSocket, client_ip: str):
        """Remove a WebSocket connection"""
        with self.lock:
            if client_ip in self.active_connections:
                self.active_connections[client_ip].discard(websocket)
                if not self.active_connections[client_ip]:
                    del self.active_connections[client_ip]
        print(f"[WS] Client disconnected from IP: {client_ip}")
        
        # Broadcast disconnection
        asyncio.create_task(self.broadcast({
            "action": "ip_disconnected",
            "ip": client_ip
        }))

    async def send_to_ip(self, message: dict, client_ip: str):
        """Send message to all connections from a specific IP"""
        with self.lock:
            if client_ip in self.active_connections:
                disconnected = set()
                for connection in self.active_connections[client_ip]:
                    try:
                        await connection.send_text(json.dumps(message))
                    except (RuntimeError, ConnectionError) as e:
                        disconnected.add(connection)
                        print(f"[WS] Error sending to {client_ip}: {e}")

                # Clean up disconnected
                self.active_connections[client_ip] -= disconnected

    async def broadcast(self, message: dict, exclude_ip: str = None):
        """Broadcast message to all connected clients"""
        with self.lock:
            for ip, connections in list(self.active_connections.items()):
                if exclude_ip and ip == exclude_ip:
                    continue
                disconnected = set()
                for connection in connections:
                    try:
                        await connection.send_text(json.dumps(message))
                    except (RuntimeError, ConnectionError) as e:
                        disconnected.add(connection)
                        print(f"[WS] Error broadcasting to {ip}: {e}")

                # Clean up disconnected
                if ip in self.active_connections:
                    self.active_connections[ip] -= disconnected
                    if not self.active_connections[ip]:
                        del self.active_connections[ip]

# Global manager instance
manager = WebSocketManager()


async def websocket_camera_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time camera control.
    
    Message format:
    {
        "action": "start_camera" | "stop_camera" | "get_status" | "clear_stop" | "get_active_ips"
    }
    
    Response format:
    {
        "action": "response",
        "request_id": "unique_id",
        "success": true/false,
        "data": { ... }
    }
    """
    # Get client IP
    client_ip = websocket.headers.get("x-forwarded-for") or websocket.client.host
    
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
                
                print(f"[WS] Received from {client_ip}: {message}")
                
                # Handle different action types
                if message.get("action") == "start_camera":
                    success = start_camera_for_ip(client_ip)
                    await manager.send_to_ip({
                        "action": "response",
                        "request_id": message.get("request_id", request_id),
                        "success": success,
                        "message": "Camera started" if success else "Camera access denied (another IP is using it)",
                        "ip": client_ip,
                        "is_allowed": is_ip_allowed(client_ip),
                        "is_active": client_ip in get_active_ips()
                    }, client_ip)
                    
                    # Broadcast status change to all clients
                    await manager.broadcast({
                        "action": "ip_status_changed",
                        "ip": client_ip,
                        "status": "active" if success else "blocked",
                        "active_ips": list(get_active_ips()),
                        "stopped_ips": list(get_stopped_ips())
                    }, exclude_ip=client_ip)

                elif message.get("action") == "stop_camera":
                    stop_camera_for_ip(client_ip)
                    await manager.send_to_ip({
                        "action": "response",
                        "request_id": message.get("request_id", request_id),
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

                elif message.get("action") == "get_status":
                    await manager.send_to_ip({
                        "action": "response",
                        "request_id": message.get("request_id", request_id),
                        "success": True,
                        "ip": client_ip,
                        "is_allowed": is_ip_allowed(client_ip),
                        "is_active": client_ip in get_active_ips(),
                        "is_stopped": client_ip in get_stopped_ips(),
                        "active_ips": list(get_active_ips()),
                        "stopped_ips": list(get_stopped_ips())
                    }, client_ip)

                elif message.get("action") == "clear_stop":
                    clear_stopped_ip(client_ip)
                    await manager.send_to_ip({
                        "action": "response",
                        "request_id": message.get("request_id", request_id),
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

                elif message.get("action") == "get_active_ips":
                    await manager.send_to_ip({
                        "action": "response",
                        "request_id": message.get("request_id", request_id),
                        "success": True,
                        "active_ips": list(get_active_ips()),
                        "stopped_ips": list(get_stopped_ips())
                    }, client_ip)

                elif message.get("action") == "ping":
                    await manager.send_to_ip({
                        "action": "pong",
                        "timestamp": message.get("timestamp")
                    }, client_ip)

                else:
                    await manager.send_to_ip({
                        "action": "error",
                        "request_id": message.get("request_id", request_id),
                        "error": "Unknown action",
                        "received_action": message.get("action")
                    }, client_ip)

            except json.JSONDecodeError as e:
                await manager.send_to_ip({
                    "action": "error",
                    "error": "Invalid JSON",
                    "details": str(e)
                }, client_ip)
            except Exception as e:
                print(f"[WS] Error processing message from {client_ip}: {e}")
                await manager.send_to_ip({
                    "action": "error",
                    "error": str(e)
                }, client_ip)

    except WebSocketDisconnect:
        manager.disconnect(websocket, client_ip)
    except Exception as e:
        print(f"[WS] WebSocket error for {client_ip}: {e}")
        manager.disconnect(websocket, client_ip)
