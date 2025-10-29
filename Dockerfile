FROM python:3.11-slim-bookworm

# Metadatos
LABEL maintainer="tu-email@ejemplo.com"
LABEL description="Docling API - Versión oficial desde PyPI"

# Variables de entorno
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libgomp1 \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Crear directorio de trabajo
WORKDIR /app

# Instalar Docling y dependencias desde PyPI (versión oficial)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
    docling==2.0.0 \
    fastapi==0.115.0 \
    uvicorn[standard]==0.31.0 \
    python-multipart==0.0.9

# Copiar código de la aplicación
COPY app.py .

# Crear usuario no-root y directorios necesarios
RUN useradd -m -u 1000 docling && \
    mkdir -p /tmp/docling && \
    chown -R docling:docling /app /tmp/docling

# Cambiar a usuario no-root
USER docling

# Exponer puerto
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Comando de inicio
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]