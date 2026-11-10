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
| `src/bai_search/api.py`, `index.html` | 검색 API와 웹 화면 |
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

# 5) 검색 화면
bai serve   # http://localhost:8000
```

분류 모델은 `.env`의 `CLASSIFY_MODEL`로 바꿉니다. 기본값은 `claude-opus-5-5`이고,
수천 건을 돌릴 때 비용을 줄이려면 `claude-haiku-5-5`로 바꿔 품질을 비교해 보세요.

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
- [ ] 의미 검색(임베딩) + 자연어 질의응답(원문 인용 필수)
- [ ] 유사사례 추천, 위반유형별 통계 대시보드
- [ ] 감사 착안사항 체크리스트 생성
- [ ] 국가법령정보센터 Open API로 법조문 연결

## 테스트

```bash
pytest -q
```
