# Afterglow repro image: live agent loop, CPU-only, one command.
#   docker build -t afterglow . && docker run -e MODEL_API_KEY=... afterglow --case picosoc-uart-div
# Toolchain (verilator+g++) is cached in the image; pinned RTL is fetched
# at build time via the repo's own ensure path (no network at run time
# except the model API).
FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1
RUN apt-get update \
  && apt-get install -y --no-install-recommends git patch verilator g++ make \
  && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pillow rich==13.9.4
WORKDIR /afterglow
COPY afterglow afterglow
COPY afterglow_core/ ./afterglow_core/
COPY agent/ ./agent/
COPY sim/ ./sim/
COPY eval/ ./eval/
COPY repro/ ./repro/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY targets.pin foundation.pin PLAN.md AGENTS.md ./
RUN chmod +x repro/run.sh afterglow \
  && python3 -m py_compile agent/loop.py agent/visual.py agent/report.py agent/progress.py sim/vbackend.py \
  && echo COMPILE-OK
ENTRYPOINT ["./repro/run.sh"]
