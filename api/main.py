from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import tempfile
import os
import uuid
from pathlib import Path
import shutil
from typing import Dict
import logging
from .utils.document_processor import process_bibliography

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI Application
app = FastAPI(
    title="Bibliography Sorter API",
    description="API for sorting bibliography entries in Word documents while preserving formatting",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create temp directory for file processing
TEMP_DIR = Path("/tmp/bibliography_sorter")
TEMP_DIR.mkdir(exist_ok=True, parents=True)

# Store for tracking processed files
processed_files = {}

@app.get("/", response_class=HTMLResponse)
async def root():
    """Root endpoint - serves the frontend"""
    try:
        html_path = Path(__file__).parent.parent / "public" / "index.html"
        if html_path.exists():
            with open(html_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        else:
            return HTMLResponse(content="<h1>Bibliography Sorter</h1><p>Frontend not found</p>")
    except Exception as e:
        logger.error(f"Error serving frontend: {e}")
        return HTMLResponse(content="<h1>Error</h1><p>Could not load frontend</p>")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "Bibliography Sorter API"}

@app.post("/api/upload")
async def upload_document(file: UploadFile = File(...)):
    """Upload and validate a Word document"""
    try:
        # Validate file type
        if not file.filename.lower().endswith(('.docx', '.doc')):
            raise HTTPException(
                status_code=400, 
                detail="Only Word documents (.docx, .doc) are supported"
            )
        
        # Validate file size (max 10MB for Vercel)
        content = await file.read()
        file_size = len(content)
        
        if file_size > 10 * 1024 * 1024:  # 10MB limit for Vercel
            raise HTTPException(
                status_code=400,
                detail="File size too large. Maximum size is 10MB"
            )
        
        # Generate unique file ID
        file_id = str(uuid.uuid4())
        
        # Save uploaded file
        input_path = TEMP_DIR / f"{file_id}_input.docx"
        with open(input_path, "wb") as buffer:
            buffer.write(content)
        
        # Validate document and count paragraphs
        paragraph_count = process_bibliography(str(input_path), None, validate_only=True)
        
        # Store file info
        processed_files[file_id] = {
            "filename": file.filename,
            "size": file_size,
            "input_path": str(input_path),
            "paragraph_count": paragraph_count,
            "status": "uploaded"
        }
        
        logger.info(f"File uploaded successfully: {file.filename} ({file_id})")
        
        return {
            "file_id": file_id,
            "filename": file.filename,
            "size": file_size,
            "paragraph_count": paragraph_count,
            "message": "File uploaded successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@app.post("/api/sort/{file_id}")
async def sort_bibliography(file_id: str):
    """Sort bibliography entries in the uploaded document"""
    try:
        if file_id not in processed_files:
            raise HTTPException(status_code=404, detail="File not found")
        
        file_info = processed_files[file_id]
        input_path = file_info["input_path"]
        
        if not os.path.exists(input_path):
            raise HTTPException(status_code=404, detail="File not found on disk")
        
        # Generate output path
        output_path = TEMP_DIR / f"{file_id}_sorted.docx"
        
        # Process the document
        result = process_bibliography(input_path, str(output_path))
        
        if not result["success"]:
            raise HTTPException(
                status_code=500, 
                detail=f"Processing failed: {result.get('error', 'Unknown error')}"
            )
        
        # Update file info
        processed_files[file_id].update({
            "output_path": str(output_path),
            "status": "processed",
            "processing_result": result
        })
        
        logger.info(f"Document sorted successfully: {file_id}")
        
        return {
            "file_id": file_id,
            "message": "Bibliography sorted successfully",
            "statistics": {
                "original_entries": result["original_count"],
                "unique_entries": result["unique_count"],
                "duplicates_removed": result["duplicates_removed"]
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sorting error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Sorting failed: {str(e)}")

@app.get("/api/download/{file_id}")
async def download_sorted_document(file_id: str):
    """Download the sorted bibliography document"""
    try:
        if file_id not in processed_files:
            raise HTTPException(status_code=404, detail="File not found")
        
        file_info = processed_files[file_id]
        
        if file_info["status"] != "processed":
            raise HTTPException(status_code=400, detail="File not yet processed")
        
        output_path = file_info["output_path"]
        
        if not os.path.exists(output_path):
            raise HTTPException(status_code=404, detail="Processed file not found")
        
        # Generate download filename
        original_filename = file_info["filename"]
        name, ext = os.path.splitext(original_filename)
        download_filename = f"{name}_sorted{ext}"
        
        logger.info(f"File downloaded: {file_id}")
        
        return FileResponse(
            path=output_path,
            filename=download_filename,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

@app.delete("/api/cleanup/{file_id}")
async def cleanup_files(file_id: str):
    """Clean up temporary files"""
    try:
        if file_id not in processed_files:
            raise HTTPException(status_code=404, detail="File not found")
        
        file_info = processed_files[file_id]
        
        # Remove files
        for path_key in ["input_path", "output_path"]:
            if path_key in file_info:
                file_path = Path(file_info[path_key])
                if file_path.exists():
                    file_path.unlink()
        
        # Remove from tracking
        del processed_files[file_id]
        
        logger.info(f"Files cleaned up for: {file_id}")
        return {"message": "Files cleaned up successfully"}
        
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {str(e)}")