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

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Crear aplicación FastAPI
app = FastAPI(
    title="Docling API",
    description="API para procesamiento de documentos con Docling (versión oficial)",
    version="1.0.0",
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

# --- INICIO DE LA CORRECCIÓN ---
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
# --- FIN DE LA CORRECCIÓN ---

@app.get("/")
async def root():
    """Endpoint raíz con información del servicio"""
    return {
        "status": "healthy",
        "service": "Docling API",
        "version": "1.0.0",
        "docling_version": "2.0.0",
        "architecture": "ARM64 compatible",
        "endpoints": {
            "health": "GET /health",
            "docs": "GET /docs",
            "process": "POST /api/process",
            "process_rag": "POST /api/process-rag",
            "extract_tables": "POST /api/extract-tables"
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
        
        # Leer contenido del archivo
        content = await file.read()
        
        # Validar tamaño (máx 50MB)
        file_size_mb = len(content) / (1024 * 1024)
        if file_size_mb > 50:
            raise HTTPException(
                status_code=413,
                detail=f"Archivo muy grande ({file_size_mb:.1f}MB). Máximo: 50MB"
            )
        
        # Guardar archivo temporal
        suffix = os.path.splitext(file.filename)[1].lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="/tmp/docling") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        logger.info(f"Archivo guardado en: {tmp_path} ({file_size_mb:.2f}MB)")
        
        # Convertir documento
        result = converter.convert(tmp_path)
        doc = result.document
        
        # Extraer elementos del documento
        chunks = []
        tables = []
        images = []
        
        for idx, element in enumerate(doc.iterate_items()):
            # Crear chunk básico
            chunk = {
                "chunk_id": f"{file.filename}_{idx}",
                "text": element.text,
                "type": element.label,
                "metadata": {
                    "filename": file.filename,
                    "chunk_index": idx,
                    "element_type": element.label
                }
            }
            
            # Agregar información de página y coordenadas
            if element.prov:
                prov = element.prov[0]
                chunk["metadata"]["page"] = prov.page
                
                if prov.bbox:
                    chunk["metadata"]["bbox"] = {
                        "x0": prov.bbox.l,
                        "y0": prov.bbox.t,
                        "x1": prov.bbox.r,
                        "y1": prov.bbox.b
                    }
            
            chunks.append(chunk)
            
            # Extraer tablas por separado si se solicita
            if extract_tables and "table" in element.label.lower():
                table_data = {
                    "page": chunk["metadata"].get("page"),
                    "text": element.text,
                    "bbox": chunk["metadata"].get("bbox")
                }
                tables.append(table_data)
            
            # Extraer imágenes por separado si se solicita
            if extract_images and "picture" in element.label.lower():
                image_data = {
                    "page": chunk["metadata"].get("page"),
                    "caption": element.text,
                    "bbox": chunk["metadata"].get("bbox")
                }
                images.append(image_data)
        
        # Metadatos del documento completo
        metadata = {
            "filename": file.filename,
            "file_size_mb": round(file_size_mb, 2),
            "file_type": suffix[1:] if suffix else "unknown",
            "total_pages": len(doc.pages),
            "total_chunks": len(chunks),
            "total_tables": len(tables),
            "total_images": len(images)
        }
        
        logger.info(f"Procesamiento exitoso: {len(chunks)} chunks, {len(tables)} tablas")
        
        response = {
            "success": True,
            "metadata": metadata,
            "chunks": chunks
        }
        
        if extract_tables and tables:
            response["tables"] = tables
        
        if extract_images and images:
            response["images"] = images
        
        return JSONResponse(content=response)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error procesando documento: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error procesando documento: {str(e)}"
        )
    
    finally:
        # Limpiar archivo temporal
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
                logger.info(f"Archivo temporal eliminado: {tmp_path}")
            except Exception as e:
                logger.warning(f"No se pudo eliminar archivo temporal: {e}")

@app.post("/api/process-rag")
async def process_for_rag(
    file: UploadFile = File(...),
    chunk_size: int = Form(default=512),
    chunk_overlap: int = Form(default=50),
    merge_peers: bool = Form(default=True)
):
    """
    Procesa un documento y genera chunks optimizados para RAG
    
    Args:
        file: Archivo a procesar
        chunk_size: Tamaño máximo de tokens por chunk
        chunk_overlap: Tokens de solapamiento entre chunks
        merge_peers: Si debe unir elementos relacionados
    
    Returns:
        Chunks optimizados para embeddings y vector store
    """
    if converter is None:
        raise HTTPException(status_code=503, detail="Converter no inicializado")
    
    tmp_path = None
    try:
        logger.info(f"Procesando para RAG: {file.filename}")
        
        # Leer y validar archivo
        content = await file.read()
        file_size_mb = len(content) / (1024 * 1024)
        
        if file_size_mb > 50:
            raise HTTPException(status_code=413, detail="Archivo muy grande (máx 50MB)")
        
        # Guardar temporal
        suffix = os.path.splitext(file.filename)[1].lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="/tmp/docling") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        # Convertir documento
        result = converter.convert(tmp_path)
        doc = result.document
        
        # Crear chunker híbrido (respeta estructura semántica)
        chunker = HybridChunker(
            max_tokens=chunk_size,
            overlap=chunk_overlap,
            merge_peers=merge_peers
        )
        
        # Generar chunks
        rag_chunks = []
        for idx, chunk in enumerate(chunker.chunk(doc)):
            # Extraer páginas únicas
            pages = set()
            element_types = set()
            has_table = False
            has_figure = False
            
            for element in chunk.meta.doc_items:
                if element.prov:
                    pages.add(element.prov[0].page)
                
                element_type = element.label.lower()
                element_types.add(element.label)
                
                if "table" in element_type:
                    has_table = True
                if "figure" in element_type or "picture" in element_type:
                    has_figure = True
            
            rag_chunk = {
                "chunk_id": f"{file.filename}_rag_{idx}",
                "text": chunk.text,
                "metadata": {
                    "filename": file.filename,
                    "chunk_index": idx,
                    "pages": sorted(list(pages)),
                    "element_types": list(element_types),
                    "has_table": has_table,
                    "has_figure": has_figure,
                    "token_count": len(chunk.text.split())  # Aproximado
                }
            }
            
            rag_chunks.append(rag_chunk)
        
        metadata = {
            "filename": file.filename,
            "file_size_mb": round(file_size_mb, 2),
            "total_pages": len(doc.pages),
            "total_chunks": len(rag_chunks),
            "chunking_config": {
                "max_tokens": chunk_size,
                "overlap": chunk_overlap,
                "merge_peers": merge_peers
            }
        }
        
        logger.info(f"RAG chunks generados: {len(rag_chunks)}")
        
        return JSONResponse(content={
            "success": True,
            "metadata": metadata,
            "chunks": rag_chunks
        })
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en process-rag: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except:
                pass

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
                table_data = {
                    "page": page.page_no,
                    "text": table.text,
                    "bbox": None
                }
                
                if table.prov:
                    prov = table.prov[0]
                    if prov.bbox:
                        table_data["bbox"] = {
                            "x0": prov.bbox.l,
                            "y0": prov.bbox.t,
                            "x1": prov.bbox.r,
                            "y1": prov.bbox.b
                        }
                
                # Intentar obtener estructura de tabla
                if hasattr(table, 'data') and table.data:
                    table_data["structure"] = table.data
                
                tables.append(table_data)
        
        return JSONResponse(content={
            "success": True,
            "filename": file.filename,
            "total_tables": len(tables),
            "tables": tables
        })
    
    except Exception as e:
        logger.error(f"Error extrayendo tablas: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except:
                pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)