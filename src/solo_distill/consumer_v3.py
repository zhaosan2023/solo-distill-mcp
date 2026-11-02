"""显式 V3 生产消费入口；不改变旧消费者或主线 B 语义。

从调用方完整可信源码、包外核心清单、冻结 POST、原始生成对象及
外部失败记录认证发布。传输通过不证明实际网络发送、选择独立性、
人工语义或宿主加载；本模块不联网、不执行来源、不读写磁盘。
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from .core_v3 import SealedCore
from .macro_v3 import canonical
from .snapshot_v2 import snapshot_scope
from .wire_v3 import _reassemble_report, build_publication


@dataclass(frozen=True)
class ConsumptionResult:
    publication_bytes: bytes
    decoded_bytes: int
    transfer_bytes: int
    physical_frames: int
    notification_chunks: int

    @property
    def publication(self):
        """每次返回独立视图，不能通过修改视图改变已认证结果。"""
        return json.loads(self.publication_bytes.decode("utf-8"))

    @property
    def status(self):
        return self.publication["status"]


@snapshot_scope
def consume_frame(raw, raw_files, *, admission, candidate, observations=None, **trusted):
    """先认证有界传输，再与包外生成对象及完整可信来源对账。

    observations 的期望由调用方提供，未提供时为无观察值的空对象；
    不接受收到的观察值作为自身期望。失败列表同样由 trusted 提供，
    不从任务证据回填。任务状态不会被结构核心的 complete 覆盖。
    """
    tick = trusted.get("tick")
    tick = tick if tick is not None else (lambda: None)
    tick()
    # 复用同一调用内已经完整解码并核对传输绑定的对象；下方仍从
    # 包外可信输入独立认证全部发布，不跨调用保存任何认证结果。
    decoded, manifest, report = _reassemble_report(raw, admission, tick=tick)
    core = SealedCore(canonical(report["core"]))
    expected = build_publication(core, report["task_evidence"], raw_files,
        candidate=candidate, observations=observations, **trusted)
    expected_bytes = canonical(expected)
    if canonical(report) != expected_bytes:
        raise ValueError("v3_consumption_publication_or_trusted_input_conflict")
    tick()
    return ConsumptionResult(
        publication_bytes=expected_bytes,
        decoded_bytes=len(decoded),
        transfer_bytes=len(raw),
        physical_frames=raw.count(b"\n"),
        notification_chunks=0 if manifest is None else manifest["manifest"]["count"],
    )
