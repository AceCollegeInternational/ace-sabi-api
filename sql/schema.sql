-- =============================================================================
-- SABI API — DATABASE SCHEMA
-- Database: sabi_db (MySQL)
-- 
-- This schema covers all data that is NOT available in the enterprise or
-- Moodle databases. The enterprise DB provides: student comprehension scores,
-- student attendance, parent communication records. Moodle provides: 
-- evaluation scores for value-added calculation. Everything else lives here.
-- =============================================================================

CREATE DATABASE IF NOT EXISTS sabi_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE sabi_db;

-- =============================================================================
-- AUTHENTICATION
-- =============================================================================

CREATE TABLE api_keys (
    id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    key_hash      VARCHAR(64) NOT NULL UNIQUE,   -- SHA-256 hash of the actual key
    label         VARCHAR(100) NOT NULL,          -- e.g. 'openclaw-staff-bot', 'n8n-prod'
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at  DATETIME NULL,
    INDEX idx_key_hash (key_hash)
) COMMENT 'API key registry. Store hashed keys only. Never store raw keys.';


-- =============================================================================
-- CORE REFERENCE TABLES
-- =============================================================================

CREATE TABLE teachers (
    id                INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    -- Link to enterprise DB staff record. NULL if staff not yet in enterprise DB.
    enterprise_id     VARCHAR(50) NULL UNIQUE,
    telegram_id       BIGINT NULL UNIQUE,         -- Telegram user ID for bot interactions
    first_name        VARCHAR(100) NOT NULL,
    last_name         VARCHAR(100) NOT NULL,
    email             VARCHAR(150) NULL UNIQUE,
    phone             VARCHAR(20) NULL,
    subject_primary   VARCHAR(100) NULL,          -- e.g. 'Mathematics'
    subject_secondary VARCHAR(100) NULL,
    employment_type   ENUM('full_time','part_time','contract') NOT NULL DEFAULT 'full_time',
    date_joined       DATE NOT NULL,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_enterprise_id (enterprise_id),
    INDEX idx_telegram_id (telegram_id),
    INDEX idx_active (is_active)
) COMMENT 'Master teacher register. Links to enterprise DB via enterprise_id.';


CREATE TABLE academic_terms (
    id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    term_name     VARCHAR(50) NOT NULL,           -- e.g. 'First Term 2025/2026'
    academic_year VARCHAR(9) NOT NULL,            -- e.g. '2025/2026'
    term_number   TINYINT UNSIGNED NOT NULL,      -- 1, 2, or 3
    start_date    DATE NOT NULL,
    end_date      DATE NOT NULL,
    is_current    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_term (academic_year, term_number),
    INDEX idx_current (is_current)
) COMMENT 'Academic term calendar. Exactly one row should have is_current = TRUE.';


-- =============================================================================
-- KPI WEIGHT CONFIGURATION
-- Weights are stored per term so they can change between terms.
-- On first insert the agreed framework values are used as defaults.
-- All weights are expressed as percentages (0.00 to 100.00).
-- The sum of category_weight across all rows for one term must equal 100.
-- The sum of index_weight across all indices in one category must equal
-- that category's category_weight.
-- =============================================================================

CREATE TABLE kpi_categories (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    category_key     VARCHAR(50) NOT NULL UNIQUE, -- machine key, never changes
    category_name    VARCHAR(100) NOT NULL,        -- display name, can change
    display_order    TINYINT UNSIGNED NOT NULL DEFAULT 0,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) COMMENT 'KPI category definitions. Keys are stable; names can be updated.';

INSERT INTO kpi_categories (category_key, category_name, display_order) VALUES
('academic_impact',       'Academic Impact',        1),
('professional_reliability', 'Professional Reliability', 2),
('professional_growth',   'Professional Growth',    3),
('institutional_care',    'Institutional Care',     4);


CREATE TABLE kpi_indices (
    id             INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    category_id    INT UNSIGNED NOT NULL,
    index_key      VARCHAR(80) NOT NULL UNIQUE,    -- machine key, never changes
    index_name     VARCHAR(150) NOT NULL,           -- display name
    data_source    ENUM('sabi','enterprise','moodle','computed') NOT NULL,
    description    TEXT NULL,
    display_order  TINYINT UNSIGNED NOT NULL DEFAULT 0,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES kpi_categories(id),
    INDEX idx_category (category_id)
) COMMENT 'Individual KPI indices within each category.';

