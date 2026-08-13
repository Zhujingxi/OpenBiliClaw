FROM python:3.11-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

# Keep model serving outside the application image. The minimal Torch/server
# extras avoid Infinity's optional Optimum/ONNX/TensorRT dependency set.
RUN pip install 'infinity_emb[torch,server]==0.0.77' \
    && pip install --no-deps 'typer>=0.16,<1'

EXPOSE 7997

ENTRYPOINT ["infinity_emb"]
CMD ["v2", "--model-id", "BAAI/bge-small-zh-v1.5", "--port", "7997", "--no-bettertransformer"]
