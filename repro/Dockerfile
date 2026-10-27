# Afterglow repro image: live agent loop, CPU-only, one command.
#   docker build -t afterglow . && docker run -e MODEL_API_KEY=... afterglow --case picosoc-uart-div
# Toolchain (verilator+g++) is cached in the image; the frozen Weft RTL
# (targets.pin) is pre-cloned at build time so cold runs skip the fetch.
FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1
RUN apt-get update \
  && apt-get install -y --no-install-recommends git patch verilator g++ make \
  && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pillow
WORKDIR /afterglow
COPY agent/ ./agent/
COPY sim/ ./sim/
COPY eval/ ./eval/
COPY repro/ ./repro/
COPY scripts/ ./scripts/
COPY targets.pin foundation.pin PLAN.md AGENTS.md ./
RUN chmod +x repro/run.sh \
  && python3 -m py_compile agent/loop.py agent/visual.py agent/report.py sim/sim.py \
  && echo COMPILE-OK
RUN python3 sim/vbackend.py --inject picosoc-uart-div
ENTRYPOINT ["./repro/run.sh"]
