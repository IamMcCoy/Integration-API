# 배포

네임스페이스는 `integration-api` 하나를 쓴다. Service 의 `selector` 는 네임스페이스를
넘지 못하므로, 매니페스트의 `namespace` 가 전부 같아야 한다.

## 적용 순서

```bash
kubectl create namespace integration-api

# 1. 비밀값 — 예시를 복사해 채운 뒤 적용한다 (ia-secret.yaml 은 gitignore 됨)
cp k8s/ia-secret.example.yaml k8s/ia-secret.yaml

#    db.properties 의 계정/비밀번호는 AES256 암호문이어야 한다 (DBPool 이 복호화)
PYTHONPATH=. python -m src.utils.AES256 integration change-me
#    출력 두 줄을 db.properties 의 username / password 에 넣고,
#    같은 평문을 integration-api-mariadb-secret 의 username / password 에 넣는다

kubectl apply -f k8s/ia-secret.yaml

# 2. 스키마 — sql/schema.sql 하나가 진실이다. 복붙하지 않고 파일에서 만든다
kubectl -n integration-api create configmap integration-api-mariadb-schema \
    --from-file=schema.sql=sql/schema.sql

# 3. 인프라
kubectl apply -f k8s/redis-deploy.yaml
kubectl apply -f k8s/mariadb-deploy.yaml

# 4. 앱
kubectl apply -f k8s/ia-configmap.yaml
kubectl apply -f k8s/ia-deploy.yaml
```

## 포트

| 대상 | 클러스터 내부 | NodePort |
|---|---|---|
| API | `integration-api-svc:8880` | `30880` |
| Redis | `integration-api-redis-svc:6379` | `30379` |
| MariaDB | `integration-api-mariadb-svc:3306` | `30306` |

NodePort 는 밖에서 들여다보기 위한 것이다. 앱은 전부 서비스 이름으로 붙는다.

**NodePort 는 기본 허용 범위가 `30000-32767` 이다.** 그래서 원래 포트의 뒷 세 자리를
`30000` 에 얹는 규칙으로 정했다 — `8880 → 30880`, `3306 → 30306`, `6379 → 30379`.
범위 밖 값을 쓰면 Deployment 는 생성되고 Service 만 거절당해서, 파드는 떠 있는데
접근 경로만 없는 상태가 된다.

## 로컬 k3d 로 돌려보기

매니페스트를 그대로 검증할 수 있다. 단계가 여럿이라 Makefile 로 묶어 뒀다.

```bash
make k3d-up      # 클러스터 생성 → Secret → 스키마 → MariaDB/Redis → 준비될 때까지 대기
make k3d-down    # 클러스터 삭제
```

**`k3d-up` 은 클러스터를 통째로 다시 만들므로 DB 데이터가 사라진다.** 데이터를 지키면서
잠시 내리려면 이쪽을 쓴다.

```bash
k3d cluster stop integration-api
k3d cluster start integration-api
```

수동으로 할 때 걸리는 것 두 가지:

```bash
# 1. NodePort 를 호스트로 빼내려면 클러스터를 만들 때 매핑해야 한다
k3d cluster create integration-api \
    -p "30306:30306@server:0" \
    -p "30379:30379@server:0" \
    -p "30880:30880@server:0"

# 2. 매니페스트의 nodeSelector 가 요구하는 라벨이 k3d 노드에는 없다.
#    매니페스트를 고치지 말고 노드에 라벨을 붙인다 (사내 클러스터에는 이 라벨이 있다).
kubectl label node k3d-integration-api-server-0 app=true master=true --overwrite
```

라벨을 안 붙이면 파드가 `Pending` 에 머문다. `kubectl describe pod` 의 Events 에
`didn't match Pod's node affinity/selector` 로 나온다.

**클러스터를 만든 직후 바로 붙으면 `socket was closed by server` 가 난다.**
k3d 는 `serverlb`(nginx)를 앞에 세우는데, 백엔드가 준비되기 전에는 연결을 수락한 뒤
곧바로 끊는다. `connection refused` 와 달리 "누군가는 듣고 있다" 는 뜻이므로 파드
상태부터 본다. `make k3d-up` 은 `rollout status` 로 기다렸다 끝나므로 이 문제가 없다.

macOS 에서는 시스템 슬립도 꺼야 한다. 잠들면 컨테이너가 멈춰 DB 연결이 끊긴다.

```bash
pmset -g custom | grep -w sleep     # 0 이 아니면 그 분 뒤에 잠든다
sudo pmset -c sleep 0
```

