-- 042：bid_wizard_qa_tasks 补 started_at / completed_at（幂等，可重复执行）
-- 背景：reconcile_task_billing（结算兜底，beat 每 120s）对全部任务模型统一引用
-- started_at（卡死扫描）与 completed_at（孤儿结算）。BidWizardQaTask 建模时缺这两列，
-- M2 部署后该兜底任务每轮 AttributeError 崩溃——正常路径结算不受影响（任务完成时
-- inline finalize），但异常路径（failed 后 billing 卡 retry/pending）从此无人回收，
-- 叠加 billing_max_active_tasks_per_user=1 会把整个账号闸门顶死。
-- 见 doc/workspace/20-bid-wizard.md 反馈⑭、13-pre-release-deploy-log.md 2026-09-10 条。
ALTER TABLE bid_wizard_qa_tasks ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NULL;
ALTER TABLE bid_wizard_qa_tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ NULL;
