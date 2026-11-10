# 감사원 결정례 검색 — 설치와 사용 안내

처음 설치하는 분을 위한 단계별 안내입니다. 명령은 **Windows 명령 프롬프트(cmd)** 기준이고,
macOS에서 다른 부분은 따로 적었습니다.

---

## 0. 먼저 알아 둘 것

**할 수 있는 일**

| 기능 | 설명 | 인터넷·비용 |
|---|---|---|
| 자료 넣기 | 감사원 처분요구서(PDF·HWP·HWPX·ZIP)를 읽어 사건 단위로 저장 | 없음 |
| 검색 | 키워드 + 의미 검색, 질문 속 조건(연도·금액·처분·기관) 자동 인식 | 없음 |
| 유사사례 | 비슷한 위반 사례와 그 처분 비교 | 없음 |
| 통계 | 위반유형·연도·처분·기관별 집계 화면 | 없음 |
| AI 분류 | 위반유형·금액·관련 법령·요약을 AI가 채움 | **Anthropic API, 유료** |
| 질문하기 | 자연어 질문에 원문을 인용해 답변 | **Anthropic API, 유료** |

**보안 주의**

- **공개된 감사결과만** 넣으세요. AI 분류·질문하기를 쓰면 원문이 Anthropic(미국) 서버로 전송됩니다.
  비공개 자료는 기관의 생성형 AI 보안 지침을 먼저 확인하세요.
- 의미 검색(임베딩)은 PC 안에서만 계산하므로 원문이 밖으로 나가지 않습니다.
- 웹 화면에는 **로그인 기능이 없습니다.** 기본 설정대로 내 PC에서만 열리게 두세요(아래 9번 참고).

---

## 1. 준비물

| 준비물 | 용도 | 받는 곳 |
|---|---|---|
| Python 3.12 (3.11 이상) | 프로그램 실행 | https://www.python.org/downloads/ — 설치 첫 화면에서 **"Add python.exe to PATH" 체크** |
| Docker Desktop | 데이터베이스(PostgreSQL + pgvector) 실행 | https://www.docker.com/products/docker-desktop/ — 설치에 관리자 권한 필요 |
| Git (선택) | 소스 내려받기·업데이트 | https://git-scm.com/ — 없으면 GitHub에서 ZIP으로 받아도 됨 |
| Anthropic API 키 (선택) | AI 분류·질문하기 | https://console.anthropic.com — 결제 수단 등록 필요 |

**설치할 때 필요한 인터넷 주소** (내부망이면 방화벽 확인)

- `pypi.org`, `files.pythonhosted.org` — 파이썬 패키지
- `huggingface.co` — 의미 검색 모델(약 2.3GB, 처음 한 번)
- Docker Hub (`registry-1.docker.io` 등) — 데이터베이스 이미지
- 사용 중: `api.anthropic.com` — AI 분류·질문하기를 쓸 때만

디스크는 **여유 10GB 이상**을 권장합니다(모델·라이브러리 약 5~6GB).

> 회사 PC에 Docker를 설치할 수 없으면 정보화 담당에 PostgreSQL 16 + pgvector 설치를 요청하고,
> 4단계의 `DATABASE_URL`만 그 주소로 바꾸면 됩니다.

---

## 2. 소스 내려받기

명령 프롬프트를 열고 작업할 폴더로 이동한 뒤:

```cmd
cd %USERPROFILE%\Documents
git clone https://github.com/Seungjun-Lee-KR/bai-search.git
cd bai-search
```

Git이 없으면 GitHub 저장소 화면의 **Code → Download ZIP**으로 받아 압축을 풀고 그 폴더로 이동합니다.
(비공개 저장소라 GitHub 로그인이 필요합니다.)

**이후 모든 명령은 이 `bai-search` 폴더에서 실행합니다.**

---

## 3. 데이터베이스 켜기

Docker Desktop을 실행해 두고:

```cmd
docker compose up -d
docker compose ps
```

`db` 항목이 `running`이면 됩니다. 처음 실행할 때 표(테이블)가 자동으로 만들어집니다.
PC를 껐다 켜면 Docker Desktop만 켜져 있으면 데이터베이스도 다시 올라옵니다(안 올라오면 `docker compose up -d`).

> PC에 이미 PostgreSQL이 설치되어 5432 포트를 쓰고 있으면 오류가 납니다.
> `docker-compose.yml`의 `"5432:5432"`를 `"5433:5432"`로 바꾸고, 4단계에서 주소의 `5432`를 `5433`으로 바꾸세요.

---

## 4. 프로그램 설치

```cmd
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e ".[local]"
```

- 두 번째 줄 이후 명령 프롬프트 앞에 `(.venv)`가 붙으면 가상환경이 켜진 것입니다.
  **새 창을 열 때마다 `.venv\Scripts\activate`를 먼저 실행**해야 `bai` 명령이 동작합니다.
- 마지막 줄은 의미 검색 모델용 라이브러리까지 설치해서 **수 GB, 10분 이상** 걸릴 수 있습니다.
- macOS: `python3 -m venv .venv` → `source .venv/bin/activate` → `pip install -e ".[local]"`

