---
name: RunPod load-balancer readiness
description: External worker routing and model capabilities must be verified independently of dashboard readiness.
---

Treat the RunPod dashboard's endpoint “Ready” label as insufficient evidence that inference works. Verify the worker's configuration version, actual HTTP serving port, health route, model discovery, and a synthetic structured extraction.

**Why:** A healthy-looking endpoint can have a stale worker or route to the wrong port/path. The stock vLLM image serves its health check at `/health`, not `/`; successful model loading alone does not establish that requests are routable.

**How to apply:** Check the external deployment configuration rather than trying to fix routing failures by extending application timeouts. Environment/port changes require worker replacement; verify the replacement is not marked stale before declaring success.

Verify modality support independently of text inference.

**Why:** The existing stock vLLM deployment defaulted to Qwen3-0.6B, which successfully extracted synthetic text quotation facts but is not an image/audio model. An OpenAI-compatible route does not imply multimodal capabilities.

**How to apply:** Never promise image or voice support based only on a successful text test. Require format-specific synthetic checks after any model change, and get approval before enabling always-on GPU workers or changing the paid compute configuration.