INSERT INTO kpi_indices
    (category_id, index_key, index_name, data_source, description, display_order)
VALUES
-- Academic Impact
(1, 'comprehension_score',    'Comprehension Score',          'enterprise',
 'Average student comprehension score across teacher classes, sourced from enterprise DB.', 1),
(1, 'value_added_progress',   'Value-Added Progress',         'computed',
 'Difference between expected and actual student progress. Rewards teachers who move students forward regardless of starting point.', 2),
(1, 'learning_retention',     'Learning Retention',           'computed',
 'How well students retain knowledge from prior topics. Measured via revision questions embedded in assessments.', 3),
(1, 'observation_score',      'Lesson Observation Score',     'sabi',
 'HOD or principal structured classroom observation score. Scale 0-100.', 4),
-- Professional Reliability
(2, 'punctuality',            'Punctuality',                  'sabi',
 'Percentage of school days teacher arrived on time. Tracked via daily Telegram log.', 1),
(2, 'lesson_plan_compliance', 'Lesson Plan Compliance',       'sabi',
 'Percentage of weekly lesson plans submitted on time and on-topic.', 2),
(2, 'teacher_attendance',     'Attendance',                   'sabi',
 'Percentage of scheduled working days attended. Excludes approved leave.', 3),
(2, 'marking_timeliness',     'Marking & Feedback Timeliness','sabi',
 'Average days between test/assignment date and when marked scores were submitted.', 4),
-- Professional Growth
(3, 'pd_quality_score',       'Professional Development',     'sabi',
 'Weighted score based on PD hours completed, relevance category, and evidence submitted.', 1),
(3, 'peer_mentorship',        'Peer Mentorship',              'sabi',
 'Sessions logged as mentor or mentee, confirmed by both parties.', 2),
(3, 'curriculum_contribution','Curriculum Contribution',      'sabi',
 'Shared teaching resources created and adopted by other staff.', 3),
-- Institutional Care
(4, 'pastoral_logs',          'Pastoral Engagement',          'sabi',
 'Number of pastoral and welfare observations logged during the term.', 1),
(4, 'student_feedback',       'Student Feedback',             'sabi',
 'End-of-term anonymous student satisfaction rating for this teacher. Scale 0-100.', 2),
(4, 'parent_engagement_rate', 'Parent Engagement Rate',       'enterprise',
 'Response rate of parents to school communications, sourced from enterprise DB.', 3),
(4, 'incident_rate',          'Discipline Incident Rate',     'sabi',
 'Inverse score: fewer disciplinary incidents in teacher classes = higher score.', 4);


CREATE TABLE kpi_weights (
    index_id    INT UNSIGNED PRIMARY KEY,
    weight      DECIMAL(5,2) NOT NULL,
    -- All rows must sum to 100.00. Enforced in the application layer on update.
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by  VARCHAR(150) NULL,      -- label of the API key or user that last changed this
    FOREIGN KEY (index_id) REFERENCES kpi_indices(id)
) COMMENT 'Active KPI weights. One row per index, always sums to 100.00. Update to rebalance — past computed scores in kpi_scores are unaffected.';

-- Seed the agreed framework defaults.
-- To change weights: UPDATE kpi_weights SET weight = x, updated_by = y WHERE index_id = z;
-- then verify the new total equals 100.00.
INSERT INTO kpi_weights (index_id, weight, updated_by)
SELECT id,
    CASE index_key
        WHEN 'comprehension_score'     THEN 10.00
        WHEN 'value_added_progress'    THEN 15.00
        WHEN 'learning_retention'      THEN  8.00
        WHEN 'observation_score'       THEN  7.00
        WHEN 'punctuality'             THEN  8.00
        WHEN 'lesson_plan_compliance'  THEN  7.00
        WHEN 'teacher_attendance'      THEN  5.00
        WHEN 'marking_timeliness'      THEN  5.00
        WHEN 'pd_quality_score'        THEN 10.00
        WHEN 'peer_mentorship'         THEN  6.00
        WHEN 'curriculum_contribution' THEN  4.00
        WHEN 'pastoral_logs'           THEN  5.00
        WHEN 'student_feedback'        THEN  4.00
        WHEN 'parent_engagement_rate'  THEN  3.00
        WHEN 'incident_rate'           THEN  3.00
        ELSE 0.00
    END,
    'system_seed'
FROM kpi_indices;


