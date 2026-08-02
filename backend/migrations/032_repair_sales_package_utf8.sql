-- Repair package copy that was seeded through a non-UTF-8 deployment pipe.
-- Keep operator-edited names/cautions untouched; only replace the known
-- question-mark placeholders produced by character loss.

BEGIN;

UPDATE sales_packages
SET name = CASE code
        WHEN 'experience' THEN '体验套餐'
        WHEN 'basic' THEN '基础套餐'
        WHEN 'premium' THEN '尊享套餐'
        WHEN 'luxury' THEN '豪华套餐'
        ELSE name
    END,
    updated_at = NOW()
WHERE code IN ('experience', 'basic', 'premium', 'luxury')
  AND name ~ '^[?]+$';

UPDATE sales_packages
SET caution = '500页以上标书谨慎使用',
    updated_at = NOW()
WHERE code = 'experience'
  AND caution ~ '^500[?]+$';

COMMIT;