앱은 사내 레지스트리 이미지라 로컬에서 pull 되지 않는다 — `ia-deploy.yaml` 은 두고
앱만 호스트에서 `poetry run python src/Application.py` 로 띄우면 된다. 그때는
`.env` 에서 `IA_REDIS_HOST`/`IA_REDIS_PORT` 를 NodePort 로 돌려야 한다.

## 검토 UI 를 같은 오리진에 배포할 때

UI 라우트(`/tasks`, `/apis`)가 백엔드 API 경로와 동일하다. 인그레스가 경로만 보고
백엔드로 보내면 해당 화면에서 새로고침할 때 JSON 이 그대로 뜬다 (dev 프록시에서
curl 로 재현 확인). **`Accept: text/html` 요청은 SPA 로, 나머지는 백엔드로** 보내는
분기를 인그레스에 넣거나, 백엔드를 `/api/*` 프리픽스로 옮기는 것으로 해결한다.

## DB 계정

같은 계정을 두 곳에 다른 형태로 적어야 한다. 하나만 바꾸면 접속이 안 된다.

| 곳 | 형태 | 쓰는 주체 |
|---|---|---|
| `integration-api-mariadb-secret` 의 `username`/`password` | 평문 | MariaDB 가 첫 기동 때 계정 생성 |
| `integration-api-secret` 의 `db.properties` | AES256 암호문 | `DBPool` 이 복호화해서 접속 |

```bash
PYTHONPATH=. python -m src.utils.AES256 integration change-me
```

**계정은 데이터 디렉토리가 비어 있을 때만 만들어진다.** 이미 데이터가 있으면
Secret 을 바꿔도 계정은 그대로다. 비밀번호를 바꾸려면 둘 중 하나다.

```bash
# 데이터를 버리고 다시 만들거나
kubectl -n integration-api scale deploy/integration-api-mariadb --replicas=0
sudo rm -rf /raid/integration-api/data/mariadb/*
kubectl -n integration-api scale deploy/integration-api-mariadb --replicas=1

# 계정만 직접 고치거나
kubectl -n integration-api exec -it deploy/integration-api-mariadb -- \
    mariadb -uroot -p -e "ALTER USER 'integration'@'%' IDENTIFIED BY '<새 비밀번호>';"
```

## 확인

```bash
kubectl -n integration-api get pods
kubectl -n integration-api get endpoints          # 비어 있으면 selector/namespace 불일치
kubectl -n integration-api logs deploy/integration-api | grep 'Database connection verified'
curl http://<노드IP>:30880/health
```

`get svc` 는 Service 가 Pod 을 못 찾아도 정상으로 보인다. `get endpoints` 를 봐야
연결 여부가 드러난다.

앱은 기동할 때 DB 에 `SELECT 1` 을 한 번 던진다. 자격증명이 어긋났거나 스키마가
안 올라갔으면 **거기서 죽는다** — `CrashLoopBackOff` 가 뜨면 `logs` 를 먼저 본다.
이 확인이 없으면 오타가 `persisting` 단계까지 숨어 있다가
"`spec_count` 만 0 인 성공" 으로 나타난다.

## 주의

- **레플리카를 늘리기 전에 `upload_staging_dir` 을 ReadWriteMany 볼륨으로 바꿔야 한다.**
  지금은 hostPath 라 노드 로컬이다. POST 를 받은 파드와 워커 파드가 다르면 워커가
  파일을 못 찾는다. → [ADR-0002](../docs/adr/0002-task-scoped-staging-directory.md)
  처리량이 부족하면 레플리카 대신 `worker_count` 를 먼저 올린다.
- **스키마 ConfigMap 은 첫 기동에만 적용된다.** `/docker-entrypoint-initdb.d` 는
  데이터 디렉토리가 비어 있을 때만 실행된다. `sql/schema.sql` 을 바꿨으면 직접 적용한다.
  ```bash
  kubectl -n integration-api exec -i deploy/integration-api-mariadb -- \
      mariadb -uroot -p<비밀번호> integration_api < sql/schema.sql
  ```
- **설정을 바꾸면 파드를 재시작해야 한다.** `Constants` 가 import 시점에 값을 읽으므로
  ConfigMap 만 갈아끼워도 반영되지 않는다.
  ```bash
  kubectl -n integration-api rollout restart deploy/integration-api
  ```
- **`IA_` 접두사 환경변수가 ConfigMap 을 덮어쓴다.** 값 하나만 급히 바꿀 때는
  ConfigMap 대신 `ia-deploy.yaml` 의 `env` 에 넣는 게 빠르다.
- MariaDB 와 Redis 는 `hostPath` + `nodeSelector` 로 특정 노드에 묶여 있다.
  노드가 바뀌면 데이터가 따라오지 않는다.