-- =============================================================================
-- TEACHER ATTENDANCE & PUNCTUALITY
-- Tracked in Sabi because enterprise DB does not provide this.
-- =============================================================================

CREATE TABLE teacher_attendance (
    id             INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    teacher_id     INT UNSIGNED NOT NULL,
    term_id        INT UNSIGNED NOT NULL,
    log_date       DATE NOT NULL,
    status         ENUM('present','absent','late','approved_leave','public_holiday') NOT NULL,
    -- Time teacher was physically registered/checked in. NULL if absent.
    arrival_time   TIME NULL,
    -- Expected arrival time for this school (e.g. 07:30:00). Sourced from config.
    expected_time  TIME NOT NULL DEFAULT '07:30:00',
    -- Minutes late. 0 if on time. NULL if absent.
    minutes_late   SMALLINT UNSIGNED NULL,
    notes          TEXT NULL,
    logged_by      VARCHAR(100) NULL,             -- who logged this (admin Telegram handle)
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_teacher_date (teacher_id, log_date),
    FOREIGN KEY (teacher_id) REFERENCES teachers(id),
    FOREIGN KEY (term_id)    REFERENCES academic_terms(id),
    INDEX idx_teacher_term (teacher_id, term_id),
    INDEX idx_date (log_date)
) COMMENT 'Daily teacher attendance and punctuality log. Replaces absent cover register.';


-- =============================================================================
-- LESSON PLAN SUBMISSIONS
-- =============================================================================

CREATE TABLE lesson_plan_submissions (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    teacher_id       INT UNSIGNED NOT NULL,
    term_id          INT UNSIGNED NOT NULL,
    week_number      TINYINT UNSIGNED NOT NULL,   -- 1 to ~14
    due_date         DATE NOT NULL,               -- typically the Friday of that week
    submitted_at     DATETIME NULL,               -- NULL = not yet submitted
    -- Was the plan submitted before the deadline?
    is_on_time       BOOLEAN NULL,
    -- Did the plan content match the expected scheme of work topic?
    -- Set by HOD/admin review or automated topic check.
    is_on_topic      BOOLEAN NULL,
    file_reference   VARCHAR(500) NULL,           -- Google Drive link or file path
    notes            TEXT NULL,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_teacher_term_week (teacher_id, term_id, week_number),
    FOREIGN KEY (teacher_id) REFERENCES teachers(id),
    FOREIGN KEY (term_id)    REFERENCES academic_terms(id),
    INDEX idx_teacher_term (teacher_id, term_id)
) COMMENT 'Weekly lesson plan submission log per teacher.';


-- =============================================================================
-- LESSON OBSERVATIONS
-- =============================================================================

CREATE TABLE lesson_observations (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    teacher_id      INT UNSIGNED NOT NULL,
    term_id         INT UNSIGNED NOT NULL,
    observed_on     DATE NOT NULL,
    observer_name   VARCHAR(150) NOT NULL,        -- HOD or principal name
    subject         VARCHAR(100) NULL,
    class_name      VARCHAR(50) NULL,             -- e.g. 'SS2A'
    -- Rubric scores (each 0-25, total = 100)
    score_questioning       TINYINT UNSIGNED NOT NULL DEFAULT 0, -- quality of questioning
    score_engagement        TINYINT UNSIGNED NOT NULL DEFAULT 0, -- student engagement
    score_differentiation   TINYINT UNSIGNED NOT NULL DEFAULT 0, -- catering to different levels
    score_pacing            TINYINT UNSIGNED NOT NULL DEFAULT 0, -- lesson pacing and structure
    -- Computed: sum of the four scores above (0-100)
    total_score     TINYINT UNSIGNED GENERATED ALWAYS AS
                    (score_questioning + score_engagement + score_differentiation + score_pacing)
                    STORED,
    strengths       TEXT NULL,
    areas_to_improve TEXT NULL,
    -- Was observation shared with teacher?
    shared_with_teacher BOOLEAN NOT NULL DEFAULT FALSE,
    shared_at       DATETIME NULL,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (teacher_id) REFERENCES teachers(id),
    FOREIGN KEY (term_id)    REFERENCES academic_terms(id),
    INDEX idx_teacher_term (teacher_id, term_id)
) COMMENT 'Structured classroom observation records. Two per teacher per term recommended.';


-- =============================================================================
-- MARKING TIMELINESS
-- Tracks the gap between test/assignment date and score submission date.
-- =============================================================================

