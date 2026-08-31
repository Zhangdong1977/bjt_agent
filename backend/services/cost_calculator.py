"""费用预估（按各厂商公开价目表的预估值）。

单价集中在本文件（唯一来源），运营台只原值透传、不二次计算，避免两处价目漂移。
调价只改这里。单位：元 / 单位；LLM 按百万 token，OCR 按次。
价格来源：各厂商公开价目表（标注日期，便于核对更新）。

本期为"按公开价目表的预估值"，落到 ai_usage_records.cost_cny；
仅 status=success 的记录累计算钱（error/timeout 行 caller 传 status!=success，
本函数直接返回 None）。

DeepSeek 上下文缓存拆分计价：命中输入约为未命中输入的 1/30，必须分开计；
其它厂商暂无缓存拆分，hit=0、miss=prompt_tokens 兜底（见 _llm_cost）。
DeepSeek 官方价目表分高峰/空闲双档（2026-08 调价），本计费口径定高峰 =
北京时间周一至周五 9:00-12:00、14:00-18:00，其余空闲（含整个周末；
与官方"不分工作日/周末"的差异是业务决策，周末差价由平台吸收）；按调用
时刻选档见 _is_peak_beijing，调用方不传 at 则取当前时刻。
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

# —— DeepSeek（按百万 token）—— 来源 api-docs.deepseek.com/zh-cn 公开价目表（2026-08-17 核对）
#    三档：缓存命中输入 hit / 缓存未命中输入 miss / 输出 output
#    2026-08 官方调价为高峰/空闲双档（空闲价 = 高峰价的一半，元/百万 token）：
#      高峰（北京周一至周五 9:00-12:00、14:00-18:00）：命中 0.10 / 未命中 3.0 / 输出 9.0
#      空闲（其余时段）：                    命中 0.05 / 未命中 1.5 / 输出 4.5
_DEEPSEEK_FLASH_TIERS = {
    "peak":    {"hit": 0.10 / 1_000_000, "miss": 3.0 / 1_000_000, "output": 9.0 / 1_000_000},
    "offpeak": {"hit": 0.05 / 1_000_000, "miss": 1.5 / 1_000_000, "output": 4.5 / 1_000_000},
}
_DEEPSEEK = {
    "deepseek-v4-flash": _DEEPSEEK_FLASH_TIERS,
    # 兜底：当前默认 provider 即 v4-flash，且 deepseek-chat/reasoner 已宣布 2026/07 弃用
    # 并映射到 v4-flash，故兜底价直接对齐 v4-flash。
    "__default__":       _DEEPSEEK_FLASH_TIERS,
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

# —— Tencent Cloud TokenHub（按百万 token）—— deepseek-v4-flash 为原厂直供正式版，
#    跟随原厂峰谷双档（2026-08-17 起），与 DeepSeek 官方同价同档，直接复用
#    _DEEPSEEK_FLASH_TIERS。来源 cloud.tencent.com/document/product/1823/130055
#    （2026-08-19 核对；表中另有一档非正式版 flat 价 hit0.2/miss1/output2，
#    现行 deepseek-v4-flash 调用均命中原厂直供正式版，不采用）。
_TENCENT = {
    "deepseek-v4-flash": _DEEPSEEK_FLASH_TIERS,
    "__default__":       _DEEPSEEK_FLASH_TIERS,
}

# —— LLM provider → 价目表映射 ——
_LLM_RATES = {
    "deepseek": _DEEPSEEK,
    "tencent": _TENCENT,
    "minimax": _MINIMAX,
    "volcengine": _VOLCENGINE,
}

# —— OCR（按次）—— 百度 accurate_basic，按内部约定结转单价（非厂商公开价）
_OCR_PER_CALL = {
    "baidu_ocr": 0.028,
}

# Embedding 按估算输入 token 计价（元/token）；缓存命中不产生输入 token。
_EMBEDDING_PER_TOKEN = {
    "volcengine_embedding": 0.50 / 1_000_000,
    "minimax_embedding": 0.50 / 1_000_000,
}

_BEIJING_TZ = timezone(timedelta(hours=8))


def _is_peak_beijing(at: Optional[datetime]) -> bool:
    """高峰时段：北京时间**周一至周五** 9:00-12:00、14:00-18:00，其余空闲
    （DeepSeek 官方与腾讯 TokenHub 原厂直供的 deepseek-v4-flash 双档价目
    共用同一时段定义）。

    官方价目不区分工作日/周末，周末同时段上游仍按高峰价向我们结算；
    2026-08-28 起计费口径主动收窄为仅工作日计高峰（周末按空闲对用户
    计费，差价由平台吸收，业务决策）。
    按 [起, 止) 半开区间实现：12:00 / 18:00 整点起计空闲。
    at 为空取当前时刻（≈ ai_usage_records 写入时刻，即调用发生时刻）；
    朴素 datetime 按 UTC 解释，避免依赖宿主机时区。
    """
    if at is None:
        at = datetime.now(timezone.utc)
    elif at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    local = at.astimezone(_BEIJING_TZ)
    return local.weekday() < 5 and (9 <= local.hour < 12 or 14 <= local.hour < 18)


def _llm_cost(
    rates: dict,
    model: Optional[str],
    *,
    prompt_tokens: int,
    completion_tokens: int,
    prompt_cache_hit_tokens: int = 0,
    prompt_cache_miss_tokens: int = 0,
    at: Optional[datetime] = None,
) -> float:
    """按 hit/miss/output 三档计价。

    兼容回退：若调用方未传 cache 拆分（hit=miss=0 但有 prompt_tokens），
    则把全部 prompt_tokens 当作 miss 计价，保证旧调用方不回归。
    价目表含 peak/offpeak 双档时（DeepSeek）按 at 所处时段选档。
    """
    rate = rates.get(model) or rates["__default__"]
    if "peak" in rate:
        rate = rate["peak" if _is_peak_beijing(at) else "offpeak"]
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
    at: Optional[datetime] = None,
    **_,
) -> Optional[float]:
    """预估单次调用费用（元）。仅 success 返回数值，否则 None。

    at 为计价时刻（DeepSeek 双档价目选峰谷用），缺省取当前时刻。
    """
    if status != "success":
        return None

    # LLM
    normalized_provider = provider[:-7] if provider.endswith("_vision") else provider
    rates = _LLM_RATES.get(normalized_provider)
    if rates is not None:
        return _llm_cost(
            rates, model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_cache_hit_tokens=prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=prompt_cache_miss_tokens,
            at=at,
        )

    # OCR
    if provider in _OCR_PER_CALL:
        return round(_OCR_PER_CALL[provider], 6)

    if provider in _EMBEDDING_PER_TOKEN:
        input_tokens = int(_.get("embedding_input_tokens", 0) or 0)
        return round(input_tokens * _EMBEDDING_PER_TOKEN[provider], 6)

    return None