**설정 파일 만들기**

```cmd
copy .env.example .env
notepad .env
```

메모장에서 다음을 확인·수정하고 저장합니다.

```ini
ANTHROPIC_API_KEY=sk-ant-...      # AI 분류·질문하기를 쓸 때만. 없으면 비워 둠
CLASSIFY_MODEL=claude-opus-5-5    # 분류 모델. 비용을 줄이려면 claude-haiku-5-5
ASK_MODEL=claude-opus-5-5         # 질문하기 모델
DATABASE_URL=postgresql://bai:bai@localhost:5432/bai
EMBED_BACKEND=local               # 의미 검색. 그대로 두세요
DATA_DIR=data
```

`.env`에는 API 키가 들어가므로 다른 사람에게 보내거나 GitHub에 올리지 마세요(자동으로 제외되도록 설정되어 있습니다).

**설치 확인**

```cmd
bai --help
```

명령 목록(ingest, watch, classify, embed, ask, similar, serve)이 나오면 성공입니다.

---

## 5. 처음 사용하기 — 자료 넣고 화면 열기

### 5-1. 감사원 홈페이지에서 자료 내려받기

감사원 홈페이지 → 감사결과 → 원하는 감사의 **처분요구서·통보서 파일**을 내려받습니다.

- **PDF를 권장합니다.** 배포용(암호화) HWP는 읽지 못합니다.
- 스캔본 PDF(글자가 그림인 파일)도 읽지 못합니다.
- 홈페이지 규정(robots.txt)상 목록 자동 수집은 하지 않으므로, 파일은 직접 내려받습니다.

### 5-2. 프로그램에 넣기 (둘 중 편한 방법)

**방법 A — 폴더에 넣기** (파일이 많을 때)

```cmd
bai watch
```

`data\inbox` 폴더에 파일을 넣으면 30초 안에 자동 처리됩니다. 끝내려면 `Ctrl+C`.
브라우저의 다운로드 폴더를 `data\inbox`로 바꿔 두면 내려받는 즉시 처리됩니다.

