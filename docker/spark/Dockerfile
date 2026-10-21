# Spark + Delta Lake 실행 이미지 (README 4 단계). docker-compose.yml 프로필 spark 가 만든다.
#
# [이유] Windows 에서 Spark 로 파일을 쓰려면 winutils.exe 가 따로 필요해 자주 깨진다.
#        공식 리눅스 이미지 안에서 돌리면 어느 PC 에서나 같은 환경이다.
# 버전: Spark 4.0.1 (Scala 2.13 · Java 17) + Delta 4.0.1 — Delta 4.0.x 는 Spark 4.0.x 짝.
#        (pypi delta-spark 최신 4.4.x 는 더 새 Spark 를 요구한다, 2026-10-21 확인)
# 파이썬: 이미지 기본은 3.10 인데 이 저장소 코드는 3.11 이상(StrEnum 등)이라 3.12 를 따로 넣고
#         Spark 드라이버 · 작업자 모두 그 파이썬을 쓰게 한다(PYSPARK_PYTHON).

FROM apache/spark:4.0.1-scala2.13-java17-python3-ubuntu

USER root

# 파이썬 3.12 (deadsnakes) + 전용 가상환경 /opt/mdp-venv.
RUN apt-get update \
 && apt-get install -y --no-install-recommends software-properties-common gpg-agent \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && apt-get update \
 && apt-get install -y --no-install-recommends python3.12 python3.12-venv \
 && python3.12 -m venv /opt/mdp-venv \
 && apt-get purge -y software-properties-common gpg-agent && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# Delta 자바 라이브러리 — Maven 공식 저장소, 올린 SHA-1 로 대조한다.
RUN cd /opt/spark/jars \
 && curl -fsSLO https://repo1.maven.org/maven2/io/delta/delta-spark_2.13/4.0.1/delta-spark_2.13-4.0.1.jar \
 && curl -fsSLO https://repo1.maven.org/maven2/io/delta/delta-storage/4.0.1/delta-storage-4.0.1.jar \
 && echo "3e469898dc4ed083c735ae360beb063adcd12e9d  delta-spark_2.13-4.0.1.jar" | sha1sum -c - \
 && echo "ebc5384d953c1d70d0ce4ceee42892da74051a81  delta-storage-4.0.1.jar" | sha1sum -c -

# 파이썬 쪽: Delta API(delta.tables) · 실행 기록용 PostgreSQL 드라이버 · 받기 모듈이 쓰는 httpx ·
# 컨테이너 안 시험용 pytest. --no-deps: delta-spark 가 pyspark 를 또 받지 않게(이미지 안 것을 쓴다).
RUN /opt/mdp-venv/bin/pip install --no-cache-dir --no-deps "delta-spark==4.0.1" \
 && /opt/mdp-venv/bin/pip install --no-cache-dir "importlib-metadata>=1.0.0" \
      "psycopg[binary]==3.2.10" "httpx==0.28.1" "pytest==8.4.2"

# 우리 코드(/app/src, compose 가 연결) + 이미지 안 pyspark · py4j.
ENV PATH=/opt/mdp-venv/bin:$PATH \
    PYTHONPATH=/app/src:/opt/spark/python:/opt/spark/python/lib/py4j-0.10.9.9-src.zip \
    PYSPARK_PYTHON=/opt/mdp-venv/bin/python \
    PYSPARK_DRIVER_PYTHON=/opt/mdp-venv/bin/python

# 임시 폴더(압축 푼 CSV) — compose 가 이름 있는 볼륨을 여기 붙인다. 볼륨은 처음 붙을 때 이 폴더의
# 주인을 물려받으므로, spark 사용자가 쓸 수 있게 미리 만들어 둔다.
RUN mkdir -p /staging && chown spark:spark /staging

WORKDIR /app
USER spark
