---
name: headless-ui-gating-checkpoint-fallback
description: Headless UI Gating with Checkpoint-Absent Auto-Fallback for Interactive Entry Scripts
tags: ["torch-npu", "gradio", "headless-validation", "entry-script", "checkpoint-fallback", "interactive-ui", "huggingface", "migration-validation"]
category: code_adaptation
subtype: interactive_ui_headless_gating
confidence: 0.75
occurrence_count: 1
---

# Headless UI Gating with Checkpoint-Absent Auto-Fallback for Interactive Entry Scripts

## When to Use
- Entry script with interactive UI (gradio.Blocks() / demo.launch()) blocks indefinitely during automated migration pipeline execution. Additionally, scripts that hardcode a model checkpoint path (e.g., --model_path='ckpt/') crash with HFValidationError when the checkpoint directory does not exist because large model weights (7B-14B) were never downloaded to the validation environment.

## Root Cause
Two interacting failure modes: (1) Interactive UI frameworks like Gradio launch an event loop via demo.launch() that never returns when invoked without a human user, causing the migration pipeline to hang until timeout. (2) Entry scripts hardcode a default checkpoint path that does not exist in the migration validation environment — model weights are too large to download automatically (e.g., SpeechGPT-2.0-preview-7B at ~14GB). The combination prevents exit_code=0 validation of NPU device readiness and project import correctness.

## How to Use
1. Identify interactive UI patterns in the entry script: grep for gradio.Blocks(), demo.launch(), gr.Interface(), streamlit.run(), or equivalent blocking UI launch calls.
2. Add a --headless CLI argument (argparse: add_argument('--headless', action='store_true')) to the entry script's argument parser.
3. Wrap existing Gradio/Streamlit UI construction (gr.Blocks(), demo.launch()) in a conditional that only executes when args.headless is False AND the checkpoint is present.
4. Add checkpoint-absent auto-fallback: before constructing the UI, check if os.path.isdir(args.model_path) returns False. If it does, print a warning and force headless mode regardless of --headless flag.
5. Implement a headless validation block that executes when headless mode is active. This block must: (a) check torch_npu.npu.device_count() and print the result, (b) create an NPU tensor via torch.randn(2, 3).npu() to verify device operability, (c) import core project modules (mimo_qwen2_grouped, Codec.models.codec, etc.) to verify import integrity, (d) call sys.exit(0) after all checks pass.
6. Verify the modified entry script produces exit_code=0 in the migration environment without requiring model weights. Confirm stdout contains NPU device count and tensor creation success messages.
7. If the project already has a dedicated smoke_test.py or similar headless validation script, prefer redirecting run_command to that script instead of modifying the interactive UI entry point — smoke_test.py may already handle missing checkpoints gracefully.

## Code Examples
[
  {
    "file": "demo_gradio.py",
    "before": "parser = argparse.ArgumentParser()\nparser.add_argument('--model_path', type=str, default='ckpt/')\nargs = parser.parse_args()\n# ... later in main ...\ndemo = gr.Blocks()\n# ... UI component construction ...\ndemo.launch()",
    "after": "parser = argparse.ArgumentParser()\nparser.add_argument('--headless', action='store_true', help='Run headless NPU validation without launching the web UI')\nparser.add_argument('--model_path', type=str, default='ckpt/')\nargs = parser.parse_args()\n\n# Auto-fallback: if checkpoint directory is missing, force headless mode\nif not os.path.isdir(args.model_path):\n    print(f'Warning: Model path \"{args.model_path}\" not found. Running headless validation.')\n    args.headless = True\n\nif args.headless:\n    # Headless NPU validation block\n    print(f'NPU devices available: {torch_npu.npu.device_count()}')\n    x = torch.randn(2, 3).npu()\n    print(f'NPU tensor creation OK: shape={x.shape}')\n    # Validate core project imports\n    from mimo_qwen2_grouped import *\n    from Codec.models.codec import Generator as SpeechGPT2Tokenizer\n    print('All core imports validated successfully.')\n    sys.exit(0)\n\n# Only reached if headless=False AND checkpoint exists\ndemo = gr.Blocks()\n# ... UI component construction ...\ndemo.launch()"
  }
]

## Do Not
- Do NOT attempt to download model checkpoints in the migration validation environment — they are typically 7B-14B parameters and will cause OOM or timeout.
- Do NOT wrap demo.launch() in a subprocess with timeout as a workaround — this masks import errors and device detection failures that the validation should catch.
- Do NOT skip NPU tensor creation in the headless validation block — torch.npu.is_available() alone is insufficient; a tensor allocation confirms device memory is usable.
- Do NOT apply headless gating to scripts that are NOT interactive UI entry points — only target scripts containing gr.Blocks(), demo.launch(), or equivalent blocking UI calls.

## Evidence
- Source runs: e2e-v3-be9f843e86a0
