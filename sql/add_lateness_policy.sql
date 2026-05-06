USE sabi_db;

CREATE TABLE IF NOT EXISTS lateness_policy (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    late_count       TINYINT UNSIGNED NOT NULL,   -- cumulative count this row applies to
    level_name       VARCHAR(20)     NOT NULL,   -- occasional | frequent | habitual
    message_template TEXT            NOT NULL,   -- {teacher_name} placeholder supported
    notify_principal TINYINT(1)      NOT NULL DEFAULT 0,
    notify_hr        TINYINT(1)      NOT NULL DEFAULT 0,
    draft_warning    TINYINT(1)      NOT NULL DEFAULT 0,  -- draft formal written warning
    draft_query      TINYINT(1)      NOT NULL DEFAULT 0,  -- draft disciplinary query
    set_penalty_flag TINYINT(1)      NOT NULL DEFAULT 0,  -- set lateness_penalty in enforcement log
    is_active        TINYINT(1)      NOT NULL DEFAULT 1,
    created_at       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_late_count (late_count)
) COMMENT 'Per-count message templates and actions for teacher lateness enforcement.';

-- ── Seed data ─────────────────────────────────────────────────────────────
INSERT INTO lateness_policy
    (late_count, level_name, message_template, notify_principal,
     notify_hr, draft_warning, draft_query, set_penalty_flag)
VALUES
-- Level 1: Occasional (counts 1-3)
(1,  'occasional',
 'Hello {teacher_name}, we noticed you arrived a little late today. Just a friendly reminder that our school day begins at 7:30 AM. We appreciate your dedication to the students and hope to see you on time going forward.',
 0, 0, 0, 0, 0),

(2,  'occasional',
 '{teacher_name}, this is your second late arrival this term. Please note that punctuality is a key professional expectation at ACE College. Kindly make every effort to arrive by 7:30 AM. A third late arrival will be formally noted.',
 0, 0, 0, 0, 0),

(3,  'occasional',
 '{teacher_name}, you have now recorded three late arrivals this term. This is your final notice at the informal level. Please be advised that the next late arrival will trigger a formal enforcement process including notification to the Principal.',
 0, 0, 0, 0, 0),

-- Level 2: Frequent (counts 4-9)
(4,  'frequent',
 '{teacher_name}, your late arrival today is your fourth this term and has crossed into the Frequent Lateness threshold. The Principal has been notified and a formal written warning is being prepared. Please treat this with the seriousness it deserves.',
 1, 0, 1, 0, 0),

(5,  'frequent',
 '{teacher_name}, this is your fifth late arrival this term. You are currently under formal enforcement at Level 2. Continued lateness will escalate this matter further. The Principal has been informed of this incident.',
 1, 0, 0, 0, 0),

(6,  'frequent',
 '{teacher_name}, six late arrivals have now been recorded this term. This pattern is seriously concerning. The Principal has been notified. Please make an immediate and sustained effort to resolve this.',
 1, 0, 0, 0, 0),

(7,  'frequent',
 '{teacher_name}, your seventh late arrival has been recorded. This is a formal notice that you are approaching the Habitual Lateness threshold. The Principal and HR have been informed. You are strongly advised to address this immediately.',
 1, 1, 0, 0, 0),

(8,  'frequent',
 '{teacher_name}, eight late arrivals this term. The Principal and HR are monitoring this situation. Two more late arrivals will trigger the Habitual Lateness classification with financial implications.',
 1, 1, 0, 0, 0),

(9,  'frequent',
 '{teacher_name}, this is your ninth late arrival. You are one incident away from the Habitual Lateness threshold. The Principal and HR have been notified. This is a final warning before financial deduction procedures commence.',
 1, 1, 0, 0, 0),

-- Level 3: Habitual (count 10 and above — insert up to 20 for safety)
(10, 'habitual',
 '{teacher_name}, your tenth late arrival has been recorded. You have been classified as a Habitual Late-comer. A disciplinary query has been raised and will be served to you formally. Financial deductions will apply at the end of this term as per school policy.',
 1, 1, 0, 1, 1),

(11, 'habitual',
 '{teacher_name}, eleventh late arrival recorded. You remain under Habitual Lateness classification. Every incident continues to be logged for disciplinary and payroll purposes.',
 1, 1, 0, 0, 0),

(12, 'habitual',
 '{teacher_name}, twelfth late arrival recorded. Your continued lateness is being formally documented. The Principal and HR are aware.',
 1, 1, 0, 0, 0),

(13, 'habitual',
 '{teacher_name}, thirteenth late arrival recorded. This pattern remains under formal review.',
 1, 1, 0, 0, 0),

(14, 'habitual',
 '{teacher_name}, fourteenth late arrival recorded. Habitual lateness classification remains active.',
 1, 1, 0, 0, 0),

(15, 'habitual',
 '{teacher_name}, fifteenth late arrival recorded. Your lateness record continues to be formally documented.',
 1, 1, 0, 0, 0)

ON DUPLICATE KEY UPDATE
    message_template = VALUES(message_template),
    notify_principal = VALUES(notify_principal),
    notify_hr        = VALUES(notify_hr),
    draft_warning    = VALUES(draft_warning),
    draft_query      = VALUES(draft_query),
    set_penalty_flag = VALUES(set_penalty_flag);
