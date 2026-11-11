-- doc 25 号方案一：审查任务级图片预识别缓存。
-- 每张解析落盘图片的 OCR 全文 + 内容类型标签，跨任务复用（同一张图全库只识别一次）；
-- 标签注入 get_section_images / doc_search 输出，供子 agent 按图选图而非盲选。
-- 新库由 SQLAlchemy create_all() 建表；本迁移面向存量环境，幂等可重放。

CREATE TABLE IF NOT EXISTS document_image_annotations (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    document_id VARCHAR(36) NOT NULL,
    image_name VARCHAR(255) NOT NULL,
    label VARCHAR(100),
    summary VARCHAR(200),
    ocr_text TEXT,
    provider VARCHAR(20) NOT NULL DEFAULT 'baidu',
    status VARCHAR(20) NOT NULL DEFAULT 'ok',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_dia_document FOREIGN KEY (document_id)
        REFERENCES documents(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_dia_document_image
    ON document_image_annotations(document_id, image_name);
CREATE INDEX IF NOT EXISTS idx_dia_document
    ON document_image_annotations(document_id);
