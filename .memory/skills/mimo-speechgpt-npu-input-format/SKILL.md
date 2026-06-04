# MIMO SpeechGPT NPU Input Format

**Promoted from**: SpeechGPT-2.0-preview migration (run SpeechGPT-2.0-preview_20260604_161651)

## Problem

SpeechGPT-2.0-preview uses a MIMO (Multi-Input Multi-Output) architecture with
interleaved text and audio-codec tokens. When migrating to NPU, passing raw text
to the model causes:

1. `RuntimeError: shape '[1, -1, 12]' is invalid` — input not divisible by
   `(n_vq+1) * group_size = 12`
2. Model generates only `<|empty|>` tokens (ID 151650) — missing chat template

## Solution

### 1. Construct MIMO Interleaved Input

```python
# Tokenize with chat template
formatted_prompt = f"[|Human|]: {prompt} ###\n[|SpeechGPT|]: "
t = tokenizer(formatted_prompt, add_special_tokens=False).input_ids

# Insert group_size-1 filler tokens between every text token
t = HeadlessInference._insert_between(t, group_size - 1, value=-100)

# Build audio placeholder channels (3 layers) filled with zeroemb_idx
audio_part = torch.full((3, t.shape[1]), zeroemb_idx, dtype=torch.int)

# Interleave: [1, seq] + [3, seq] -> flatten to [1, seq * 4]
input_ids = torch.cat([t, audio_part], dim=0).T.reshape(1, -1)
```

### 2. Derive zeroemb_idx

`MIMOModelArguments` has no `zeroemb_idx` attribute. Derive from vocabulary:

```python
zeroemb_idx = model_args.speech_vocab_size - 1  # = 1024 for SpeechGPT-2.0-preview
```

### 3. Output Decoding

After generation, reshape and extract text tokens:

```python
generated_ids = generated_ids.int().cpu().reshape(-1, audio_channels + 1).T[:, prompt_len:]
text_tokens = generated_ids[0, :: group_size][:-1]  # channel 0, every group_size-th, drop trailing
result = tokenizer.decode(text_tokens, skip_special_tokens=True)
```

### 4. Model Constructor Arguments

`MIMOLlamaForCausalLM.from_pretrained()` requires explicit kwargs:

```python
model = MIMOLlamaForCausalLM.from_pretrained(
    model_path,
    padding_idx=tokenizer.pad_token_id,  # from tokenizer
    sosp_idx=tokenizer.convert_tokens_to_ids("<|sosp|>"),   # speech start
    eosp_idx=tokenizer.convert_tokens_to_ids("<|eosp|>"),   # speech end
    args=model_args,                                        # MIMOModelArguments instance
    attn_implementation="sdpa",
    torch_dtype=torch.bfloat16,
    device_map="npu",
)
```

## Dependencies

- HuggingFace `transformers` (GenerationConfig)
- PyTorch with `torch.npu` backend
- SpeechGPT-specific: `MIMOLlamaForCausalLM`, `MIMOModelArguments`, `Generator` (codec)

## Verification

Run text-to-text inference with at least 2 test prompts:
```bash
ASCEND_RT_VISIBLE_DEVICES=2 python run_inference.py --mode text \
    --model_path SpeechGPT-2.0-preview-7B/ \
    --prompt "Hello, how are you?"
```

Expected: Coherent Chinese response (model is Chinese-speech-trained).
No `<|empty|>` tokens in output.
No reshape errors.

## Notes

- `text_auxiliary_loss_inference_mode` should remain `False` for text-only mode
- Model responds in Chinese regardless of input language (training data is Chinese speech)
- `group_size` typically = 3, `n_vq` = 3 → (n_vq+1)*group_size = 12
- Attention mask warning ("pad token is same as eos") is benign
