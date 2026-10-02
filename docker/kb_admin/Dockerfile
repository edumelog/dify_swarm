# Imagem do kb_admin: interface web de publicação das imagens da base de conhecimento.
# Roda como root porque escreve no volume dify_kb_assets, cujos arquivos antigos
# (publicados pelo publish.sh) pertencem ao root.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS test
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

FROM base AS runtime
COPY kb_admin ./kb_admin
EXPOSE 8000
CMD ["uvicorn", "kb_admin.main:build_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
