"""
conftest.py — pytest configuration for the Agent Framework service.

Adds the service root (services/agent-framework/) to sys.path so that
`import agent_framework` resolves to the agent_framework/ package directory.
"""
import sys
import os

# Ensure the service root is on the path so `agent_framework` is importable.
service_root = os.path.dirname(os.path.abspath(__file__))
if service_root not in sys.path:
    sys.path.insert(0, service_root)
