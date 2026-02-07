#!/usr/bin/env python3
"""
X-Ray Generator Web Monitor

A FastAPI-based web interface for monitoring the X-Ray generator running in the same process.
Provides real-time progress updates via WebSocket using shared state.
"""

import asyncio
import threading
from datetime import datetime
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# =============================================================================
# Global State (Shared with xray_generator.py)
# =============================================================================

app = FastAPI(title="X-Ray Generator Monitor")

# Active WebSocket connections
active_connections: list[WebSocket] = []

# Progress state (updated by xray_generator.py via update_progress_state)
progress_state = {
    "status": "idle",  # idle, running, completed, error, terminated
    "current_book": "",
    "progress_pct": 0,
    "current_chunk": 0,
    "total_chunks": 0,
    "current_operation": "",
    "start_time": None,
    "end_time": None,
    "error_message": "",
    "stats": {},
    "ai_retry_count": 0,  # Track AI retry failures
    "ai_success_count": 0,  # Track AI successful calls
}

# Log buffer for recent messages
log_buffer: list[dict[str, str]] = []
MAX_LOG_ENTRIES = 100

# Thread lock for state updates
_state_lock = threading.Lock()

# Event loop reference (set when server starts)
_event_loop: asyncio.AbstractEventLoop | None = None


# =============================================================================
# State Update Functions (Called by xray_generator.py)
# =============================================================================


def increment_ai_retry_count() -> None:
    """Increment the AI retry failure counter (thread-safe)."""
    with _state_lock:
        progress_state["ai_retry_count"] += 1

    # Schedule broadcast if event loop is available
    if _event_loop is not None and not _event_loop.is_closed():
        asyncio.run_coroutine_threadsafe(
            _broadcast_progress(progress_state.copy()), _event_loop
        )


def increment_ai_success_count() -> None:
    """Increment the AI success counter (thread-safe)."""
    with _state_lock:
        progress_state["ai_success_count"] += 1

    # Schedule broadcast if event loop is available
    if _event_loop is not None and not _event_loop.is_closed():
        asyncio.run_coroutine_threadsafe(
            _broadcast_progress(progress_state.copy()), _event_loop
        )


def update_progress_state(
    status: str | None = None,
    current_book: str | None = None,
    progress_pct: int | None = None,
    current_chunk: int | None = None,
    total_chunks: int | None = None,
    current_operation: str | None = None,
    error_message: str | None = None,
    stats: dict | None = None,
) -> None:
    """Update progress state (thread-safe, can be called from any thread)."""
    with _state_lock:
        if status is not None:
            progress_state["status"] = status
            if status == "running" and progress_state["start_time"] is None:
                progress_state["start_time"] = datetime.now().isoformat()
                # Reset counters on new run
                progress_state["ai_retry_count"] = 0
                progress_state["ai_success_count"] = 0
            elif status in ("completed", "error", "terminated"):
                progress_state["end_time"] = datetime.now().isoformat()

        if current_book is not None:
            progress_state["current_book"] = current_book
        if progress_pct is not None:
            progress_state["progress_pct"] = progress_pct
        if current_chunk is not None:
            progress_state["current_chunk"] = current_chunk
        if total_chunks is not None:
            progress_state["total_chunks"] = total_chunks
        if current_operation is not None:
            progress_state["current_operation"] = current_operation
        if error_message is not None:
            progress_state["error_message"] = error_message
        if stats is not None:
            progress_state["stats"] = stats

    # Schedule broadcast if event loop is available
    if _event_loop is not None and not _event_loop.is_closed():
        asyncio.run_coroutine_threadsafe(
            _broadcast_progress(progress_state.copy()), _event_loop
        )


