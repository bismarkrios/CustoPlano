# Imagem para publicar no Render (ou qualquer servidor com Docker).
# Já inclui o Java, necessário para ler os arquivos .mpp.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
# Memória do Java limitada para caber no plano gratuito (512 MB)
ENV JAVA_TOOL_OPTIONS="-Xmx300m" CP_BANCO=/dados/custoplano.db
RUN mkdir -p /dados
EXPOSE 8000
CMD ["sh", "-c", "gunicorn -w 1 --threads 4 -b 0.0.0.0:${PORT:-8000} --timeout 180 app:app"]
