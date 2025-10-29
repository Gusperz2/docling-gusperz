from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.chunking import HybridChunker
import tempfile
import os
import logging
from typing import Optional
# --- INICIO DE CAMBIOS ---
import pandas as pd
import io
# --- FIN DE CAMBIOS ---

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Crear aplicación FastAPI
app = FastAPI(
    title="Docling API",
    description="API para procesamiento de documentos con Docling y soporte para Excel",
    version="1.2.0", # <-- VERSIÓN ACTUALIZADA
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configurar CORS (ajusta según tus necesidades)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Cambiar en producción
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- INICIO DE CAMBIOS ---
# Inicializar DocumentConverter con configuración actualizada para la nueva versión
try:
    converter = DocumentConverter(
        allowed_formats=[
            InputFormat.PDF,
            InputFormat.DOCX,
            InputFormat.PPTX,
            InputFormat.HTML,
            InputFormat.IMAGE,
            InputFormat.ASCIIDOC,
            InputFormat.MD,
        ],
        format_options={
            InputFormat.PDF: PdfFormatOption(
                # Las opciones ahora se pasan directamente, sin el diccionario "pipeline_options"
                do_ocr=True,
                do_table_structure=True
            )
        }
    )
    logger.info("DocumentConverter inicializado correctamente")
except Exception as e:
    logger.error(f"Error inicializando DocumentConverter: {e}")
    converter = None
# --- FIN DE CAMBIOS ---

@app.get("/")
async def root():
    """Endpoint raíz con información del servicio"""
    return {
        "status": "healthy",
        "service": "Docling API",
        "version": "1.2.0", # <-- VERSIÓN ACTUALIZADA
        "architecture": "ARM64 compatible",
        "endpoints": {
            "health": "GET /health",
            "docs": "GET /docs",
            "process": "POST /api/process",
            "process_rag": "POST /api/process-rag",
            "extract_tables": "POST /api/extract-tables",
            "process_excel": "POST /api/process-excel" # <-- ENDPOINT NUEVO
        }
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok",
        "converter_ready": converter is not None
    }

@app.post("/api/process")
async def process_document(
    file: UploadFile = File(...),
    extract_tables: bool = Form(default=True),
    extract_images: bool = Form(default=False),
    do_ocr: bool = Form(default=True)
):
    """
    Procesa un documento y extrae su contenido estructurado
    
    Args:
        file: Archivo a procesar (PDF, DOCX, PPTX, HTML, etc.)
        extract_tables: Si debe extraer tablas con estructura
        extract_images: Si debe extraer metadatos de imágenes
        do_ocr: Si debe aplicar OCR en imágenes
    
    Returns:
        JSON con chunks del documento y metadatos
    """
    if converter is None:
        raise HTTPException(status_code=503, detail="Converter no inicializado")
    
    tmp_path = None
    try:
        logger.info(f"Procesando archivo: {file.filename}")
        
        content = await file.read()
        
        file_size_mb = len(content) / (1024 * 1024)
        if file_size_mb > 50:
            raise HTTPException(
                status_code=413,
                detail=f"Archivo muy grande ({file_size_mb:.1f}MB). Máximo: 50MB"
            )
        
        suffix = os.path.splitext(file.filename)[1].lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="/tmp/docling") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        logger.info(f"Archivo guardado en: {tmp_path} ({file_size_mb:.2f}MB)")
        
        result = converter.convert(tmp_path)
        doc = result.document
        
        chunks, tables, images = [], [], []
        for idx, element in enumerate(doc.iterate_items()):
            chunk = {
                "chunk_id": f"{file.filename}_{idx}", "text": element.text, "type": element.label,
                "metadata": {"filename": file.filename, "chunk_index": idx, "element_type": element.label}
            }
            if element.prov:
                prov = element.prov[0]
                chunk["metadata"]["page"] = prov.page
            chunks.append(chunk)
            if extract_tables and "table" in element.label.lower():
                tables.append({"page": chunk["metadata"].get("page"), "text": element.text})
            if extract_images and "picture" in element.label.lower():
                images.append({"page": chunk["metadata"].get("page"), "caption": element.text})
        
        metadata = {
            "filename": file.filename, "file_size_mb": round(file_size_mb, 2),
            "total_pages": len(doc.pages), "total_chunks": len(chunks),
            "total_tables": len(tables), "total_images": len(images)
        }
        
        logger.info(f"Procesamiento exitoso: {len(chunks)} chunks, {len(tables)} tablas")
        
        response = {"success": True, "metadata": metadata, "chunks": chunks}
        if tables: response["tables"] = tables
        if images: response["images"] = images
        
        return JSONResponse(content=response)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

