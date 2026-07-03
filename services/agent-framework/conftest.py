"""
conftest.py — pytest configuration for the Agent Framework service.

Adds the service root (services/agent-framework/) to sys.path so that
`import agent_framework` resolves to the agent_framework/ package directory.

Also adds the workspace root (two levels up) to sys.path so that `shared` and
other platform-level modules are importable during test runs.
"""
import sys
import os

# Ensure the service root is on the path so `agent_framework` is importable.
service_root = os.path.dirname(os.path.abspath(__file__))
if service_root not in sys.path:
    sys.path.insert(0, service_root)

# Ensure the workspace root is on the path so `shared` is importable.
workspace_root = os.path.abspath(os.path.join(service_root, "..", ".."))
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)
