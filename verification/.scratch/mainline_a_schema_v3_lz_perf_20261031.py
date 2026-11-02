"""获准耗时修剪的有界内存诊断；不占用真实三样本或在线接纳。
问题：生产 LZ 的索引、复制和期限回调成本能否降低而保持令牌字节？
范围：历史失败 trace 的只读元数据与固定内存样本；不执行/修改来源，
不联网、不启动服务、不删除失败现场。环境：现有标准库与绑定契约。
验收：真实退出、同一固定样本摘要、编解码等值和分段计时；诊断不是
真实冷链通过证明。内部 27 秒、外部 30 秒；结果最多 8192 字节。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import threading
import time

from solo_distill import lz_v3

TRACE = Path("verification/.scratch/mainline-a-schema-v3-20261030T095713Z-preflight-1-lz-v3-1-20261031T1047Z.full.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, choices=("before", "after"))
    args = parser.parse_args()
    started = time.monotonic()
    deadline = started + 27
    cancelled = threading.Event()
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if cancelled.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("lz_perf_deadline")
        # 模拟真实客户端/准备链的嵌套期限核验，不改变生产行为。
        if cancelled.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("lz_perf_deadline")

    check()
    trace = json.loads(TRACE.read_bytes())
    metadata = {"keys": sorted(trace), "preparation_metrics": trace.get("metrics"),
                "gateway_request_count": len(trace.get("gateway_requests", []))}
    # 固定且可复算的内存样本；正文、Unicode、重复记录与非重复身份并存。
    value = {"records": [{"id": str(index), "body": "abcdef中文\n" * 12,
                           "signature": "def example(value):", "unknowns": ["未证明"]}
                          for index in range(1600)]}
    raw = lz_v3.canonical(value)
    begin = time.monotonic()
    tokens = lz_v3._compress(raw, tick=check)
    compress_seconds = time.monotonic() - begin
    begin = time.monotonic()
    restored = lz_v3._inflate(tokens, len(raw), tick=check)
    inflate_seconds = time.monotonic() - begin
    if restored != raw:
        raise AssertionError("lz_perf_bytes_conflict")
    check()
    result = {"diagnostic_only": True, "label": args.label,
              "raw_bytes": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest(),
              "token_bytes": len(tokens), "token_sha256": hashlib.sha256(tokens).hexdigest(),
              "compress_seconds": compress_seconds, "inflate_seconds": inflate_seconds,
              "tick_calls": calls, "elapsed_seconds": time.monotonic() - started,
              "trace_metadata": metadata, "preflight_acceptance": "not_run"}
    output = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(output.encode("utf-8")) > 8192:
        raise ValueError("lz_perf_output_budget")
    print(output, flush=True)
    check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
