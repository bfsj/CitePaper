"""Optional local-only model server for an actual model-assisted smoke test.

Install: pip install -e '.[local]'
Run: python scripts/local_model_server.py --model /path/to/Qwen3-0.6B
This is a serial development adapter, NOT a high-throughput inference server.
"""

import argparse
import threading
import time

import torch
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field
from transformers import AutoModelForCausalLM, AutoTokenizer


class ChatRequest(BaseModel):
    model: str
    messages: list[dict[str, str]]
    max_tokens: int = Field(default=768, ge=1, le=1800)
    temperature: float = 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    torch.set_num_threads(4)
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    model = (
        AutoModelForCausalLM.from_pretrained(
            args.model,
            local_files_only=True,
            torch_dtype="auto",
        )
        .to(args.device)
        .eval()
    )
    lock = threading.Lock()
    app = FastAPI(title="CitePaper local model test adapter")

    @app.get("/health")
    def health():
        return {"status": "ok", "model": args.model, "device": args.device}

    @app.post("/v1/chat/completions")
    def chat(body: ChatRequest):
        prompt = tokenizer.apply_chat_template(
            body.messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(args.device)
        if inputs.input_ids.shape[1] > 12000:
            from fastapi import HTTPException

            raise HTTPException(422, "Prompt too large for this test adapter")
        start = time.perf_counter()
        with lock, torch.inference_mode():
            output = model.generate(
                **inputs,
                max_new_tokens=min(body.max_tokens, 1000),
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        answer = output[0, inputs.input_ids.shape[1] :]
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": tokenizer.decode(answer, skip_special_tokens=True),
                    }
                }
            ],
            "usage": {
                "prompt_tokens": inputs.input_ids.shape[1],
                "completion_tokens": len(answer),
                "total_tokens": inputs.input_ids.shape[1] + len(answer),
                "elapsed_seconds": round(time.perf_counter() - start, 3),
            },
        }

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
