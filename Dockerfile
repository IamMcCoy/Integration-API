## builder / 1단계 : 의존성 설치
FROM python:3.11-slim AS builder
WORKDIR /opt/app

# poetry 는 빌드 단계에서만 쓴다. 앱 의존성은 .venv 로 분리해 실행 이미지로 복사한다.
RUN pip install --no-cache-dir poetry

COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.in-project true && \
    poetry install --only main --no-root --no-interaction --no-ansi

## app / 2단계 : 실행 환경
FROM python:3.11-slim AS app
WORKDIR /opt/app

# opendataloader-pdf 는 동봉된 jar 를 java 로 실행한다. JRE 가 없으면 변환 단계에서 죽는다.
RUN apt-get update && \
    apt-get install -y --no-install-recommends default-jre-headless && \
    rm -rf /var/lib/apt/lists/*

# conf/ 는 ConfigMap+Secret 마운트 지점, staging/ 은 업로드 착지 기본 경로.
RUN groupadd -g 1000 app && \
    useradd -m -u 1000 -g app app && \
    mkdir -p /opt/app/conf /opt/app/staging && \
    chown -R app:app /opt/app

COPY --chown=app:app --from=builder /opt/app/.venv .venv
COPY --chown=app:app src src

# `python src/Application.py` 는 src/ 를 sys.path 에 올리므로 `from src...` import 를 위해
# 앱 루트를 PYTHONPATH 에 넣는다.
ENV PATH="/opt/app/.venv/bin:$PATH" \
    PYTHONPATH="/opt/app"

USER app

CMD ["python", "src/Application.py"]
