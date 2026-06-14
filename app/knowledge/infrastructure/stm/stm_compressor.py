"""短期记忆消息压缩工具。

这个模块负责：
- 把 `MessageRecord` 编码成适合存入 Redis 的紧凑字节串
- 按消息体积选择不同的 Zstd 压缩等级，平衡 CPU 和存储
- 提供与 Redis 实现解耦的纯压缩接口，便于独立测试

这个模块不负责：
- Redis Key 管理
- 会话窗口裁剪
- 摘要压缩策略
"""

import msgpack
import zstandard as zstd

from app.knowledge.domain.schemas import MessageRecord

# 多级压缩器：按消息大小选择压缩级别，平衡 CPU 和存储。
_zstd_fast = zstd.ZstdCompressor(level=1)  # 2-4KB，中型消息优先速度
_zstd_normal = zstd.ZstdCompressor(level=3)  # 4-16KB，默认平衡点
_zstd_high = zstd.ZstdCompressor(level=9)  # >16KB，优先压缩比
_zstd_decompressor = zstd.ZstdDecompressor()  # 解压端不需要感知压缩等级


def compress_message(message: MessageRecord) -> bytes:
    """MsgPack + 多级 Zstd 压缩。

    ≤2KB   → 仅 MsgPack（\x00 前缀）
    2-4KB  → MsgPack + Zstd level=1（\x01 前缀，快速）
    4-16KB → MsgPack + Zstd level=3（\x02 前缀，平衡）
    >16KB  → MsgPack + Zstd level=9（\x03 前缀，高压缩比）
    """
    packed = msgpack.packb(message.model_dump(), use_bin_type=True)

    if len(packed) <= 2048:
        return b"\x00" + packed
    if len(packed) <= 4096:
        return b"\x01" + _zstd_fast.compress(packed)
    if len(packed) <= 16384:
        return b"\x02" + _zstd_normal.compress(packed)
    return b"\x03" + _zstd_high.compress(packed)


def decompress_message(data: bytes) -> MessageRecord:
    """解压：解压器不关心压缩级别，flag >= 0x01 均走 zstd 解压。"""
    flag, payload = data[0], data[1:]
    if flag in (0x01, 0x02, 0x03):
        unpacked = msgpack.unpackb(_zstd_decompressor.decompress(payload), raw=False)
    else:
        unpacked = msgpack.unpackb(payload, raw=False)
    return MessageRecord(**unpacked)
