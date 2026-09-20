-- 044: blind_check_findings 补 evidences/rule_references 两列（幂等，可重复执行）
-- 背景（2026-09-12，ADR-0002「AI 主笔、工具供事实」）：发现卡按暗标要求维度聚合，
-- 卡内 evidences 列表承载多条定位锚点（text/page_number/paragraph_index/story/
-- locateable），rule_references 记录该卡吸收的工具规则（守门员覆盖判定依据）。
-- 同族教训（023/040/041/043）：新代码引用的每一列必须有对应迁移，预发布
-- create_all 建出的列不代表生产 schema 就绪。
ALTER TABLE blind_check_findings ADD COLUMN IF NOT EXISTS evidences JSONB;
ALTER TABLE blind_check_findings ADD COLUMN IF NOT EXISTS rule_references JSONB;