처리된 원본은 `data\processed\날짜\`로, 읽지 못한 파일은 사유 파일과 함께 `data\failed\`로 옮겨집니다.
처리 기록은 `data\processed\수집기록.csv`(엑셀로 열림)에 쌓입니다.

**방법 B — 웹 화면에서 올리기** — 5-4에서 화면을 연 뒤 **자료 추가** 탭에 파일을 끌어다 놓습니다.

**공개일·감사명 붙이기** (연도 검색과 통계에 쓰임, 선택)

- 파일명에 날짜를 넣기: `2026-06-02_법원 회계 관리실태.pdf`
- 또는 `data\inbox`에 목록 CSV를 함께 넣기 (엑셀에서 그냥 CSV로 저장해도 됨)

  | 파일명 | 공개일 | 감사명 |
  |---|---|---|
  | 처분요구서1.pdf | 2026-06-02 | 법원 회계 관리실태 |

- 또는 웹 화면 자료 추가 탭에서 직접 입력

같은 파일을 두 번 넣어도 한 번만 저장됩니다.

### 5-3. AI 분류 (선택, 유료)

위반유형·금액·기관유형·요약은 AI 분류를 해야 채워집니다. 통계 화면과 금액·기관유형 검색 조건도 분류 후에 동작합니다.

```cmd
bai classify --limit 5
```

**처음엔 5건만** 돌려서 결과를 보고, 괜찮으면 `--limit` 숫자를 늘려 나머지를 처리합니다.
확신도가 낮은 건은 화면에 **"분류 검수 필요"**로 표시됩니다.

**비용 어림** (추정치 — 실제 금액은 https://console.anthropic.com 사용량 화면에서 확인)

| 작업 | Opus 5.5 (기본) | Haiku 5.5 |
|---|---|---|
| 분류 1건 (원문 약 7천 자) | 약 50~100원 | 약 2~5원 |
| 질문하기 1회 (사례 6건 원문 전송) | 약 150~300원 | — (기본 Opus 권장) |

### 5-4. 웹 화면 열기

```cmd
bai serve
```

브라우저에서 **http://localhost:8000** 을 엽니다. 끌 때는 명령 창에서 `Ctrl+C`.

---

## 6. 화면별 사용법

### 검색 탭

- 검색어를 넣으면 **키워드 + 의미 검색**을 합쳐 찾습니다. 원문과 다른 표현("보관하던 돈을 빼돌린")도 찾습니다.
- 검색어 속 조건을 자동으로 알아듣습니다: `2024년 이후 지자체 1억 원 이상 횡령 징계`
  → 화면에 "질문에서 찾은 조건"이 뜨고, **조건 없이 다시 찾기**로 풀 수 있습니다.
- 위반유형·처분·기관·연도·금액 칸으로 직접 걸러도 됩니다(직접 고른 값이 우선).
- 결과를 누르면 조치할 사항, 원문, 관련 법령, **비슷한 사례**가 펼쳐집니다.

### 질문하기 탭 (API 키 필요)

- 예: "이미 계약에 포함된 과업을 추가 과업으로 돈을 더 준 사례와 처분 수위를 알려줘"
- 검색된 사례 원문만 근거로 답하고, 문장마다 **[1] [2] 근거 번호**가 붙습니다. 번호를 누르면 인용 원문으로 이동합니다.
- AI 답변은 참고용입니다. 반드시 근거 원문을 확인하세요.

### 자료 추가 탭

파일을 끌어다 놓고 **올리기**. 한 번에 올린 파일들에는 같은 공개일·감사명이 붙으므로 다른 감사 건은 나눠서 올리세요.
아래 "최근 저장한 문서"에서 임베딩·분류 대기 현황을 볼 수 있습니다.

### 통계 탭

요약 지표, 연도별 추이, 위반유형별·처분별·기관별 건수, **위반유형별 처분 수위**(같은 위반에 어떤 처분이 나왔는지), 금액 상위 사례.
막대를 누르면 그 조건의 사례 검색으로 넘어갑니다. 금액·위반유형은 AI 분류 결과이므로 원문 확인이 필요합니다.

---

## 7. 명령어 모음

| 명령 | 하는 일 |
|---|---|
| `bai watch` | `data\inbox` 감시, 새 파일 자동 처리 (`--once` 한 번만, `--classify` 분류까지) |
| `bai ingest 폴더` | 폴더 안 파일을 옮기지 않고 한 번에 저장 (`--force` 다시 처리) |
| `bai classify --limit 20` | 미분류 사건 AI 분류 |
| `bai embed` | 의미 검색 임베딩 생성 (모델 설치 전에 넣은 자료용, `--all` 전부 다시) |
| `bai ask "질문"` | 명령 창에서 질문하기 |
| `bai similar 12` | 사건 #12와 비슷한 사례 |
| `bai serve` | 웹 화면 (`--port 8001` 다른 포트) |

---

## 8. 매일 쓰는 순서

```cmd
cd %USERPROFILE%\Documents\bai-search
.venv\Scripts\activate
bai serve
```

(Docker Desktop이 켜져 있어야 합니다.) 새 자료가 있으면 다른 명령 창에서 `bai watch`를 함께 켜 둡니다.

---

## 9. 문제가 생겼을 때

| 증상 | 해결 |
|---|---|
| `'bai'은(는) 내부 또는 외부 명령...이 아닙니다` | 가상환경이 꺼져 있음. `.venv\Scripts\activate` |
| `connection refused` / DB 연결 오류 | Docker Desktop 실행 후 `docker compose up -d` |
| `[중단] API 키가 없습니다` | `.env`의 `ANTHROPIC_API_KEY` 입력 후 명령 창을 새로 열기 |
| `Anthropic 서버에 연결하지 못했습니다` | 방화벽에서 `api.anthropic.com` 허용 필요 |
| `임베딩 생략: 의미 검색 모델이 설치되지 않았습니다` | `pip install -e ".[local]"` 후 `bai embed`. 그동안 키워드 검색은 됨 |
| 모델 내려받기가 막힘(내부망) | 인터넷 되는 PC에서 `pip install huggingface_hub` → `huggingface-cli download BAAI/bge-m3 --local-dir bge-m3`(새 버전은 `hf download`), 폴더를 복사한 뒤 `.env`에 `EMBED_MODEL=C:\경로\bge-m3` |
| `배포용(암호화) 문서는 지원하지 않습니다` | 같은 자료를 PDF로 받기 |
| `텍스트 층이 없습니다(스캔본)` | OCR이 필요한 스캔본. 현재 미지원 |
| `8000 포트 사용 중` | `bai serve --port 8001` 후 http://localhost:8001 |
| 동료와 같이 쓰고 싶음 | `bai serve --host 0.0.0.0` 로 열 수 있지만 **로그인이 없어 누구나 접근** 가능. 기관 내부망에서만, 담당 부서 확인 후 |

---

## 10. 업데이트와 백업

**새 버전 받기**

```cmd
git pull
.venv\Scripts\activate
pip install -e ".[local]"
docker compose cp db/schema.sql db:/tmp/schema.sql
docker compose exec db psql -U bai -d bai -f /tmp/schema.sql
```

마지막 두 줄은 표 구조를 최신으로 맞춥니다. 기존 자료는 그대로 유지됩니다(여러 번 실행해도 안전).

**백업**

```cmd
docker compose exec db pg_dump -U bai -Fc -f /tmp/bai.dump bai
docker compose cp db:/tmp/bai.dump bai.dump
```

**복원**

```cmd
docker compose cp bai.dump db:/tmp/bai.dump
docker compose exec db pg_restore -U bai -d bai --clean /tmp/bai.dump
```

원본 파일은 `data\processed\`에 남아 있으므로, DB를 새로 만들어도 `bai ingest data\processed`로 다시 넣을 수 있습니다.

**완전히 지우기**: `docker compose down -v` (DB 자료가 모두 삭제됩니다. 주의)
