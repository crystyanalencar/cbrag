# Imagem de produção da interface web (Chainlit). Não empacota dados:
# `data/knowledge_storage/` (índice Qdrant, gerado por `uv run ingest`) e
# `.env` (segredo) ficam fora da imagem — montados como volume/env_file no
# deploy (ver docker-compose.yml). Chat usa só BM25 em runtime, sem
# Ollama/GPU necessário aqui — geração é via OpenRouter (API).
FROM python:3.12-slim

# uv instalado via imagem oficial (multi-stage copy), sem precisar de curl/pip
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Camada de dependências separada do código: cache do Docker só invalida
# aqui quando pyproject.toml/uv.lock mudam, não a cada edição de código.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
COPY chainlit.md ./
COPY .chainlit/ .chainlit/
COPY public/ public/
RUN uv sync --frozen --no-dev

# Não rodar como root. uid 1000 (padrão do 1º usuário não-root em toda
# distro comum) evita problema de permissão nos bind mounts de dados
# (data/logs, data/knowledge_storage...) — se o host usa outro uid pro
# dono desses diretórios, ajuste aqui ou dê permissão de escrita a esse uid.
# HOME próprio porque crewai/chainlit gravam config em ~/.config.
RUN useradd --uid 1000 --create-home app && chown -R app:app /app
USER app
ENV HOME=/home/app

EXPOSE 8000

CMD ["uv", "run", "chainlit", "run", "src/casas_bahia_rag/chainlit_app.py", \
     "--host", "0.0.0.0", "--port", "8000", "--headless"]
