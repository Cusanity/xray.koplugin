#!/usr/bin/env python3
"""
Test script to verify web monitor integration works correctly.
This simulates the generator updating progress without running the full analysis.
"""

import time
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Test import and get custom print
try:
    from xray_web_monitor import (
        update_progress_state,
        add_log_entry,
        increment_ai_retry_count,
        increment_ai_success_count,
    )

    # Import the custom print that sends to web monitor
    from xray_generator import print as monitored_print

    print = monitored_print  # Use monitored print for all output
    print("✓ Web monitor imports successful")
except ImportError as e:
    import builtins

    print = builtins.print
    print(f"✗ Failed to import web monitor: {e}")
    sys.exit(1)

# Start web monitor
try:
    import uvicorn
    import threading
    from xray_web_monitor import app

    def run_web_monitor():
        uvicorn.run(app, host="0.0.0.0", port=8765, log_level="error")

    web_monitor_thread = threading.Thread(target=run_web_monitor, daemon=True)
    web_monitor_thread.start()

    # Get local IP for easier mobile access
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "localhost"

    print("✓ Web monitor started")
    print("\n" + "=" * 70)
    print("Open the web monitor in your browser:")
    print(f"  Local:   http://localhost:8765")
    print(f"  Network: http://{local_ip}:8765  (use this on your phone)")
    print("=" * 70 + "\n")

    # Give server time to start
    time.sleep(2)

except ImportError:
    print("✗ FastAPI/uvicorn not installed. Install with: pip install fastapi uvicorn")
    sys.exit(1)

# Simulate progress updates
print("Starting simulated progress updates...\n")

try:
    # Initial state
    update_progress_state(
        status="running",
        current_book="Test Book",
        progress_pct=0,
        current_chunk=0,
        total_chunks=10,
        current_operation="initializing",
    )
    add_log_entry("Starting test simulation")
    print("✓ Initial state updated")
    time.sleep(1)

    # Simulate processing chunks
    messages = [
        ("Extracting text from EPUB", "info"),
        ("Parsing EPUB structure", "info"),
        ("Processing chunk 1/10", "info"),
        ("AI request failed - retrying", "warning"),
        ("Retrying AI request (attempt 2)", "warning"),
        ("Successfully generated character data", "success"),
        ("Processing chunk 2/10", "info"),
        ("Analyzing section 3", "info"),
        ("Consolidating entities", "info"),
        ("Saved X-Ray data to file", "success"),
    ]

    for i in range(1, 11):
        pct = i * 10

        print(f"\n=== Processing Chunk {i}/10 ===")

        # Use varied messages
        if i <= len(messages):
            msg, msg_type = messages[i - 1]
            print(f"  {msg}")
        else:
            print(f"  Processing chunk {i}/10")

        time.sleep(0.5)

        # Simulate AI retry failures on chunks 3 and 7
        if i in (3, 7):
            print(f"  ⚠️ AI request failed - retrying...")
            add_log_entry(f"AI request failed for chunk {i}", "WARNING")
            increment_ai_retry_count()
            time.sleep(1)
            print(f"  ✅ Retry successful")
            add_log_entry(f"AI retry successful for chunk {i}", "INFO")
            increment_ai_success_count()

        # Simulate error on chunk 5
        if i == 5:
            print(f"  ❌ Fatal error reading EPUB section")
            add_log_entry("Fatal error: Unable to parse EPUB section", "ERROR")
            time.sleep(1)
            print(f"  Recovering from error...")
            add_log_entry("Recovered from error, continuing", "INFO")

        print(f"  ✅ Completed chunk {i}")
        increment_ai_success_count()

        update_progress_state(
            progress_pct=pct,
            current_chunk=i,
            current_operation=f"processing chunk {i}",
            stats={
                "characters": i * 5,
                "locations": i * 2,
                "themes": i,
                "events": i * 3,
            },
        )

        print(f"  [Merged] Chars: {i * 5}, Locs: {i * 2}, Events: {i * 3}")
        print(f"  [Consolidation] Processing pending items...")
        print(f"  Saved checkpoint at {pct}%")

        add_log_entry(f"Completed chunk {i}/10 ({pct}%)", "INFO")
        time.sleep(1.5)

    # Completion
    update_progress_state(
        status="completed", progress_pct=100, current_operation="completed"
    )
    add_log_entry("Test simulation completed successfully")
    print("\n✓ Simulation completed")

    print("\n" + "=" * 70)
    print("Test successful! Check the web monitor to verify updates appeared.")
    print("Press Ctrl+C to exit...")
    print("=" * 70 + "\n")

    # Keep running to allow viewing results
    while True:
        time.sleep(1)

except KeyboardInterrupt:
    print("\n\nTest interrupted by user")
except Exception as e:
    print(f"\n✗ Error during simulation: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)
