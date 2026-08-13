DockerBuild = DOCKER_BUILDKIT=1 docker build
DockerPush = docker push
DockerRun = docker run

# Docker 이미지 이름 및 버전
IMAGE	:= registry.seculayer.com:31500/integration-api
VERSION	:= 0.1.0
NAME	:= integration-api

# run 대상용. CONF_DIR 은 ia-conf.xml 과 db.properties 가 든 호스트 디렉토리로 바꿔 쓴다.
CONF_DIR	:= $(PWD)/conf
CONTAINER_CONF_DIR := /opt/app/conf
# staging 을 볼륨으로 잡지 않으면 컨테이너 재시작 때 업로드 파일이 날아가는데,
# 외부 Redis 큐에는 태스크가 남아 워커가 없는 파일을 집다 죽는다.
STAGING_DIR	:= $(PWD)/.staging
CONTAINER_STAGING_DIR := /opt/app/staging
# Application 은 8880 으로 뜬다. 바꾸려면 Application.py 의 uvicorn.run 도 같이 고친다.
PORT		:= 38880:8880

# 로컬 k3d 클러스터
CLUSTER		:= integration-api
NAMESPACE	:= integration-api

.PHONY: setEnv build push-image run k3d-load k3d-up k3d-down

setEnv:
	@echo "${IMAGE}:${VERSION}"

build:
	${DockerBuild} -t ${IMAGE}:${VERSION} -f Dockerfile .

push-image:
	${DockerPush} ${IMAGE}:${VERSION}

# 로컬 빌드 이미지를 k3d 클러스터로 넣는다. 레지스트리 푸시 없이 배포할 때 쓴다.
# ia-deploy.yaml 의 imagePullPolicy: IfNotPresent 와 짝이다.
k3d-load:
	k3d image import ${IMAGE}:${VERSION} -c ${CLUSTER}

run:
	${DockerRun} --name ${NAME} \
		--env-file .env \
		-v ${CONF_DIR}:${CONTAINER_CONF_DIR} \
		-v ${STAGING_DIR}:${CONTAINER_STAGING_DIR} \
		-p ${PORT} \
		-t ${IMAGE}:${VERSION}

# 로컬 개발용 MariaDB / Redis 를 k3d 클러스터에 올린다.
# 클러스터를 통째로 다시 만들므로 DB 데이터는 사라진다.
# 데이터를 지키면서 잠시 내리려면 k3d cluster stop/start ${CLUSTER} 를 쓴다.
#
# 노드 라벨은 매니페스트의 nodeSelector 를 맞추기 위한 것이다. 매니페스트를 고치면
# 사내 클러스터와 갈리므로 노드 쪽에 붙인다.
k3d-up:
	-k3d cluster delete ${CLUSTER}
	k3d cluster create ${CLUSTER} \
		-p "30306:30306@server:0" \
		-p "30379:30379@server:0" \
		-p "30880:30880@server:0"
	kubectl label node k3d-${CLUSTER}-server-0 app=true master=true --overwrite
	kubectl create namespace ${NAMESPACE}
	kubectl apply -f k8s/ia-secret.yaml
	kubectl -n ${NAMESPACE} create configmap integration-api-mariadb-schema \
		--from-file=schema.sql=sql/schema.sql
	kubectl apply -f k8s/mariadb-deploy.yaml -f k8s/redis-deploy.yaml
	kubectl -n ${NAMESPACE} rollout status deploy/integration-api-mariadb --timeout=180s
	kubectl -n ${NAMESPACE} rollout status deploy/integration-api-redis --timeout=120s
	@echo
	@echo "MariaDB  10.1.11.121:30306  test / test@123  integration_api"
	@echo "Redis    10.1.11.121:30379"

k3d-down:
	k3d cluster delete ${CLUSTER}