@app.post("/api/process-rag")
async def process_for_rag(
    file: UploadFile = File(...),
    chunk_size: int = Form(default=512),
    chunk_overlap: int = Form(default=50),
    merge_peers: bool = Form(default=True)
):
    """
    Procesa un documento y genera chunks optimizados para RAG
    """
    if converter is None:
        raise HTTPException(status_code=503, detail="Converter no inicializado")
    
    tmp_path = None
    try:
        content = await file.read()
        if len(content) / (1024 * 1024) > 50:
            raise HTTPException(status_code=413, detail="Archivo muy grande (máx 50MB)")
        
        suffix = os.path.splitext(file.filename)[1].lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="/tmp/docling") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        result = converter.convert(tmp_path)
        doc = result.document
        chunker = HybridChunker(max_tokens=chunk_size, overlap=chunk_overlap, merge_peers=merge_peers)
        
        rag_chunks = []
        for idx, chunk in enumerate(chunker.chunk(doc)):
            pages = sorted(list(set(p.prov[0].page for p in chunk.meta.doc_items if p.prov)))
            element_types = list(set(e.label for e in chunk.meta.doc_items))
            rag_chunk = {
                "chunk_id": f"{file.filename}_rag_{idx}", "text": chunk.text,
                "metadata": {"filename": file.filename, "chunk_index": idx, "pages": pages, "element_types": element_types}
            }
            rag_chunks.append(rag_chunk)
        
        metadata = {"filename": file.filename, "total_pages": len(doc.pages), "total_chunks": len(rag_chunks)}
        
        return JSONResponse(content={"success": True, "metadata": metadata, "chunks": rag_chunks})
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

@app.post("/api/extract-tables")
async def extract_tables_only(file: UploadFile = File(...)):
    """
    Extrae únicamente las tablas de un documento con su estructura preservada
    """
    if converter is None:
        raise HTTPException(status_code=503, detail="Converter no inicializado")
    
    tmp_path = None
    try:
        content = await file.read()
        suffix = os.path.splitext(file.filename)[1].lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="/tmp/docling") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        result = converter.convert(tmp_path)
        doc = result.document
        
        tables = []
        for page in doc.pages:
            for table in page.tables:
                tables.append({"page": page.page_no, "text": table.text})
        
        return JSONResponse(content={"success": True, "filename": file.filename, "total_tables": len(tables), "tables": tables})
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

# --- INICIO DE CAMBIOS ---
@app.post("/api/process-excel")
async def process_excel_for_rag(file: UploadFile = File(...)):
    """
    Procesa un archivo de Excel (XLSX, XLS) y genera chunks de texto por cada fila para RAG.
    """
    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="El archivo debe ser un .xlsx o .xls")
    
    try:
        logger.info(f"Procesando archivo Excel para RAG: {file.filename}")
        
        content = await file.read()
        df = pd.read_excel(io.BytesIO(content))
        
        rag_chunks = []
        for idx, row in df.iterrows():
            row_text = ", ".join([f"{col}: {val}" for col, val in row.items() if pd.notna(val)])
            if not row_text:
                continue
            rag_chunk = {
                "chunk_id": f"{file.filename}_row_{idx}", "text": row_text,
                "metadata": {"filename": file.filename, "row": idx + 2}
            }
            rag_chunks.append(rag_chunk)
            
        metadata = {"filename": file.filename, "total_rows": len(df), "total_chunks": len(rag_chunks)}
        
        logger.info(f"Excel RAG chunks generados: {len(rag_chunks)}")
        
        return JSONResponse(content={"success": True, "metadata": metadata, "chunks": rag_chunks})

    except Exception as e:
        logger.error(f"Error en process-excel: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error procesando el archivo Excel: {str(e)}")
# --- FIN DE CAMBIOS ---

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)