def add_log_entry(message: str, level: str = "INFO") -> None:
    """Add a log entry (thread-safe, can be called from any thread)."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "message": message,
        "level": level,
    }

    with _state_lock:
        log_buffer.append(entry)
        # Keep buffer size limited
        if len(log_buffer) > MAX_LOG_ENTRIES:
            log_buffer.pop(0)

    # Schedule broadcast if event loop is available
    if _event_loop is not None and not _event_loop.is_closed():
        asyncio.run_coroutine_threadsafe(
            _broadcast_progress(
                {
                    "type": "log",
                    "timestamp": entry["timestamp"],
                    "message": entry["message"],
                }
            ),
            _event_loop,
        )


# =============================================================================
# WebSocket Manager
# =============================================================================


async def _broadcast_progress(data: dict[str, Any]):
    """Broadcast progress update to all connected clients."""
    disconnected = []
    for connection in active_connections:
        try:
            await connection.send_json(data)
        except Exception:
            disconnected.append(connection)

    # Clean up disconnected clients
    for conn in disconnected:
        if conn in active_connections:
            active_connections.remove(conn)


async def _periodic_broadcast():
    """Periodically broadcast current state to keep connections alive."""
    while True:
        await asyncio.sleep(2)  # Broadcast every 2 seconds
        if active_connections:
            with _state_lock:
                state_copy = progress_state.copy()
            await _broadcast_progress(state_copy)


@app.on_event("startup")
async def startup_event():
    """Capture event loop reference on startup."""
    global _event_loop
    _event_loop = asyncio.get_event_loop()
    # Start periodic broadcast task
    asyncio.create_task(_periodic_broadcast())


# =============================================================================
# API Endpoints
# =============================================================================


@app.get("/")
async def get_index():
    """Serve the monitoring interface."""
    return HTMLResponse(HTML_CONTENT)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time progress updates."""
    await websocket.accept()
    active_connections.append(websocket)

    try:
        # Send current state immediately
        await websocket.send_json(progress_state)

        # Send recent logs
        for entry in log_buffer[-20:]:  # Last 20 entries
            await websocket.send_json(
                {
                    "type": "log",
                    "timestamp": entry["timestamp"],
                    "message": entry["message"],
                }
            )

        # Keep connection alive
        while True:
            # Just wait for messages (we don't expect any commands in this architecture)
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send a ping to keep connection alive
                await websocket.send_json({"type": "ping"})

    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)


@app.post("/terminate")
async def terminate_endpoint():
    """Terminate the entire process immediately."""
    import os

    # Log the termination
    add_log_entry("⚠️ Process termination requested via web UI")

    # Give a moment for the log to be sent
    await asyncio.sleep(0.5)

    # Force exit the entire process (including all threads)
    os._exit(1)

    # This line will never be reached, but return for type checking
    return {"success": True}


@app.get("/health")
async def health_check():
    """Health check endpoint for debugging."""
    return {
        "status": "ok",
        "connections": len(active_connections),
        "event_loop_running": _event_loop is not None and not _event_loop.is_closed(),
        "progress_state": progress_state,
    }


