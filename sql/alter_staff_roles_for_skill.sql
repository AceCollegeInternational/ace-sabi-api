USE sabi_db;

ALTER TABLE staff_roles
    MODIFY COLUMN role ENUM(
        'principal','vice_principal','hr','admin',
        'hod','class_teacher','year_tutor'
    ) NOT NULL,
    ADD COLUMN subject_scope VARCHAR(100) NULL AFTER role,
    ADD COLUMN class_scope VARCHAR(50) NULL AFTER subject_scope,
    ADD COLUMN level_scope VARCHAR(100) NULL AFTER class_scope,
    ADD COLUMN assigned_by VARCHAR(150) NULL AFTER is_active,
    ADD COLUMN notes TEXT NULL AFTER assigned_by,
    ADD COLUMN updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP
        AFTER created_at,
    ADD UNIQUE KEY uq_teacher_role_scope
        (teacher_id, role, subject_scope, class_scope, level_scope),
    ADD INDEX idx_role (role, is_active),
    ADD INDEX idx_subject (subject_scope, role);
