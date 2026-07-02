"""
API Gateway — Layer 1 (POC)

Single ingress point for all LLM traffic from enterprise consumer applications.
Accepts OpenAI-compatible HTTP requests, authenticates via static API key,
enforces in-memory sliding-window rate limiting, normalizes payloads into the
Internal Message Format (IMF), and forwards them to the Security Layer.
"""
