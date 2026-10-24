-- Sewa Setu Old Age Pension Portal - schema
-- NOTE: seed.sql (shipped separately) loads after this and contains the
-- production data. Do not re-run against a live database.

CREATE TABLE IF NOT EXISTS applications (
    id              SERIAL PRIMARY KEY,
    application_no  VARCHAR(20),
    applicant_name  VARCHAR(100),
    mobile          VARCHAR(15),
    dob             DATE,
    gender          VARCHAR(10),
    marital_status  VARCHAR(20),
    husband_name    VARCHAR(100),
    husband_employer VARCHAR(100),
    village         VARCHAR(100),
    block           VARCHAR(50),
    bank_account    VARCHAR(30),
    ifsc            VARCHAR(15),
    doc_path        VARCHAR(200),
    status          VARCHAR(20) DEFAULT 'PENDING',
    submitted_at    TIMESTAMP,
    decided_at      TIMESTAMP,
    decided_by      VARCHAR(50)
);

-- Status portal accounts (created at submission time)
CREATE TABLE IF NOT EXISTS portal_users (
    mobile        VARCHAR(15) PRIMARY KEY,
    password_hash VARCHAR(80)
);

CREATE TABLE IF NOT EXISTS otps (
    id         SERIAL PRIMARY KEY,
    mobile     VARCHAR(15),
    code       VARCHAR(6),
    created_at TIMESTAMP
);

-- Provider-approved pseudonymous subject reference only; never store Aadhaar
-- numbers, OTPs, or authentication payloads here.
CREATE TABLE IF NOT EXISTS identity_verifications (
    id                 SERIAL PRIMARY KEY,
    application_id     INTEGER NOT NULL UNIQUE
                       REFERENCES applications(id) ON DELETE RESTRICT,
    provider_name      VARCHAR(100) NOT NULL,
    subject_reference  VARCHAR(255) NOT NULL,
    verified_at        TIMESTAMP NOT NULL,
    CONSTRAINT identity_verifications_provider_subject_unique
        UNIQUE (provider_name, subject_reference)
);
