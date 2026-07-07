"""费用预估（按各厂商公开价目表的预估值）。

单价集中在本文件（唯一来源），运营台只原值透传、不二次计算，避免两处价目漂移。
调价只改这里。单位：元 / 单位；LLM 按百万 token，OCR 按次。
价格来源：各厂商公开价目表（标注日期，便于核对更新）。

本期为"按公开价目表的预估值"，落到 ai_usage_records.cost_cny；
仅 status=success 的记录累计算钱（error/timeout 行 caller 传 status!=success，
本函数直接返回 None）。

DeepSeek 上下文缓存拆分计价：命中输入是未命中输入的 1/50，必须分开计；
其它厂商暂无缓存拆分，hit=0、miss=prompt_tokens 兜底（见 _llm_cost）。
"""

from typing import Optional

# —— DeepSeek（按百万 token）—— 来源 api-docs.deepseek.com/zh-cn 公开价目表（2026-06 核对）
#    三档：缓存命中输入 hit / 缓存未命中输入 miss / 输出 output
#    deepseek-v4-flash：命中 0.02 元、未命中 1 元、输出 2 元（每百万 token）
_DEEPSEEK = {
    "deepseek-v4-flash": {"hit": 0.02 / 1_000_000, "miss": 1.0 / 1_000_000, "output": 2.0 / 1_000_000},
    # 兜底：当前默认 provider 即 v4-flash，且 deepseek-chat/reasoner 已宣布 2026/07 弃用
    # 并映射到 v4-flash，故兜底价直接对齐 v4-flash。
    "__default__":       {"hit": 0.02 / 1_000_000, "miss": 1.0 / 1_000_000, "output": 2.0 / 1_000_000},
}

# —— MiniMax（按百万 token）—— 无缓存拆分
_MINIMAX = {
    "MiniMax-M2.7-highspeed": {"hit": 0.0, "miss": 1.0 / 1_000_000, "output": 4.0 / 1_000_000},
    "__default__":            {"hit": 0.0, "miss": 1.0 / 1_000_000, "output": 4.0 / 1_000_000},
}

# —— Volcengine / 火山（按百万 token）—— doubao-seed 系列，无缓存拆分
_VOLCENGINE = {
    "doubao-seed-2-0-pro-260215": {"hit": 0.0, "miss": 4.0 / 1_000_000, "output": 16.0 / 1_000_000},
    "__default__":                {"hit": 0.0, "miss": 4.0 / 1_000_000, "output": 16.0 / 1_000_000},
}

# —— LLM provider → 价目表映射 ——
_LLM_RATES = {
    "deepseek": _DEEPSEEK,
    "minimax": _MINIMAX,
    "volcengine": _VOLCENGINE,
}

# —— OCR（按次）—— 百度 accurate_basic，按内部约定结转单价（非厂商公开价）
_OCR_PER_CALL = {
    "baidu_ocr": 0.028,
}


def _llm_cost(
    rates: dict,
    model: Optional[str],
    *,
    prompt_tokens: int,
    completion_tokens: int,
    prompt_cache_hit_tokens: int = 0,
    prompt_cache_miss_tokens: int = 0,
) -> float:
    """按 hit/miss/output 三档计价。

    兼容回退：若调用方未传 cache 拆分（hit=miss=0 但有 prompt_tokens），
    则把全部 prompt_tokens 当作 miss 计价，保证旧调用方不回归。
    """
    rate = rates.get(model) or rates["__default__"]
    miss = prompt_cache_miss_tokens
    hit = prompt_cache_hit_tokens
    if miss == 0 and hit == 0:
        miss = prompt_tokens  # 无 cache 拆分信息时的兜底
    return round(
        hit * rate["hit"] + miss * rate["miss"] + completion_tokens * rate["output"],
        6,
    )


def estimate_cost(
    *,
    provider: str,
    model: Optional[str] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    prompt_cache_hit_tokens: int = 0,
    prompt_cache_miss_tokens: int = 0,
    status: str,
    **_,
) -> Optional[float]:
    """预估单次调用费用（元）。仅 success 返回数值，否则 None。"""
    if status != "success":
        return None

    # LLM
    rates = _LLM_RATES.get(provider)
    if rates is not None:
        return _llm_cost(
            rates, model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_cache_hit_tokens=prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=prompt_cache_miss_tokens,
        )

    # OCR
    if provider in _OCR_PER_CALL:
        return round(_OCR_PER_CALL[provider], 6)

    return None
