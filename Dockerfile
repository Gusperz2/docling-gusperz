# Usa una imagen base de Python 3.11 slim, moderna y compatible con ARM64
FROM python:3.11-slim-bookworm

# Metadatos de la imagen
LABEL maintainer="Gusperz2"
LABEL description="API para Docling con soporte multi-formato (PDF, Office, Excel)"

# Variables de entorno para optimizar la ejecución de Python en Docker
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

# Instala las dependencias del sistema operativo de forma eficiente
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libgomp1 \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Establece el directorio de trabajo
WORKDIR /app

# Actualiza pip e instala las librerías de Python.
# Se instalan las últimas versiones estables para asegurar compatibilidad.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
    "docling[pdf,ocr,docx,pptx]" \
    "fastapi" \
    "uvicorn[standard]" \
    "python-multipart" \
    "pandas" \
    "openpyxl"

# Copia el código de la aplicación al contenedor
COPY app.py .

# Crea un usuario no-root por seguridad y asigna los permisos necesarios.
# Esta es la solución definitiva para el error de permisos [Errno 13].
RUN useradd -m -u 1000 docling && \
    mkdir -p /tmp/docling && \
    chown -R docling:docling /app /tmp/docling && \
    chown -R docling:docling /usr/local/lib/python3.11/site-packages

# Cambia al usuario no-root para ejecutar la aplicación
USER docling

# Expone el puerto que usará la API
EXPOSE 8000

# Define un chequeo de salud robusto para que Coolify monitoree la aplicación
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Comando para iniciar el servidor de la aplicación
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]