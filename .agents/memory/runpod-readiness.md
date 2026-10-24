---
name: RunPod load-balancer readiness
description: External worker routing and model capabilities must be verified independently of dashboard readiness.
---

Treat the RunPod dashboard's endpoint “Ready” label as insufficient evidence that inference works. Verify the worker's configuration version, actual HTTP serving port, health route, model discovery, and a synthetic structured extraction.

**Why:** A healthy-looking endpoint can have a stale worker or route to the wrong port/path. The stock vLLM image serves its health check at `/health`, not `/`; successful model loading alone does not establish that requests are routable.

**How to apply:** Check the external deployment configuration rather than trying to fix routing failures by extending application timeouts. Environment/port changes require worker replacement; verify the replacement is not marked stale before declaring success.

Verify modality support independently of text inference.

**Why:** The original stock vLLM deployment defaulted to a text-only model. The user explicitly requires quotation images as well as text; an OpenAI-compatible route alone does not imply those capabilities. Voice is a separate requirement, not implied by image support.

**How to apply:** Never promise image or voice support based only on a successful text test. Require format-specific synthetic checks after any model change, and get approval before enabling always-on GPU workers or changing the paid compute configuration.

Keep a commercially permitted image-capable model for this endpoint rather than reverting to the container's default text model. Qwen/Qwen3-VL-4B-Instruct was selected for the existing 24 GB GPU; the user approved 20 GB of container disk for weights/cache, not an always-on worker or a larger GPU.

**Why:** This preserves the user's requested image support without treating the model-change approval as permission for broader ongoing compute charges.

**How to apply:** Preserve scale-to-zero and existing GPU selection unless separately approved. This vision model does not support audio; retain explicit format errors instead of implying that voice analysis is ready.

Do not describe Qwen/Qwen2.5-VL-3B-Instruct as Apache-licensed or suitable for unrestricted commercial use.

**Why:** Its upstream model card and LICENSE were checked directly on 2026-10-25: this particular size uses the Qwen Research License, permitting research/evaluation only and requiring a separate licence for commercial use. The repository's Apache licence does not cover these external model weights.

**How to apply:** Flag this restriction in competition submissions and before commercial deployment. Seek an appropriately licensed replacement or separate permission rather than assuming all models in the Qwen family share a licence. See https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct/blob/main/LICENSE for the authoritative terms.

Synthetic vision acceptance checks must include explicit currency and the absence of invented calendar dates, not only numeric prices and successful HTTP responses.

**Why:** A smaller Apache-licensed replacement read prices but omitted MYR and invented a delivery date that was not on the image. A successful image request was not sufficient for quotation comparison.

**How to apply:** Check company, currency, price, delivery duration, and null dates across supported image formats before accepting a replacement model. The current model's upstream card declares Apache-2.0: https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct.
