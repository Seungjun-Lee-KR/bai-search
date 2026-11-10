# 감사원 결정례 검색 (bai-search)

감사원 감사결과·심사결정·변상판정을 모아 **위반유형별로 분류하고 검색**하는 서비스입니다.
횡령, 수의계약 쪼개기, 특정업체 몰아주기 같은 지적 사례를 빠르게 찾는 것이 목표입니다.

> ⚠️ **공개된 자료만** 넣으세요. 분류 단계에서 원문이 외부 LLM API(Anthropic)로 전송됩니다.
> 비공개·내부 자료는 공공기관 생성형 AI 보안 지침에 따라 별도 내부망 환경이 필요합니다.

## 구성

| 경로 | 역할 |
|---|---|
| `src/bai_search/parsers.py` | PDF · HWP(5.x) · HWPX에서 본문 추출 |
| `src/bai_search/segment.py` | 보고서 전문을 지적사항(사건) 단위로 분할 |
| `src/bai_search/taxonomy.py` | 위반유형·처분 분류표 (실무에 맞게 계속 수정) |
| `src/bai_search/classify.py` | Claude로 기관·금액·위반유형·처분·법령 구조화 |
| `src/bai_search/db.py`, `db/schema.sql` | PostgreSQL 저장·검색 (pg_trgm, pgvector) |
| `src/bai_search/embed.py` | 임베딩(local bge-m3 / voyage / hash)과 청크 분할 |
| `src/bai_search/search.py` | 키워드 + 의미 혼합 검색 (RRF) |
| `src/bai_search/ask.py` | 자연어 질문 → 검색 → Claude가 원문 인용하며 답변 |
| `src/bai_search/api.py`, `index.html` | 검색 API와 웹 화면 (검색 / 질문하기 탭) |
| `src/bai_search/cli.py` | `bai ingest / classify / serve` 명령 |

## 시작하기

```bash
# 1) DB 실행
docker compose up -d

# 2) 설치
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env   # ANTHROPIC_API_KEY 입력

# 3) 감사원 홈페이지에서 받은 PDF/HWP 파일을 data/raw/ 에 넣고
bai ingest data/raw --source audit_report

# 4) 분류 (처음엔 적은 건수로 결과 확인)
bai classify --limit 20

# 5) 의미 검색용 임베딩
pip install -e '.[local]'   # 처음 한 번, BAAI/bge-m3 모델(약 2GB)을 내려받음
bai embed

# 6) 질문 (명령줄)
bai ask "이미 계약에 포함된 일을 추가 과업으로 돈 더 준 사례"

# 7) 검색 화면
bai serve   # http://localhost:8000
```

## 의미 검색과 자연어 질문

- **혼합 검색:** 검색어가 있으면 키워드 검색과 의미 검색 결과를 순위 결합(RRF)합니다.
  "보관하던 돈을 직원이 빼돌린" 처럼 원문과 다른 표현으로 물어도 찾도록 하는 것이 목적입니다.
- **임베딩 방식 (`EMBED_BACKEND`)**
  - `local` (기본, 권장): `BAAI/bge-m3`를 PC에서 실행. 원문이 외부로 나가지 않습니다.
  - `voyage`: Voyage AI API. 원문이 외부로 전송됩니다.
  - `hash`: 모델 없이 글자 조각 일치로 계산. 테스트·오프라인용이며 뜻은 이해하지 못합니다.
  - 방식을 바꾸면 `bai embed --all`로 다시 만드세요.
- **질문하기:** 혼합 검색 상위 6건의 원문을 Claude에 보내고, Claude의 인용(citations) 기능으로
  답변 문장마다 근거 원문 위치를 붙입니다. 근거가 없으면 "찾지 못했습니다"라고 답하도록 지시합니다.
  질문 1건당 원문 약 3~5만 자가 전송됩니다. 기본 모델은 `ASK_MODEL=claude-opus-5-5`이며,
  안전 분류기가 답변을 거절하면 서버에서 다른 모델로 다시 시도하는 기능(fallbacks)을 켜 두었습니다.