CREATE TABLE marking_timeliness (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    teacher_id       INT UNSIGNED NOT NULL,
    term_id          INT UNSIGNED NOT NULL,
    assessment_name  VARCHAR(200) NOT NULL,       -- e.g. 'Week 4 Maths Test - SS2A'
    class_name       VARCHAR(50) NULL,
    assessment_date  DATE NOT NULL,               -- date the test/assignment was given
    scores_submitted_at DATETIME NULL,            -- when teacher submitted all scores
    -- Computed: calendar days between assessment_date and scores_submitted_at
    days_to_submit   SMALLINT UNSIGNED NULL,
    -- School policy threshold: submissions within this many days = compliant
    policy_days      TINYINT UNSIGNED NOT NULL DEFAULT 7,
    is_compliant     BOOLEAN GENERATED ALWAYS AS
                     (days_to_submit IS NOT NULL AND days_to_submit <= policy_days)
                     STORED,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (teacher_id) REFERENCES teachers(id),
    FOREIGN KEY (term_id)    REFERENCES academic_terms(id),
    INDEX idx_teacher_term (teacher_id, term_id)
) COMMENT 'Tracks how promptly teachers return marked work. Policy threshold is configurable per record.';


-- =============================================================================
-- PROFESSIONAL DEVELOPMENT
-- =============================================================================

CREATE TABLE pd_logs (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    teacher_id       INT UNSIGNED NOT NULL,
    term_id          INT UNSIGNED NOT NULL,
    -- PD relevance category affects quality weighting
    pd_type          ENUM(
                       'subject_specific',   -- highest weight: directly improves subject delivery
                       'pedagogy',           -- teaching methodology, classroom management
                       'technology',         -- edtech, digital tools
                       'leadership',         -- management, administration
                       'general'             -- lowest weight: broad/generic
                     ) NOT NULL DEFAULT 'general',
    title            VARCHAR(300) NOT NULL,
    provider         VARCHAR(200) NULL,           -- e.g. 'Pearson Nigeria', 'Coursera'
    attended_on      DATE NOT NULL,
    duration_hours   DECIMAL(4,1) NOT NULL,       -- e.g. 1.5, 8.0
    -- Evidence: certificate, photo, URL, Google Drive link
    evidence_ref     VARCHAR(500) NULL,
    -- Has this been verified by admin? Unverified logs have reduced weight.
    is_verified      BOOLEAN NOT NULL DEFAULT FALSE,
    verified_by      VARCHAR(150) NULL,
    verified_at      DATETIME NULL,
    notes            TEXT NULL,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (teacher_id) REFERENCES teachers(id),
    FOREIGN KEY (term_id)    REFERENCES academic_terms(id),
    INDEX idx_teacher_term (teacher_id, term_id)
) COMMENT 'Professional development event log per teacher. Type affects quality weighting.';


CREATE TABLE pd_type_weights (
    pd_type          ENUM('subject_specific','pedagogy','technology','leadership','general')
                     PRIMARY KEY,
    weight_multiplier DECIMAL(3,2) NOT NULL,
    -- subject_specific = 1.50, pedagogy = 1.25, technology = 1.10,
    -- leadership = 1.00, general = 0.75
    description      VARCHAR(200) NULL,
    updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) COMMENT 'Quality multipliers for PD types. Adjustable without code changes.';

INSERT INTO pd_type_weights (pd_type, weight_multiplier, description) VALUES
('subject_specific', 1.50, 'Direct subject expertise improvement. Highest multiplier.'),
('pedagogy',         1.25, 'Teaching method and classroom practice.'),
('technology',       1.10, 'Educational technology and digital tools.'),
('leadership',       1.00, 'School leadership and administration.'),
('general',          0.75, 'Generic or broad professional development. Lowest multiplier.');


-- =============================================================================
-- PEER MENTORSHIP
-- =============================================================================

