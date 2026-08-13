# integration-api

PDF(벤더 REST API 가이드 문서)를 업로드하면 API 규격 — 요청/응답 파라미터,
타입 등 — 을 추출해 MariaDB 에 적재하는 파이프라인.

```
PDF 업로드 → 마크다운 변환 → 청킹 → API 청크 선별(LLM) → 규격 추출(LLM) → 채점 → MariaDB 적재
```

추출 결과는 곧바로 운영 테이블로 가지 않는다. 파라미터마다 딸려 온 근거(evidence)가
원문에 실재하는지 대조해 점수를 매기고, 임계값을 넘은 것만 자동 적재한다. 나머지는
사람이 검토 API 로 승인·수정·반려한다.

## 빠른 시작

```bash
# 1. 의존성
poetry install

# 2. 로컬 인프라 (k3d 클러스터에 MariaDB + Redis, 스키마 자동 적용)
cp k8s/ia-secret.example.yaml k8s/ia-secret.yaml   # 비밀값 채우기
make k3d-up

# 3. 설정
cp .env.example .env          # OPENAI_API_KEY 등을 채운다

# 4. 실행 — 둘 중 하나
poetry run python src/Application.py               # 호스트에서 직접 (:8880)
make build && make k3d-load \
  && kubectl apply -f k8s/ia-configmap.yaml -f k8s/ia-deploy.yaml   # 파드로 (:30880)
```

업로드부터 검토까지 전부 HTTP 다. Swagger UI 는 `/docs`.

```bash
curl -X POST localhost:8880/documents -F "file=@guide.pdf" \
  -F "vendor_name=SECUI" -F "device_name=MFD" -F "api_version=2.0"
curl localhost:8880/tasks/<task_id>                 # 진행 상황
curl localhost:8880/extractions?status=pending      # 검토 대기열
curl localhost:8880/apis                            # 적재된 운영 API
```

전체 엔드포인트와 응답 스키마는 [docs/design/api.md](docs/design/api.md).

## 검증과 린트

테스트 프레임워크 없이 모듈별 자체 점검을 쓴다 — 모듈을 직접 실행하면 `assert` 가 돈다.

```bash
PYTHONPATH=. python -m src.core.chunker.MarkdownChunker
PYTHONPATH=. python -m src.core.extractor.SpecScorer
# ... 전체 목록은 CLAUDE.md

pre-commit run --all-files    # flake8 / mypy / autopep8 등
```

## 더 읽을 것

| 문서 | 내용 |
|---|---|
| [docs/overview.md](docs/overview.md) | 프로젝트 범위 + 현재 구현 상태 |
| [docs/architecture/](docs/architecture/README.md) | 계층, 데이터 흐름, 확장 지점 |
| [docs/design/](docs/design/pipeline.md) | 파이프라인·API·DB 설계 |
| [docs/adr/](docs/adr/README.md) | 되돌리기 비싼 결정들과 그 이유 |
| [docs/experiments/](docs/experiments/) | 실측 기록 (재현 스크립트 포함) |
| [k8s/README.md](k8s/README.md) | 배포 순서, 포트, 레플리카 주의사항 |

---
Powered by Seculayer © 2026 AI Team, R&D Center.
