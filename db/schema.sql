CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- 원문 문서 (감사결과 보고서, 심사결정, 변상판정 등)
CREATE TABLE IF NOT EXISTS documents (
    id            BIGSERIAL PRIMARY KEY,
    source        TEXT NOT NULL,          -- audit_report | review_decision | compensation | ...
    source_url    TEXT UNIQUE,
    title         TEXT NOT NULL,
    published_on  DATE,
    file_path     TEXT,
    full_text     TEXT,
    collected_at  TIMESTAMPTZ DEFAULT now()
);

-- 지적사항(사건) 단위 구조화 결과
CREATE TABLE IF NOT EXISTS cases (
    id                BIGSERIAL PRIMARY KEY,
    document_id       BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    agency            TEXT,
    agency_type       TEXT,               -- 중앙행정기관 | 지방자치단체 | 공공기관 | 기타
    audit_year        INT,
    violation_types   TEXT[] NOT NULL DEFAULT '{}',
    dispositions      TEXT[] NOT NULL DEFAULT '{}',
    amount_krw        BIGINT,
    contract_method   TEXT,
    vendor_related    BOOLEAN,
    related_laws      TEXT[] NOT NULL DEFAULT '{}',
    summary           TEXT,
    body              TEXT NOT NULL,      -- 해당 지적사항 원문
    confidence        REAL,
    needs_review      BOOLEAN DEFAULT TRUE,
    embedding         vector(1024)        -- 2단계: 의미 검색용
);

CREATE INDEX IF NOT EXISTS cases_body_trgm  ON cases USING gin (body gin_trgm_ops);
CREATE INDEX IF NOT EXISTS cases_title_trgm ON cases USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS cases_vtypes     ON cases USING gin (violation_types);
CREATE INDEX IF NOT EXISTS cases_disp       ON cases USING gin (dispositions);
CREATE INDEX IF NOT EXISTS cases_year       ON cases (audit_year);