분류 모델은 `.env`의 `CLASSIFY_MODEL`로 바꿉니다. 기본값은 `claude-opus-5-5`이고,
수천 건을 돌릴 때 비용을 줄이려면 `claude-haiku-5-5`로 바꿔 품질을 비교해 보세요.

## 질문 속 조건 자동 추출

검색어나 질문에 조건을 쓰면 규칙(`conditions.py`)으로 뽑아 필터로 적용합니다. AI를 쓰지 않아 비용이 없고 결과가 일정합니다.

| 조건 | 알아듣는 표현 예 |
|---|---|
| 연도 | `2025년`, `2023~2025년`, `2024년 이후`, `2022년 이전`, `최근 3년`, `작년`, `올해` |
| 금액 | `1억 원 이상`, `5천만원 넘는`, `10억 초과`, `1억5천만 원을 넘는`, `3억대` |
| 처분 | `징계`, `변상`, `문책`, `고발`, `수사의뢰` / `주의요구`, `시정요구`, `통보 처분` |
| 기관유형 | `지자체`, `공공기관`, `중앙부처` |
| 기관명 | DB에 있는 기관명 (`인천시` → 인천광역시 처럼 줄임말 포함) |

- 화면에 "질문에서 찾은 조건"이 표시되고, **조건 없이 다시 찾기**를 누르면 풀 수 있습니다.
- 조건에 맞는 사례가 0건이면 자동으로 조건을 풀고 다시 찾은 뒤 그 사실을 알립니다.
- `통보하지 않은`, `주의 깊게`처럼 처분이 아닌 뜻으로 쓰인 말은 조건으로 잡지 않습니다.
- 처분·기관명은 원문 양식에서 읽은 값이라 바로 걸러집니다. **연도·금액·기관유형은 AI 분류(`bai classify`)를
  마친 사건만** 걸러지니, 이 조건을 쓰려면 분류를 먼저 돌리세요.
- 사용자가 화면에서 직접 고른 필터가 자동 조건보다 우선합니다.

## 품질 관리

- 확신도 0.7 미만은 `needs_review = true`로 표시되어 화면에 "분류 검수 필요"가 뜹니다.
- 처음 200~300건은 직접 검수해서 분류표(`taxonomy.py`)와 프롬프트를 다듬으세요.
- 감사원 처분요구서 양식(`감 사 원 / 처분종류 / 제 목 / 소관기관 / 조치기관 / 내 용 / 조치할 사항`)은
  `segment.py`가 규칙으로 읽습니다. 제목·기관·처분·조치할 사항은 **AI 추정이 아니라 원문 값**이고,
  AI 분류가 이 값을 덮어쓰지 않습니다.
- 같은 지적을 여러 기관에 처분한 경우(예: 복지부·소방청에 각각 통보) 제목이 같아 상세 화면에서 묶어 보여 줍니다.
- 양식에 맞지 않는 문서는 `HEADING_PATTERNS` 예비 규칙으로 나눕니다.

## 로드맵

- [x] 1단계: 파서, 사건 분할, 분류, 키워드·필터 검색, 웹 화면
- [ ] 감사원 홈페이지 자동 수집기 (이용 조건 확인 후)
- [x] 의미 검색(임베딩) + 자연어 질의응답(원문 인용 필수)
- [x] 질문에서 연도·기관·금액·처분 조건 자동 추출
- [ ] 유사사례 추천, 위반유형별 통계 대시보드
- [ ] 감사 착안사항 체크리스트 생성
- [ ] 국가법령정보센터 Open API로 법조문 연결

## 테스트

```bash
pytest -q
# 실제 DB(pgvector) 통합 테스트
TEST_DATABASE_URL=postgresql://bai:bai@localhost:5432/bai_test pytest tests/test_integration_db.py
```
