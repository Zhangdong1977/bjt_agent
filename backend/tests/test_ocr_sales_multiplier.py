"""OCR 独立销售倍率（迁移 051）单元测试：拆分公式、倍率快照取值、payload 解析。

结算集成路径（settle_task_consumption）依赖真实 DB，按本仓惯例不在单测覆盖，
由预发布 E2E 验证（运营配置 → snapshot → 任务结算 → consumption_records 审计列）。
"""

from decimal import Decimal

import pytest

from backend.models.sales import SalesConfig
from backend.schemas.sales import SalesConfigPayload
from backend.services.billing import split_sales_points
from backend.services.sales import ocr_multiplier_for_task


# ---------------------------------------------------------------------------
# split_sales_points：分段计价公式
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_split_when_ocr_multiplier_none():
    """ocr_multiplier=None：全额按功能倍率，与旧口径逐字节一致。"""
    total = Decimal("90.97")
    ocr = Decimal("86.10")
    legacy = split_sales_points(total, ocr, Decimal("3.8"), None)
    # 等价旧公式：cost_points × 3.8，HALF_UP 2 位
    assert legacy == (Decimal("90.97") * 10 * Decimal("3.8")).quantize(Decimal("0.01"))


@pytest.mark.unit
def test_split_applies_ocr_multiplier_to_ocr_share_only():
    """拆分：LLM 等其余成本 × 功能倍率 + OCR 成本 × OCR 倍率。"""
    # 任务 607de39c 实测形态：LLM 4.87 元 + OCR 86.10 元，功能倍率 3.8，OCR 倍率 2.5
    total = Decimal("90.97")
    ocr = Decimal("86.10")
    other = total - ocr  # 4.87
    points = split_sales_points(total, ocr, Decimal("3.8"), Decimal("2.5"))
    expected_other = (other * 10 * Decimal("3.8")).quantize(Decimal("0.01"))
    expected_ocr = (ocr * 10 * Decimal("2.5")).quantize(Decimal("0.01"))
    assert points == expected_other + expected_ocr
    assert points == Decimal("2337.56")  # 185.06 + 2152.50


@pytest.mark.unit
def test_split_with_zero_ocr_multiplier_makes_ocr_free():
    """OCR 倍率 0 = OCR 部分免费（0 值不得被当成"未配置"）。"""
    points = split_sales_points(Decimal("10"), Decimal("8"), Decimal("4"), Decimal("0"))
    assert points == Decimal("80.00")  # 仅 2 元非 OCR 成本 × 4 × 10


@pytest.mark.unit
def test_split_rounds_each_segment_half_up():
    """分段各自 HALF_UP 舍入后相加。"""
    # 0.855×10×3.8=32.49 → 32.49；0.045×10×0.5=0.225 → 0.23
    points = split_sales_points(Decimal("0.90"), Decimal("0.045"), Decimal("3.8"), Decimal("0.5"))
    assert points == Decimal("32.72")


# ---------------------------------------------------------------------------
# ocr_multiplier_for_task：NULL 语义 = 不拆分（不回退全局）
# ---------------------------------------------------------------------------


def _config(ocr_multiplier=None) -> SalesConfig:
    return SalesConfig(
        id="default",
        sales_multiplier=Decimal("4"),
        ocr_multiplier=ocr_multiplier,
    )


@pytest.mark.unit
def test_ocr_multiplier_null_means_no_split():
    assert ocr_multiplier_for_task(_config(ocr_multiplier=None)) is None


@pytest.mark.unit
def test_ocr_multiplier_zero_is_valid_free_value():
    """0 是有效值（OCR 免费），不是"未配置"。"""
    assert ocr_multiplier_for_task(_config(ocr_multiplier=0)) == Decimal("0")


@pytest.mark.unit
def test_ocr_multiplier_configured_value():
    assert ocr_multiplier_for_task(_config(ocr_multiplier=2.5)) == Decimal("2.5")


# ---------------------------------------------------------------------------
# snapshot payload：新字段解析与兼容
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_payload_ocr_multiplier_defaults_to_none():
    """旧版 operate-two 不推送该键 → None → 不拆分（兼容）。"""
    payload = SalesConfigPayload(sales_multiplier=4, low_balance_threshold=0, version=2)
    assert payload.ocr_multiplier is None


@pytest.mark.unit
def test_payload_ocr_multiplier_accepts_zero():
    payload = SalesConfigPayload(sales_multiplier=4, low_balance_threshold=0, version=2, ocr_multiplier=0)
    assert payload.ocr_multiplier == 0


@pytest.mark.unit
def test_payload_ocr_multiplier_rejects_negative_and_oversized():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SalesConfigPayload(sales_multiplier=4, low_balance_threshold=0, version=2, ocr_multiplier=-1)
    with pytest.raises(ValidationError):
        SalesConfigPayload(sales_multiplier=4, low_balance_threshold=0, version=2, ocr_multiplier=1001)
