"""本机 Qwen3-Reranker-4B HTTP 服务（端口 5071）。

设计：
- 模型 lazy load，第一次 /rerank 请求时加载（~7.5GB MPS 显存）
- POST /rerank  {query: str, docs: [str], instruction?: str} → {scores: [float]}
- 同时启的服务也支持 batch；query 一般是 event 的 title+description，docs 是若干 source 的 title+content
- 给 cognihub（macmini）通过 tailscale 调用：http://<本机 tailscale ip>:5071/rerank

启动：
    /Users/zhang.longqiang/PycharmProjects/PythonProject/.venv/bin/python tools/reranker_service.py

参考实现来自 ~/PycharmProjects/PythonProject/memory_test_tool/app.py 的 Qwen3Reranker 类。
"""
from __future__ import annotations

import argparse
import logging
import threading
import time
from typing import Optional

import torch
from flask import Flask, jsonify, request
from transformers import AutoModelForCausalLM, AutoTokenizer


MODEL_PATH = "/Users/zhang.longqiang/.cache/modelscope/hub/models/Qwen/Qwen3-Reranker-4B"
DEFAULT_INSTRUCTION = (
    "Given a web search query, retrieve relevant passages that answer the query"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reranker")


class Qwen3Reranker:
    def __init__(self, model_path: str):
        if torch.backends.mps.is_available():
            self.device = torch.device("mps")
        elif torch.cuda.is_available():
            self.device = torch.device("cuda")
        else:
            self.device = torch.device("cpu")
        logger.info("loading model from %s on %s", model_path, self.device)
        t0 = time.time()
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, padding_side="left")
        self.model = (
            AutoModelForCausalLM.from_pretrained(
                model_path,
                torch_dtype=torch.float16 if self.device.type != "cpu" else torch.float32,
            )
            .to(self.device)
            .eval()
        )
        logger.info("model loaded in %.1fs", time.time() - t0)

        self.token_false_id = self.tokenizer.convert_tokens_to_ids("no")
        self.token_true_id = self.tokenizer.convert_tokens_to_ids("yes")
        self.max_length = 8192

        prefix = (
            "<|im_start|>system\nJudge whether the Document meets the requirements "
            "based on the Query and the Instruct provided. Note that the answer "
            "can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n"
        )
        suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        self.prefix_tokens = self.tokenizer.encode(prefix, add_special_tokens=False)
        self.suffix_tokens = self.tokenizer.encode(suffix, add_special_tokens=False)

    def format_instruction(self, query: str, doc: str, instruction: str) -> str:
        return f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {doc}"

    def _process_inputs(self, pairs: list[str]):
        inputs = self.tokenizer(
            pairs,
            padding=False,
            truncation="longest_first",
            return_attention_mask=False,
            max_length=self.max_length - len(self.prefix_tokens) - len(self.suffix_tokens),
        )
        for i, ele in enumerate(inputs["input_ids"]):
            inputs["input_ids"][i] = self.prefix_tokens + ele + self.suffix_tokens
        inputs = self.tokenizer.pad(inputs, padding=True, return_tensors="pt", max_length=self.max_length)
        return {k: v.to(self.device) for k, v in inputs.items()}

    @torch.no_grad()
    def _compute(self, inputs) -> list[float]:
        batch_scores = self.model(**inputs).logits[:, -1, :]
        true_vec = batch_scores[:, self.token_true_id]
        false_vec = batch_scores[:, self.token_false_id]
        stacked = torch.stack([false_vec, true_vec], dim=1)
        log_softmax = torch.nn.functional.log_softmax(stacked, dim=1)
        return log_softmax[:, 1].exp().tolist()

    def predict(
        self,
        query: str,
        docs: list[str],
        instruction: Optional[str] = None,
        batch_size: int = 8,
    ) -> list[float]:
        instr = instruction or DEFAULT_INSTRUCTION
        scores: list[float] = []
        for i in range(0, len(docs), batch_size):
            batch = docs[i : i + batch_size]
            pairs = [self.format_instruction(query, d, instr) for d in batch]
            inputs = self._process_inputs(pairs)
            scores.extend(self._compute(inputs))
        return scores


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
_model_lock = threading.Lock()
_model: Optional[Qwen3Reranker] = None
_model_path = MODEL_PATH


def get_model() -> Qwen3Reranker:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = Qwen3Reranker(_model_path)
    return _model


# 推理一次只能一个 request 跑（GPU 内存有限）
_inference_lock = threading.Lock()

app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "model_loaded": _model is not None,
        "model_path": _model_path,
    })


@app.post("/rerank")
def rerank():
    body = request.get_json(force=True, silent=True) or {}
    query = body.get("query")
    docs = body.get("docs")
    instruction = body.get("instruction")
    batch_size = int(body.get("batch_size", 8))

    if not isinstance(query, str) or not query.strip():
        return jsonify({"error": "query (non-empty string) required"}), 400
    if not isinstance(docs, list) or not docs:
        return jsonify({"error": "docs (non-empty list) required"}), 400
    if not all(isinstance(d, str) for d in docs):
        return jsonify({"error": "docs must be list[str]"}), 400

    t0 = time.time()
    model = get_model()
    with _inference_lock:
        scores = model.predict(query, docs, instruction=instruction, batch_size=batch_size)
    elapsed = time.time() - t0
    logger.info("rerank: %d docs in %.2fs (%.0fms/doc)", len(docs), elapsed, elapsed * 1000 / max(len(docs), 1))
    return jsonify({
        "scores": scores,
        "elapsed_ms": int(elapsed * 1000),
        "doc_count": len(docs),
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5071)
    parser.add_argument("--preload", action="store_true",
                        help="启动时立即加载模型（默认 lazy load）")
    parser.add_argument("--model-path", default=MODEL_PATH)
    args = parser.parse_args()

    global _model_path
    _model_path = args.model_path

    if args.preload:
        get_model()

    logger.info("listening on http://%s:%d", args.host, args.port)
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
