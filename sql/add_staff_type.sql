-- Migration: add staff_type column to teachers table
-- Run once against sabi_db
-- Date: 2026-05-09

ALTER TABLE teachers
ADD COLUMN staff_type ENUM('teaching','office','both') NOT NULL DEFAULT 'teaching'
AFTER employment_type;
