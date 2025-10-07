
```python
import base64
import json
import logging
import os
import re
import socket # [DIAGNOSTIC] Import socket for raw network test
import tempfile
import textwrap
import traceback
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

import oci
import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field, ValidationError

# --- Pydantic Model for incoming data ---
class Item(BaseModel):
    name: str = Field(..., example="Sample Item")
    description: str | None = Field(None, example="A description for the item.")

# --- Constants for OCI Authentication ---
REQUIRED_AUTH_VARS = [
    "OCI_USER_OCID", "OCI_FINGERPRINT", "OCI_TENANCY_OCID",
    "OCI_REGION", "OCI_PRIVATE_KEY_CONTENT"
]
PEM_HEADER = "-----BEGIN RSA PRIVATE KEY-----"
PEM_FOOTER = "-----END RSA PRIVATE KEY-----"

# --- Structured Logging Setup ---
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": record.created,
            "level": record.levelname,
            "message": record.getMessage(),
            "invocation_id": getattr(record, 'invocation_id', 'N/A'),
        }
        if record.exc_info:
            log_record['exception'] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(log_record)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.hasHandlers():
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.propagate = False

# --- Global Clients & Connection Pool ---
secrets_client = None
db_pool = None

# --- Lifespan Management for Initialization ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # For this diagnostic test, we can skip the slow startup initialization.
    log = logging.LoggerAdapter(logger, {'invocation_id': 'startup'})
    log.info("--- DIAGNOSTIC MODE: SKIPPING LIFESPAN INITIALIZATION ---")
    yield
    log.info("--- DIAGNOSTIC MODE: SHUTDOWN COMPLETE ---")


# --- FastAPI Application ---
app = FastAPI(
    title="PostgreSQL Inserter Function (DIAGNOSTIC MODE)",
    description="A function to insert data into a PostgreSQL database.",
    lifespan=lifespan
)

@app.post("/call", tags=["Data Ingestion"])
async def create_item(log: logging.LoggerAdapter = Depends(get_logger)):
    """
    [DIAGNOSTIC] This endpoint is temporarily replaced with a raw socket
    connection test to isolate network connectivity issues.
    """
    host = "10.0.0.146"
    port = 6432
    timeout_seconds = 10

    log.info(f"Attempting raw socket connection to {host}:{port} with a {timeout_seconds}s timeout...")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_seconds)
            s.connect((host, port))
        
        message = f"SUCCESS: Raw TCP connection to {host}:{port} was established."
        log.info(message)
        return {"status": "success", "message": message}

    except socket.timeout:
        message = f"FAILURE: Connection to {host}:{port} timed out after {timeout_seconds} seconds. The network path is blocked."
        log.error(message)
        raise HTTPException(status_code=504, detail=message)
    except Exception as e:
        message = f"FAILURE: An unexpected error occurred while connecting to {host}:{port}: {e}"
        log.error(message, exc_info=True)
        raise HTTPException(status_code=500, detail=message)

# Helper function for logging, not used in diagnostic but kept for completeness
def get_logger(fn_invoke_id: Annotated[str | None, Header(alias="fn-invoke-id")] = None) -> logging.LoggerAdapter:
    invocation_id = fn_invoke_id or str(uuid.uuid4())
    return logging.LoggerAdapter(logger, {'invocation_id': invocation_id})