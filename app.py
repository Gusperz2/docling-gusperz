from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Security
from fastapi.security import APIKeyHeader
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.chunking import HybridChunker
import tempfile
import os
import logging
from typing import Optional
import pandas as pd
import io

# Configuración de logging estándar
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- INICIO DE LA SECCIÓN DE SEGURIDAD ---

API_KEY = os.getenv("DOCLING_API_KEY", "tu-clave-secreta-por-defecto")
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)

async def get_api_key(api_key_header: str = Security(api_key_header)):
    if api_key_header == API_KEY:
        return api_key_header
    else:
        raise HTTPException(status_code=403, detail="Clave de API inválida o no proporcionada.")

# --- FIN DE LA SECCIÓN DE SEGURIDAD ---

# Creación de la aplicación FastAPI con metadatos actualizados
app = FastAPI(
    title="Docling API Segura",
    description="API para procesamiento de documentos con Docling y soporte para Excel",
    version="3.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configuración de CORS para permitir todas las conexiones
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# Inicialización de DocumentConverter con la sintaxis corregida y actualizada
try:
    converter = DocumentConverter(
        allowed_formats=[
            InputFormat.PDF, InputFormat.DOCX, InputFormat.PPTX,
            InputFormat.HTML, InputFormat.IMAGE, InputFormat.ASCIIDOC, InputFormat.MD
        ],
        format_options={
            InputFormat.PDF: PdfFormatOption(do_ocr=True, do_table_structure=True)
        }
    )
    logger.info("DocumentConverter inicializado correctamente")
except Exception as e:
    logger.error(f"Error inicializando DocumentConverter: {e}")
    converter = None

@app.get("/")
async def root():
    """Endpoint raíz con información del servicio y endpoints disponibles."""
    return {
        "status": "healthy", "service": "Docling API", "version": "3.0.0",
        "endpoints": {
            "health": "/health", "docs": "/docs", "process": "/api/process",
            "process_rag": "/api/process-rag", "extract_tables": "/api/extract-tables",
            "process_excel": "/api/process-excel"
        }
    }

@app.get("/health")
async def health_check():
    """Health check para monitoreo por parte de Coolify."""
    return {"status": "ok", "converter_ready": converter is not None}

@app.post("/api/process-rag")
async def process_for_rag(
    file: UploadFile = File(...), chunk_size: int = Form(default=512),
    chunk_overlap: int = Form(default=50), merge_peers: bool = Form(default=True),
    api_key: str = Security(get_api_key) # <-- Protección añadida
):
    """Procesa un documento estándar (PDF, DOCX, etc.) y lo divide en chunks para RAG."""
    if converter is None: raise HTTPException(status_code=503, detail="Converter no inicializado")
    tmp_path = None
    try:
        content = await file.read()
        if len(content) / (1024 * 1024) > 50: raise HTTPException(status_code=413, detail="Archivo muy grande (máx 50MB)")
        
        suffix = os.path.splitext(file.filename)[1].lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="/tmp/docling") as tmp:
            tmp.write(content); tmp_path = tmp.name
        
        doc = converter.convert(tmp_path).document
        chunker = HybridChunker(max_tokens=chunk_size, overlap=chunk_overlap, merge_peers=merge_peers)
        
        rag_chunks = []
        for idx, chunk in enumerate(chunker.chunk(doc)):
            pages = sorted(list(set(p.prov[0].page_no for p in chunk.meta.doc_items if p.prov)))
            element_types = list(set(e.label for e in chunk.meta.doc_items))
            rag_chunk = {"chunk_id": f"{file.filename}_rag_{idx}", "text": chunk.text, "metadata": {"filename": file.filename, "chunk_index": idx, "pages": pages, "element_types": element_types}}
            rag_chunks.append(rag_chunk)
        
        metadata = {"filename": file.filename, "total_pages": len(doc.pages), "total_chunks": len(rag_chunks)}
        return JSONResponse(content={"success": True, "metadata": metadata, "chunks": rag_chunks})
    except Exception as e:
        logger.error(f"Error en process-rag: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path): os.unlink(tmp_path)

@app.post("/api/process-excel")
async def process_excel_for_rag(
    file: UploadFile = File(...),
    api_key: str = Security(get_api_key) # <-- Protección añadida
):
    """Procesa un archivo de Excel (.xlsx) y convierte cada fila en un chunk para RAG."""
    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="El archivo debe ser un .xlsx o .xls")
    try:
        content = await file.read()
        df = pd.read_excel(io.BytesIO(content))
        
        rag_chunks = []
        for idx, row in df.iterrows():
            row_text = ", ".join([f"{col}: {val}" for col, val in row.items() if pd.notna(val)])
            if not row_text: continue
            rag_chunk = {"chunk_id": f"{file.filename}_row_{idx}", "text": row_text, "metadata": {"filename": file.filename, "row": idx + 2}}
            rag_chunks.append(rag_chunk)
            
        metadata = {"filename": file.filename, "total_rows": len(df), "total_chunks": len(rag_chunks)}
        return JSONResponse(content={"success": True, "metadata": metadata, "chunks": rag_chunks})
    except Exception as e:
        logger.error(f"Error en process-excel: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error procesando el archivo Excel: {str(e)}")

@app.post("/api/process")
async def process_document(
    file: UploadFile = File(...),
    api_key: str = Security(get_api_key) # <-- Protección añadida
):
    """Esta es una versión simplificada del endpoint original para mantener la funcionalidad."""
    return await process_for_rag(file)

@app.post("/api/extract-tables")
async def extract_tables_only(
    file: UploadFile = File(...),
    api_key: str = Security(get_api_key) # <-- Protección añadida
):
    """Extrae únicamente las tablas de un documento."""
    if converter is None: raise HTTPException(status_code=503, detail="Converter no inicializado")
    tmp_path = None
    try:
        content = await file.read()
        suffix = os.path.splitext(file.filename)[1].lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="/tmp/docling") as tmp:
            tmp.write(content); tmp_path = tmp.name
        
        doc = converter.convert(tmp_path).document
        tables = [{"page": page.page_no, "text": table.text} for page in doc.pages for table in page.tables]
        return JSONResponse(content={"success": True, "filename": file.filename, "total_tables": len(tables), "tables": tables})
    except Exception as e:
        logger.error(f"Error en extract-tables: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path): os.unlink(tmp_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)