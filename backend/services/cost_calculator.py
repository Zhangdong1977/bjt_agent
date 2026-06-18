"""费用预估（预估口径，非精确计费）。

单价集中在本文件（唯一来源），运营台只原值透传、不二次计算，避免两处价目漂移。
调价只改这里。单位：元 / 单位；LLM 按百万 token，OCR 按次。
价格来源：各厂商公开价目表（标注日期，便于核对更新）。

本期为"按公开价目表的预估值"，落到 ai_usage_records.cost_cny；
仅 status=success 的记录累计算钱（error/timeout 行 caller 传 status!=success，
本函数直接返回 None）。
"""

from typing import Optional

# —— DeepSeek（按百万 token）—— 来源 deepseek.com 公开价目表
_DEEPSEEK = {
    "deepseek-chat":     {"input": 2.0 / 1_000_000, "output": 8.0 / 1_000_000},
    "deepseek-reasoner": {"input": 4.0 / 1_000_000, "output": 16.0 / 1_000_000},
    # 新模型兜底价（deepseek-v4-flash 等未明示的，按 chat 档粗估）
    "__default__":       {"input": 2.0 / 1_000_000, "output": 8.0 / 1_000_000},
}

# —— MiniMax（按百万 token）——
_MINIMAX = {
    "MiniMax-M2.7-highspeed": {"input": 1.0 / 1_000_000, "output": 4.0 / 1_000_000},
    "__default__":            {"input": 1.0 / 1_000_000, "output": 4.0 / 1_000_000},
}

# —— Volcengine / 火山（按百万 token）—— doubao-seed 系列
_VOLCENGINE = {
    "doubao-seed-2-0-pro-260215": {"input": 4.0 / 1_000_000, "output": 16.0 / 1_000_000},
    "__default__":                {"input": 4.0 / 1_000_000, "output": 16.0 / 1_000_000},
}

# —— LLM provider → 价目表映射 ——
_LLM_RATES = {
    "deepseek": _DEEPSEEK,
    "minimax": _MINIMAX,
    "volcengine": _VOLCENGINE,
}

# —— OCR（按次）—— accurate_basic 公开价
_OCR_PER_CALL = {
    "baidu_ocr": 0.015,
}


def estimate_cost(
    *,
    provider: str,
    model: Optional[str] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    status: str,
    **_,
) -> Optional[float]:
    """预估单次调用费用（元）。仅 success 返回数值，否则 None。"""
    if status != "success":
        return None

    # LLM
    rates = _LLM_RATES.get(provider)
    if rates is not None:
        rate = rates.get(model) or rates["__default__"]
        return round(rate["input"] * prompt_tokens + rate["output"] * completion_tokens, 6)

    # OCR
    if provider in _OCR_PER_CALL:
        return round(_OCR_PER_CALL[provider], 6)

    return None