# =============================================================================
# HTML Frontend
# =============================================================================

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>X-Ray Generator Monitor</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        :root {
            --bg-primary: #0a0e27;
            --bg-secondary: #151932;
            --bg-card: #1a1f3a;
            --accent-primary: #6366f1;
            --accent-secondary: #8b5cf6;
            --accent-success: #10b981;
            --accent-error: #ef4444;
            --accent-warning: #f59e0b;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --border-color: rgba(148, 163, 184, 0.1);
        }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: linear-gradient(135deg, var(--bg-primary) 0%, #1a1f3a 100%);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 2rem;
            overflow-x: hidden;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        header {
            text-align: center;
            margin-bottom: 3rem;
            position: relative;
        }

        h1 {
            font-size: 3rem;
            font-weight: 700;
            background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 0.5rem;
            animation: fadeInDown 0.6s ease-out;
        }

        .subtitle {
            color: var(--text-secondary);
            font-size: 1.1rem;
            font-weight: 300;
            animation: fadeInUp 0.6s ease-out;
        }

        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 2rem;
            margin-bottom: 2rem;
        }

        @media (max-width: 1024px) {
            .grid {
                grid-template-columns: 1fr;
            }
        }

        .card {
            background: var(--bg-card);
            border-radius: 1rem;
            padding: 2rem;
            border: 1px solid var(--border-color);
            backdrop-filter: blur(10px);
            transition: all 0.3s ease;
            animation: fadeIn 0.6s ease-out;
        }

        .card:hover {
            transform: translateY(-4px);
            box-shadow: 0 20px 40px rgba(99, 102, 241, 0.15);
            border-color: rgba(99, 102, 241, 0.3);
        }

        .card-title {
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 1.5rem;
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .status-indicator {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            animation: pulse 2s ease-in-out infinite;
        }

        .status-idle { background: var(--text-secondary); }
        .status-running { background: var(--accent-primary); }
        .status-completed { background: var(--accent-success); }
        .status-error { background: var(--accent-error); }
        .status-terminated { background: var(--accent-warning); }

        .progress-container {
            margin: 1.5rem 0;
        }

        .progress-bar-bg {
            width: 100%;
            height: 8px;
            background: var(--bg-secondary);
            border-radius: 1rem;
            overflow: hidden;
            position: relative;
        }

        .progress-bar {
            height: 100%;
            background: linear-gradient(90deg, var(--accent-primary), var(--accent-secondary));
            border-radius: 1rem;
            transition: width 0.5s ease;
            position: relative;
            overflow: hidden;
        }

        .progress-bar::after {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent);
            animation: shimmer 2s infinite;
        }

        .progress-text {
            margin-top: 0.75rem;
            display: flex;
            justify-content: space-between;
            font-size: 0.9rem;
            color: var(--text-secondary);
        }

        .stat-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 1rem;
            margin-top: 1.5rem;
        }

        .stat-item {
            background: var(--bg-secondary);
            padding: 1rem;
            border-radius: 0.75rem;
            border: 1px solid var(--border-color);
        }

        .stat-label {
            font-size: 0.85rem;
            color: var(--text-secondary);
            margin-bottom: 0.5rem;
        }

        .stat-value {
            font-size: 1.5rem;
            font-weight: 600;
            color: var(--text-primary);
        }

        .log-container {
            background: var(--bg-secondary);
            border-radius: 0.75rem;
            padding: 1.5rem;
            max-height: 400px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 0.85rem;
            border: 1px solid var(--border-color);
        }

        .log-entry {
            padding: 0.5rem;
            margin-bottom: 0.25rem;
            border-radius: 0.25rem;
            animation: slideInLeft 0.3s ease-out;
        }

        .log-entry:hover {
            background: rgba(99, 102, 241, 0.1);
        }

        .log-timestamp {
            color: var(--accent-primary);
            margin-right: 0.5rem;
        }

        /* Notification Bubbles */
        .notification-container {
            position: fixed;
            top: 6rem;
            right: 2rem;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            z-index: 1000;
            max-width: 400px;
        }

        .notification {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-left: 4px solid var(--accent-primary);
            border-radius: 0.75rem;
            padding: 1rem 1.25rem;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
            backdrop-filter: blur(10px);
            animation: slideInRight 0.3s ease-out;
            display: flex;
            align-items: flex-start;
            gap: 0.75rem;
            max-width: 400px;
            transition: all 0.2s ease;
        }

        .notification:hover {
            transform: translateX(-4px);
            box-shadow: 0 12px 32px rgba(0, 0, 0, 0.4);
        }

        .notification.fade-out {
            animation: fadeOutRight 0.3s ease-out forwards;
        }

        /* Notification Types */
        .notification.success {
            background: rgba(16, 185, 129, 0.1);
            border-left-color: var(--accent-success);
            border-color: rgba(16, 185, 129, 0.3);
        }

        .notification.error {
            background: rgba(239, 68, 68, 0.1);
            border-left-color: var(--accent-error);
            border-color: rgba(239, 68, 68, 0.3);
        }

        .notification.warning {
            background: rgba(245, 158, 11, 0.1);
            border-left-color: var(--accent-warning);
            border-color: rgba(245, 158, 11, 0.3);
        }

        .notification.info {
            background: rgba(99, 102, 241, 0.1);
            border-left-color: var(--accent-primary);
            border-color: rgba(99, 102, 241, 0.3);
        }

        .notification-icon {
            font-size: 1.5rem;
            flex-shrink: 0;
            line-height: 1;
        }

        .notification-content {
            flex: 1;
            min-width: 0;
        }

        .notification-time {
            font-size: 0.7rem;
            color: var(--text-secondary);
            margin-bottom: 0.25rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-weight: 500;
        }

        .notification-message {
            font-size: 0.95rem;
            color: var(--text-primary);
            word-wrap: break-word;
            line-height: 1.4;
        }

        /* Modal Styles */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(10, 14, 39, 0.8);
            backdrop-filter: blur(4px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 2000;
            animation: fadeIn 0.3s ease-out;
        }

        .modal-overlay.active {
            display: flex;
        }

        .modal {
            background: var(--bg-card);
            border-radius: 1rem;
            border: 1px solid var(--border-color);
            max-width: 800px;
            width: 90%;
            max-height: 80vh;
            display: flex;
            flex-direction: column;
            animation: scaleIn 0.3s ease-out;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
        }

        .modal-header {
            padding: 1.5rem 2rem;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .modal-title {
            font-size: 1.5rem;
            font-weight: 600;
            color: var(--text-primary);
        }

        .modal-close {
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 1.5rem;
            cursor: pointer;
            padding: 0.25rem 0.5rem;
            border-radius: 0.25rem;
            transition: all 0.2s ease;
        }

        .modal-close:hover {
            background: var(--bg-secondary);
            color: var(--text-primary);
        }

        .modal-body {
            padding: 2rem;
            overflow-y: auto;
            flex: 1;
        }

        .connection-status {
            position: fixed;
            top: 2rem;
            right: 2rem;
            padding: 0.75rem 1.5rem;
            border-radius: 2rem;
            font-size: 0.85rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            backdrop-filter: blur(10px);
            border: 1px solid var(--border-color);
            animation: fadeIn 0.6s ease-out;
            z-index: 100;
        }

        .connection-connected {
            background: rgba(16, 185, 129, 0.2);
            color: var(--accent-success);
        }
        
        .connection-disconnected {
            background: rgba(239, 68, 68, 0.2);
            color: var(--accent-error);
            border-color: var(--accent-error);
        }
        
        .btn {
            padding: 0.75rem 1.5rem;
            border: none;
            border-radius: 0.5rem;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, var(--accent-primary) 0%, var(--accent-secondary) 100%);
            color: white;
            box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
        }
        
        .btn-primary:hover {
            box-shadow: 0 6px 20px rgba(99, 102, 241, 0.4);
            transform: translateY(-2px);
        }
        
        .btn-danger {
            background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
            color: white;
            box-shadow: 0 4px 12px rgba(239, 68, 68, 0.3);
        }
        
        .btn-danger:hover {
            background: linear-gradient(135deg, #dc2626 0%, #b91c1c 100%);
            box-shadow: 0 6px 20px rgba(239, 68, 68, 0.4);
            transform: translateY(-2px);
        }
        
        .btn-danger:active, .btn-primary:active {
            transform: translateY(0);
        }
        
        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        @keyframes fadeInDown {
            from {
                opacity: 0;
                transform: translateY(-20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        @keyframes fadeInUp {
            from {
                opacity: 0;
                transform: translateY(20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        @keyframes slideInLeft {
            from {
                opacity: 0;
                transform: translateX(-20px);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        @keyframes shimmer {
            0% { transform: translateX(-100%); }
            100% { transform: translateX(100%); }
        }

        @keyframes slideInRight {
            from {
                opacity: 0;
                transform: translateX(100%);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }

        @keyframes fadeOutRight {
            from {
                opacity: 1;
                transform: translateX(0);
            }
            to {
                opacity: 0;
                transform: translateX(100%);
            }
        }

        @keyframes scaleIn {
            from {
                opacity: 0;
                transform: scale(0.9);
            }
            to {
                opacity: 1;
                transform: scale(1);
            }
        }

        ::-webkit-scrollbar {
            width: 8px;
        }

        ::-webkit-scrollbar-track {
            background: var(--bg-secondary);
            border-radius: 1rem;
        }

        ::-webkit-scrollbar-thumb {
            background: var(--accent-primary);
            border-radius: 1rem;
        }

        ::-webkit-scrollbar-thumb:hover {
            background: var(--accent-secondary);
        }
    </style>
</head>
<body>
    <!-- Notification Container -->
    <div class="notification-container" id="notificationContainer"></div>

    <!-- Connection Status -->
    <div class="connection-status" id="connectionStatus">
        <div class="status-indicator"></div>
        <span>Connecting...</span>
    </div>

    <!-- Live Logs Modal -->
    <div class="modal-overlay" id="logsModal" onclick="closeLogsModal(event)">
        <div class="modal" onclick="event.stopPropagation()">
            <div class="modal-header">
                <h2 class="modal-title">📋 Live Logs</h2>
                <button class="modal-close" onclick="closeLogsModal()">&times;</button>
            </div>
            <div class="modal-body">
                <div class="log-container" id="logContainer">
                    <div class="log-entry">
                        <span class="log-timestamp">[System]</span>
                        <span>Waiting for connection...</span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="container">
        <header>
            <h1>X-Ray Generator Monitor</h1>
            <p class="subtitle">Real-time monitoring for EPUB X-Ray analysis</p>
        </header>

        <div class="grid">
            <div class="card">
                <h2 class="card-title">
                    <div class="status-indicator" id="statusIndicator"></div>
                    Status & Progress
                </h2>

                <div class="stat-grid">
                    <div class="stat-item">
                        <div class="stat-label">Status</div>
                        <div class="stat-value" id="statusText">Idle</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">Current Book</div>
                        <div class="stat-value" id="currentBook" style="font-size: 1rem;">-</div>
                    </div>
                </div>

                <div class="progress-container">
                    <div class="progress-bar-bg">
                        <div class="progress-bar" id="progressBar" style="width: 0%"></div>
                    </div>
                    <div class="progress-text">
                        <span id="progressPct">0%</span>
                        <span id="chunkInfo">0 / 0 chunks</span>
                    </div>
                </div>

                <div class="stat-item" style="margin-top: 1rem;">
                    <div class="stat-label">Current Operation</div>
                    <div class="stat-value" id="currentOp" style="font-size: 1rem;">-</div>
                </div>
                
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1rem;">
                    <div class="stat-item" style="background: rgba(16, 185, 129, 0.1); border-color: rgba(16, 185, 129, 0.3);">
                        <div class="stat-label">AI Success</div>
                        <div class="stat-value" id="aiSuccessCount" style="font-size: 1.5rem; color: var(--accent-success);">0</div>
                    </div>
                    
                    <div class="stat-item" style="background: rgba(239, 68, 68, 0.1); border-color: rgba(239, 68, 68, 0.3);">
                        <div class="stat-label">AI Retry Failures</div>
                        <div class="stat-value" id="aiRetryCount" style="font-size: 1.5rem; color: var(--accent-error);">0</div>
                    </div>
                </div>
                
                <button class="btn btn-primary" onclick="openLogsModal()" style="margin-top: 1.5rem; width: 100%;">
                    📋 View Live Logs
                </button>
                
                <button class="btn btn-danger" id="terminateBtn" onclick="terminateProcess()" style="margin-top: 1rem; width: 100%;">
                    ⚠️ Terminate Process
                </button>
            </div>

            <div class="card">
                <h2 class="card-title">
                    Statistics
                </h2>

                <div class="stat-grid">
                    <div class="stat-item">
                        <div class="stat-label">Start Time</div>
                        <div class="stat-value" id="startTime" style="font-size: 0.9rem;">-</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">End Time</div>
                        <div class="stat-value" id="endTime" style="font-size: 0.9rem;">-</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">Characters</div>
                        <div class="stat-value" id="statsChars">-</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">Locations</div>
                        <div class="stat-value" id="statsLocs">-</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">Themes</div>
                        <div class="stat-value" id="statsThemes">-</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">Events</div>
                        <div class="stat-value" id="statsEvents">-</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        console.log('Web Monitor JavaScript loaded');
        let ws = null;
        let reconnectAttempts = 0;
        const maxReconnectAttempts = 5;

        function connect() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws`;
            
            console.log('Attempting to connect to:', wsUrl);

            ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                console.log('WebSocket connected');
                reconnectAttempts = 0;
                updateConnectionStatus(true);
                addLog('System', 'Connected to monitor');
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);

                if (data.type === 'log') {
                    addLog(data.timestamp, data.message);
                } else if (data.type === 'ping') {
                    // Ignore ping messages
                } else {
                    updateUI(data);
                }
            };

            ws.onclose = () => {
                console.log('WebSocket disconnected');
                updateConnectionStatus(false);
                addLog('System', 'Disconnected from monitor');

                if (reconnectAttempts < maxReconnectAttempts) {
                    reconnectAttempts++;
                    setTimeout(connect, 2000 * reconnectAttempts);
                }
            };

            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                addLog('System', 'Connection error');
            };
        }

        function updateConnectionStatus(connected) {
            const statusEl = document.getElementById('connectionStatus');
            const indicator = statusEl.querySelector('.status-indicator');
            const text = statusEl.querySelector('span');

            if (connected) {
                statusEl.className = 'connection-status connection-connected';
                indicator.className = 'status-indicator status-completed';
                text.textContent = 'Connected';
            } else {
                statusEl.className = 'connection-status connection-disconnected';
                indicator.className = 'status-indicator status-error';
                text.textContent = 'Disconnected';
            }
        }

        function updateUI(state) {
            // Update status
            const statusText = document.getElementById('statusText');
            const statusIndicator = document.getElementById('statusIndicator');
            statusText.textContent = state.status.charAt(0).toUpperCase() + state.status.slice(1);
            statusIndicator.className = `status-indicator status-${state.status}`;

            // Update progress
            document.getElementById('progressBar').style.width = `${state.progress_pct}%`;
            document.getElementById('progressPct').textContent = `${state.progress_pct}%`;
            document.getElementById('chunkInfo').textContent = `${state.current_chunk} / ${state.total_chunks} chunks`;

            // Update current info
            document.getElementById('currentBook').textContent = state.current_book || '-';
            document.getElementById('currentOp').textContent = state.current_operation || '-';
            
            // Update AI counters
            const successCount = state.ai_success_count || 0;
            const retryCount = state.ai_retry_count || 0;
            document.getElementById('aiSuccessCount').textContent = successCount;
            document.getElementById('aiRetryCount').textContent = retryCount;

            // Update times
            if (state.start_time) {
                const startDate = new Date(state.start_time);
                document.getElementById('startTime').textContent = startDate.toLocaleTimeString();
            }
            if (state.end_time) {
                const endDate = new Date(state.end_time);
                document.getElementById('endTime').textContent = endDate.toLocaleTimeString();
            }

            // Update stats
            if (state.stats) {
                document.getElementById('statsChars').textContent = state.stats.characters || '-';
                document.getElementById('statsLocs').textContent = state.stats.locations || '-';
                document.getElementById('statsThemes').textContent = state.stats.themes || '-';
                document.getElementById('statsEvents').textContent = state.stats.events || '-';
            }
        }
        
        function terminateProcess() {
            if (confirm("⚠️ Are you sure you want to forcefully terminate the process?\\n\\nThis will immediately stop all processing and exit the program.")) {
                addLog('System', 'Terminating process...');
                // Send terminate request to server
                fetch('/terminate', { method: 'POST' })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            addLog('System', 'Process terminated successfully');
                        } else {
                            addLog('System', 'Failed to terminate: ' + (data.error || 'Unknown error'));
                        }
                    })
                    .catch(error => {
                        addLog('System', 'Error sending terminate request: ' + error);
                    });
            }
        }

        // =====================================================================
        // PM-Approved Message Filtering & Transformation System
        // =====================================================================
        // This system acts as a smart filter between technical logs and user-facing
        // notifications. It filters out noise and transforms messages into
        // user-friendly language.
        
        function shouldShowMessage(message) {
            // Filter out messages that provide no value to end users
            const msg = message.toLowerCase();
            
            // BLOCK: Internal consolidation updates (too technical, too frequent)
            if (msg.includes('[char]') || msg.includes('[loc]') || 
                msg.includes('updated') && (msg.includes('✓') || msg.includes('['))) {
                return false;
            }
            
            // BLOCK: Raw technical merge messages
            if (msg.includes('=== merging chunk') || msg.includes('===')) {
                return false;
            }
            
            // BLOCK: Verbose AI request details
            if (msg.includes('ai request sent') && msg.includes('chars)')) {
                return false;
            }
            
            // BLOCK: Raw merge statistics (shown in progress bar instead)
            if (msg.includes('[merged]') && msg.includes('chars:')) {
                return false;
            }
            
            // BLOCK: Consolidation processing details
            if (msg.includes('[consolidation]') && msg.includes('processing')) {
                return false;
            }
            
            // BLOCK: Checkpoint saves (not interesting to users)
            if (msg.includes('saved checkpoint') || msg.includes('saved') && msg.includes('.json')) {
                return false;
            }
            
            // BLOCK: Cache hits (internal optimization detail)
            if (msg.includes('using cached') || msg.includes('cache')) {
                return false;
            }
            
            // BLOCK: Parallel processing details
            if (msg.includes('parallel processing:') || msg.includes('workers')) {
                return false;
            }
            
            // ALLOW: Important user-facing messages
            return true;
        }
        
        function categorizeMessage(message) {
            // First check if we should even show this message
            if (!shouldShowMessage(message)) {
                return null; // Signal to skip this message
            }
            
            const msg = message.toLowerCase();
            
            // Success patterns
            if (msg.includes('✅') || msg.includes('✓') ||
                msg.includes('completed') || 
                msg.includes('success') ||
                msg.includes('finished') ||
                msg.includes('done')) {
                return {
                    type: 'success',
                    icon: '✅',
                    friendlyMessage: translateMessage(message, 'success')
                };
            }
            
            // Error patterns
            if (msg.includes('❌') || msg.includes('✗') ||
                msg.includes('error') || 
                msg.includes('failed') || 
                msg.includes('fatal') ||
                msg.includes('exception') ||
                msg.includes('crash')) {
                return {
                    type: 'error',
                    icon: '❌',
                    friendlyMessage: translateMessage(message, 'error')
                };
            }
            
            // Warning patterns
            if (msg.includes('⚠️') || msg.includes('⚠') ||
                msg.includes('warning') || 
                msg.includes('retry') ||
                msg.includes('retrying') ||
                msg.includes('terminate') ||
                msg.includes('skip')) {
                return {
                    type: 'warning',
                    icon: '⚠️',
                    friendlyMessage: translateMessage(message, 'warning')
                };
            }
            
            // Progress/Processing patterns
            if (msg.includes('processing') || 
                msg.includes('analyzing') ||
                msg.includes('chunk') ||
                msg.includes('reading') ||
                msg.includes('extracting') ||
                msg.includes('parsing') ||
                msg.includes('generating')) {
                return {
                    type: 'info',
                    icon: '🔄',
                    friendlyMessage: translateMessage(message, 'progress')
                };
            }
            
            // Connection/System patterns
            if (msg.includes('connected') || 
                msg.includes('disconnected') ||
                msg.includes('monitor') ||
                msg.includes('system')) {
                return {
                    type: 'info',
                    icon: '🔌',
                    friendlyMessage: translateMessage(message, 'system')
                };
            }
            
            // Default to info for anything else that passed the filter
            return {
                type: 'info',
                icon: '�',
                friendlyMessage: translateMessage(message, 'default')
            };
        }
        
        function translateMessage(message, context) {
            let friendly = message;
            
            // Remove emoji prefixes (we'll add our own)
            friendly = friendly.replace(/^[✅❌⚠️🔄📝🔌✓✗]\\s*/, '');
            
            // ===================================================================
            // TRANSFORMATION RULES: Technical → User-Friendly
            // ===================================================================
            
            // Rule 1: Simplify chunk processing messages
            // "Processing chunk 5/10" → "Analyzing part 5 of 10"
            friendly = friendly.replace(/processing chunk (\\d+)\\/(\\d+)/gi, 'Analyzing part $1 of $2');
            friendly = friendly.replace(/chunk (\\d+)\\/(\\d+)/gi, 'Part $1 of $2');
            
            // Rule 2: Remove technical chapter titles with Chinese characters
            // "=== Merging Chunk 5/10: 《第二章　亨哲呀，对不起（续1）》 ===" → "Processing chapter 5 of 10"
            friendly = friendly.replace(/===\\s*merging chunk (\\d+)\\/(\\d+):\\s*《[^》]+》\\s*===/gi, 
                'Processing chapter $1 of $2');
            
            // Rule 3: Simplify consolidation messages
            // "[Char] 父亲 updated" → skip (already filtered)
            // "Parallel processing: 3 chars, 2 locs" → skip (already filtered)
            
            // Rule 4: Humanize EPUB operations
            const operations = {
                'Extracting text from EPUB': 'Reading your book',
                'Parsing EPUB structure': 'Understanding book structure',
                'Reading EPUB': 'Opening book',
                'Processing EPUB': 'Analyzing book',
            };
            
            for (const [technical, userFriendly] of Object.entries(operations)) {
                const regex = new RegExp(technical, 'gi');
                friendly = friendly.replace(regex, userFriendly);
            }
            
            // Rule 5: Simplify AI-related messages
            const aiMessages = {
                'AI request failed': 'AI temporarily unavailable',
                'Retrying AI request': 'Reconnecting to AI',
                'AI Error': 'AI connection issue',
                'Received AI response': 'AI analysis complete',
                'Using cached AI response': 'Using previous analysis',
            };
            
            for (const [technical, userFriendly] of Object.entries(aiMessages)) {
                const regex = new RegExp(technical, 'gi');
                friendly = friendly.replace(regex, userFriendly);
            }
            
            // Rule 6: Simplify data operations
            const dataOps = {
                'Generating X-Ray data': 'Creating character insights',
                'Consolidating entities': 'Organizing information',
                'Merging data': 'Combining results',
                'Saving checkpoint': 'Saving progress',
            };
            
            for (const [technical, userFriendly] of Object.entries(dataOps)) {
                const regex = new RegExp(technical, 'gi');
                friendly = friendly.replace(regex, userFriendly);
            }
            
            // Rule 7: Remove technical jargon
            const jargonMap = {
                'WebSocket': 'Connection',
                'HTTP': 'Network',
                'JSON': 'data',
                'API': 'service',
                'endpoint': 'service',
                'Fatal error': 'Critical issue',
                'Process termination': 'Stopping',
            };
            
            for (const [jargon, simple] of Object.entries(jargonMap)) {
                const regex = new RegExp(jargon, 'gi');
                friendly = friendly.replace(regex, simple);
            }
            
            // Rule 8: Simplify units and abbreviations
            friendly = friendly
                .replace(/\\b(\\d+)\\/(\\d+)\\b/g, '$1 of $2')
                .replace(/\\bpct\\b/gi, '%')
                .replace(/\\bsecs?\\b/gi, 'seconds')
                .replace(/\\bmins?\\b/gi, 'minutes')
                .replace(/\\bchars?\\b/gi, 'characters');
            
            // Rule 9: Clean up brackets and technical markers
            friendly = friendly
                .replace(/\\[Chunk \\d+\\]/g, '')
                .replace(/\\[System\\]/g, '')
                .replace(/\\[Info\\]/g, '')
                .trim();
            
            // Rule 10: Context-specific enhancements
            if (context === 'success') {
                // Make success messages more celebratory
                if (!friendly.match(/[.!?]$/)) {
                    friendly += '!';
                }
                // Add encouraging words for major milestones
                if (friendly.includes('completed') || friendly.includes('finished')) {
                    friendly = '🎉 ' + friendly;
                }
            } else if (context === 'error') {
                // Make errors actionable
                if (!friendly.includes('Please') && !friendly.includes('Try') && 
                    !friendly.includes('check')) {
                    friendly += '. Check the logs for details';
                }
            } else if (context === 'warning') {
                // Add reassurance to warnings
                if (friendly.includes('retry') || friendly.includes('Reconnecting')) {
                    friendly += '...';
                }
            } else if (context === 'progress') {
                // Keep progress messages concise
                friendly = friendly.replace(/\\\\.+$/, '');
            }
            
            // Rule 11: Capitalize first letter if not already
            if (friendly.length > 0 && !friendly.match(/^[🎉✨💫]/)) {
                friendly = friendly.charAt(0).toUpperCase() + friendly.slice(1);
            }
            
            // Rule 12: Remove multiple spaces
            friendly = friendly.replace(/\\s+/g, ' ').trim();
            
            return friendly;
        }

        function showNotification(timestamp, message) {
            const container = document.getElementById('notificationContainer');
            
            // Categorize and translate message
            const result = categorizeMessage(message);
            
            // Skip if message was filtered out
            if (result === null) {
                return;
            }
            
            const { type, icon, friendlyMessage } = result;
            const notification = document.createElement('div');
            
            notification.className = `notification ${type}`;
            
            const formattedTime = typeof timestamp === 'string' && timestamp.includes('T') 
                ? new Date(timestamp).toLocaleTimeString() 
                : timestamp;
            
            notification.innerHTML = `
                <div class="notification-icon">${icon}</div>
                <div class="notification-content">
                    <div class="notification-time">${formattedTime}</div>
                    <div class="notification-message">${friendlyMessage}</div>
                </div>
            `;
            
            container.appendChild(notification);
            
            // Auto-dismiss after duration based on type
            const dismissTime = type === 'error' ? 5000 : type === 'warning' ? 4000 : 3000;
            
            setTimeout(() => {
                notification.classList.add('fade-out');
                setTimeout(() => {
                    if (notification.parentNode) {
                        notification.parentNode.removeChild(notification);
                    }
                }, 300); // Wait for fade-out animation
            }, dismissTime);
        }

        function addLog(timestamp, message) {
            // Add to modal log container
            const logContainer = document.getElementById('logContainer');
            const entry = document.createElement('div');
            entry.className = 'log-entry';

            const ts = document.createElement('span');
            ts.className = 'log-timestamp';
            ts.textContent = `[${typeof timestamp === 'string' && timestamp.includes('T') ? new Date(timestamp).toLocaleTimeString() : timestamp}]`;

            const msg = document.createElement('span');
            msg.textContent = message;

            entry.appendChild(ts);
            entry.appendChild(msg);
            logContainer.appendChild(entry);

            // Auto-scroll to bottom if modal is open
            if (document.getElementById('logsModal').classList.contains('active')) {
                logContainer.scrollTop = logContainer.scrollHeight;
            }

            // Limit log entries
            while (logContainer.children.length > 100) {
                logContainer.removeChild(logContainer.firstChild);
            }
            
            // Show notification bubble
            showNotification(timestamp, message);
        }
        
        function openLogsModal() {
            const modal = document.getElementById('logsModal');
            modal.classList.add('active');
            // Scroll to bottom when opening
            const logContainer = document.getElementById('logContainer');
            logContainer.scrollTop = logContainer.scrollHeight;
        }
        
        function closeLogsModal(event) {
            // Only close if clicking overlay or close button
            if (!event || event.target.id === 'logsModal') {
                document.getElementById('logsModal').classList.remove('active');
            }
        }
        
        // Close modal on Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                closeLogsModal();
            }
        });

        // Connect on page load
        connect();
    </script>
</body>
</html>
"""
