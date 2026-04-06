FROM python:3.11-slim

# Pipeline dependencies — CPU-only (no nvidia-nccl-cu12)
RUN pip install --no-cache-dir \
    pandas==2.2.* \
    numpy==1.26.* \
    scikit-learn==1.5.* \
    joblib==1.4.* \
    matplotlib==3.9.* \
    pyarrow==17.* \
    scipy \
    requests && \
    pip install --no-cache-dir --no-deps xgboost==2.1.*

# Bake task and simulation scripts into the image
COPY tasks/ /app/tasks/
COPY scripts/ /app/scripts/
COPY data/scenarios/ /app/data/scenarios/

WORKDIR /app
