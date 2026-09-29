# 零售客服 Agent（前后端一体）。约定与 Innocellence-Gaias-Hub/aichat-api/Dockerfile.local 一致：
# 基础镜像 python:3.13.11-slim；构建机连不上 pypi.org，一律走阿里云镜像；
# planex-core / retail_skill 以预构建的 wheel 装入（wheels/），镜像里不带源码树。
FROM python:3.13.11-slim

WORKDIR /app
ENV PIP_ROOT_USER_ACTION=ignore \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

COPY wheels/ /app/wheels/
COPY requirements.txt /app/requirements.txt
RUN pip install -i https://mirrors.aliyun.com/pypi/simple/ --find-links /app/wheels \
        planex-core retail-skill -r /app/requirements.txt \
    && rm -rf /app/wheels

# 非 root 运行；sessions/ 是每个会话的工作目录（Agent 的 execute 子进程会在里面写脚本与数据副本）
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/sessions && chown -R app:app /app/sessions

COPY --chown=app:app app/ /app/app/
COPY --chown=app:app static/ /app/static/
COPY --chown=app:app data/ /app/data/

USER app
ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/api/config' % os.environ.get('PORT','8000'), timeout=4)"

CMD ["sh", "-c", "exec uvicorn app.server:app --host 0.0.0.0 --port ${PORT}"]