CREATE TABLE mentorship_logs (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    term_id          INT UNSIGNED NOT NULL,
    mentor_id        INT UNSIGNED NOT NULL,       -- the teacher providing mentorship
    mentee_id        INT UNSIGNED NOT NULL,       -- the teacher receiving mentorship
    session_date     DATE NOT NULL,
    duration_minutes SMALLINT UNSIGNED NOT NULL,
    topic            VARCHAR(300) NULL,           -- e.g. 'Questioning technique in SS1'
    notes            TEXT NULL,
    -- Both parties must confirm for the session to count toward KPI
    mentor_confirmed  BOOLEAN NOT NULL DEFAULT FALSE,
    mentee_confirmed  BOOLEAN NOT NULL DEFAULT FALSE,
    mentor_confirmed_at DATETIME NULL,
    mentee_confirmed_at DATETIME NULL,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (term_id)    REFERENCES academic_terms(id),
    FOREIGN KEY (mentor_id)  REFERENCES teachers(id),
    FOREIGN KEY (mentee_id)  REFERENCES teachers(id),
    INDEX idx_mentor_term (mentor_id, term_id),
    INDEX idx_mentee_term  (mentee_id, term_id)
) COMMENT 'Peer mentorship sessions. Dual confirmation required to count toward KPI.';


-- =============================================================================
-- CURRICULUM CONTRIBUTIONS
-- =============================================================================

CREATE TABLE curriculum_contributions (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    teacher_id       INT UNSIGNED NOT NULL,
    term_id          INT UNSIGNED NOT NULL,
    title            VARCHAR(300) NOT NULL,       -- e.g. 'SS2 Maths Revision Guide Term 1'
    resource_type    ENUM(
                       'question_bank',
                       'revision_guide',
                       'teaching_aid',
                       'worksheet',
                       'scheme_of_work',
                       'other'
                     ) NOT NULL DEFAULT 'other',
    file_reference   VARCHAR(500) NULL,           -- Google Drive link
    -- How many other staff have used/adopted this resource
    adoption_count   TINYINT UNSIGNED NOT NULL DEFAULT 0,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (teacher_id) REFERENCES teachers(id),
    FOREIGN KEY (term_id)    REFERENCES academic_terms(id),
    INDEX idx_teacher_term (teacher_id, term_id)
) COMMENT 'Resources created by teachers and shared with colleagues.';


-- =============================================================================
-- PASTORAL LOGS
-- Shared between the welfare system and the KPI system.
-- =============================================================================

CREATE TABLE pastoral_logs (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    teacher_id       INT UNSIGNED NOT NULL,       -- teacher who filed the log
    term_id          INT UNSIGNED NOT NULL,
    -- enterprise_student_id links to the student record in the enterprise DB
    enterprise_student_id VARCHAR(50) NOT NULL,
    log_type         ENUM('welfare','discipline','pastoral','positive') NOT NULL,
    description      TEXT NOT NULL,
    action_taken     TEXT NULL,
    follow_up_date   DATE NULL,
    follow_up_done   BOOLEAN NOT NULL DEFAULT FALSE,
    parent_notified  BOOLEAN NOT NULL DEFAULT FALSE,
    parent_notified_at DATETIME NULL,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (teacher_id) REFERENCES teachers(id),
    FOREIGN KEY (term_id)    REFERENCES academic_terms(id),
    INDEX idx_teacher_term (teacher_id, term_id),
    INDEX idx_student (enterprise_student_id),
    INDEX idx_type (log_type)
) COMMENT 'Pastoral, welfare and discipline logs filed by teachers. Feeds both welfare system and KPI.';


-- =============================================================================
-- STUDENT FEEDBACK
-- End-of-term anonymous student ratings per teacher.
-- =============================================================================

CREATE TABLE student_feedback (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    teacher_id       INT UNSIGNED NOT NULL,
    term_id          INT UNSIGNED NOT NULL,
    class_name       VARCHAR(50) NOT NULL,        -- e.g. 'SS2A'
    -- Aggregated ratings (0-100 scale, computed from raw student responses)
    -- Individual responses are NOT stored to preserve anonymity
    score_clarity    TINYINT UNSIGNED NOT NULL,   -- explains clearly when not understood
    score_safety     TINYINT UNSIGNED NOT NULL,   -- students feel safe to ask questions
    score_care       TINYINT UNSIGNED NOT NULL,   -- teacher cares whether students learn
    -- Number of students who responded
    response_count   SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    -- Total students in class (to compute response rate)
    class_size       SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    -- Computed average across the three dimensions
    aggregate_score  DECIMAL(5,2) GENERATED ALWAYS AS
                     ((score_clarity + score_safety + score_care) / 3.0)
                     STORED,
    collected_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_teacher_term_class (teacher_id, term_id, class_name),
    FOREIGN KEY (teacher_id) REFERENCES teachers(id),
    FOREIGN KEY (term_id)    REFERENCES academic_terms(id),
    INDEX idx_teacher_term (teacher_id, term_id)
) COMMENT 'End-of-term anonymous student satisfaction scores per teacher per class.';


