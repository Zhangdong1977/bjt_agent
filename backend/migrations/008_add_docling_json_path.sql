-- Add docling_json_path column to documents table
-- Stores the path to DoclingDocument JSON for PDF documents parsed by Docling
ALTER TABLE documents ADD COLUMN IF NOT EXISTS docling_json_path VARCHAR(500);
