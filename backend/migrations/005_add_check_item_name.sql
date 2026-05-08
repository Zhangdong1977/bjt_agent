-- Add rule_doc_name and check_item_name columns to review results tables
ALTER TABLE review_results ADD COLUMN IF NOT EXISTS rule_doc_name VARCHAR(255);
ALTER TABLE review_results ADD COLUMN IF NOT EXISTS check_item_name VARCHAR(255);

ALTER TABLE project_review_results ADD COLUMN IF NOT EXISTS rule_doc_name VARCHAR(255);
ALTER TABLE project_review_results ADD COLUMN IF NOT EXISTS check_item_name VARCHAR(255);
