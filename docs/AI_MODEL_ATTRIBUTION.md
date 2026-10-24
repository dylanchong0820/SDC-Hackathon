# External AI model attribution

Quote Bot AI uses an externally trained model hosted on RunPod. The team did not
create or train its weights.

- **Model:** Qwen/Qwen3-VL-4B-Instruct
- **Author:** Qwen / Alibaba Cloud
- **Declared licence:** Apache License 2.0
- **Official model card:** https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct
- **Inference engine:** vLLM, Apache License 2.0
- **Inference engine source:** https://github.com/vllm-project/vllm

This model is used for text and image quotations. Audio support is not implied.
Model weights and the vLLM runtime are hosted separately and are not included in
this application repository. Preserve their upstream licence and attribution
requirements if redistributing either component.

The previously tested Qwen2.5-VL-3B-Instruct uses a different, research-only
licence. It must not be substituted for the model above for unrestricted
commercial use.