-- =============================================================================
-- DISCIPLINARY GATEWAY
-- Does NOT affect the score. Determines eligibility only.
-- =============================================================================

CREATE TABLE disciplinary_gateway (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    teacher_id       INT UNSIGNED NOT NULL,
    term_id          INT UNSIGNED NOT NULL,
    action_type      ENUM('query','verbal_warning','written_warning','final_warning','suspension')
                     NOT NULL,
    issued_date      DATE NOT NULL,
    reason           TEXT NOT NULL,
    issued_by        VARCHAR(150) NOT NULL,
    -- An action is "active" while it blocks incentive eligibility.
    -- Set to FALSE when the matter is formally resolved.
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    resolved_date    DATE NULL,
    resolution_notes TEXT NULL,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (teacher_id) REFERENCES teachers(id),
    FOREIGN KEY (term_id)    REFERENCES academic_terms(id),
    INDEX idx_teacher_active (teacher_id, is_active),
    INDEX idx_term (term_id)
) COMMENT 'Disciplinary actions. Any active record blocks incentive eligibility. Does not affect score.';


-- =============================================================================
-- KPI SCORES
-- Computed and stored at end of term (or on-demand).
-- One row per teacher per term. Recomputation overwrites the existing row.
-- =============================================================================

CREATE TABLE kpi_scores (
    id                       INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    teacher_id               INT UNSIGNED NOT NULL,
    term_id                  INT UNSIGNED NOT NULL,

    -- Category subtotals (each is the weighted contribution to the 100-point total)
    score_academic_impact         DECIMAL(6,3) NULL,  -- max 40.000
    score_professional_reliability DECIMAL(6,3) NULL, -- max 25.000
    score_professional_growth     DECIMAL(6,3) NULL,  -- max 20.000
    score_institutional_care      DECIMAL(6,3) NULL,  -- max 15.000

    -- Individual index raw scores (0.00 to 100.00 before weighting)
    raw_comprehension_score       DECIMAL(5,2) NULL,
    raw_value_added_progress      DECIMAL(5,2) NULL,
    raw_learning_retention        DECIMAL(5,2) NULL,
    raw_observation_score         DECIMAL(5,2) NULL,
    raw_punctuality               DECIMAL(5,2) NULL,
    raw_lesson_plan_compliance    DECIMAL(5,2) NULL,
    raw_teacher_attendance        DECIMAL(5,2) NULL,
    raw_marking_timeliness        DECIMAL(5,2) NULL,
    raw_pd_quality_score          DECIMAL(5,2) NULL,
    raw_peer_mentorship           DECIMAL(5,2) NULL,
    raw_curriculum_contribution   DECIMAL(5,2) NULL,
    raw_pastoral_logs             DECIMAL(5,2) NULL,
    raw_student_feedback          DECIMAL(5,2) NULL,
    raw_parent_engagement_rate    DECIMAL(5,2) NULL,
    raw_incident_rate             DECIMAL(5,2) NULL,

    -- Final score and eligibility
    total_score              DECIMAL(6,3) NULL,       -- 0.000 to 100.000
    is_eligible              BOOLEAN NOT NULL DEFAULT FALSE, -- gateway check result
    ineligibility_reason     VARCHAR(300) NULL,

    -- Delta from previous term (NULL for first term)
    previous_term_score      DECIMAL(6,3) NULL,
    score_delta              DECIMAL(6,3) NULL,       -- positive = improvement

    -- Computation metadata
    computed_at              DATETIME NULL,
    computation_notes        TEXT NULL,               -- any warnings or missing data flags

    created_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_teacher_term (teacher_id, term_id),
    FOREIGN KEY (teacher_id) REFERENCES teachers(id),
    FOREIGN KEY (term_id)    REFERENCES academic_terms(id),
    INDEX idx_term_score (term_id, total_score),       -- for leaderboard queries
    INDEX idx_teacher (teacher_id)
) COMMENT 'Computed KPI scores per teacher per term. Stores both raw and weighted values for full transparency.';


-- =============================================================================
-- END OF SCHEMA
-- =============================================================================

-- Enforcement tables are maintained in a dedicated migration file so the
-- deferred accountability system can be rolled out independently:
-- sql/add_enforcement_tables.sql
