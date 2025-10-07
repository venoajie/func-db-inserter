import base64
import json
import logging
import os
import re
import tempfile
import textwrap
import traceback
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

import oci
import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

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
    global secrets_client, db_pool
    log = logging.LoggerAdapter(logger, {'invocation_id': 'startup'})
    log.info("--- LIFESPAN START: INITIALIZING DEPENDENCIES ---")

    # [FIX] The original code is being refactored to move the slow DB connection
    # test out of the time-sensitive startup/lifespan phase.

    try:
        # 1. OCI Client Initialization (from provided evidence)
        env_string = repr(os.environ)
        def _get_config_from_env_str(key: str, env_str: str) -> str | None:
            match = re.search(f"'{re.escape(key)}': '([^']*)'", env_str)
            return match.group(1) if match else None

        config_values = {key: _get_config_from_env_str(key, env_string) for key in REQUIRED_AUTH_VARS}
        missing_vars = [key for key, value in config_values.items() if not value]
        if missing_vars:
            raise ValueError(f"Missing OCI auth config variables: {missing_vars}")

        base64_body = config_values["OCI_PRIVATE_KEY_CONTENT"].replace(PEM_HEADER, "").replace(PEM_FOOTER, "").strip()
        wrapped_body = "\n".join(textwrap.wrap(base64_body, 64))
        private_key_content = f"{PEM_HEADER}\n{wrapped_body}\n{PEM_FOOTER}\n"
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".pem") as key_file:
            key_file.write(private_key_content)
            key_file_path = key_file.name

        config = {
            "user": config_values["OCI_USER_OCID"], "key_file": key_file_path,
            "fingerprint": config_values["OCI_FINGERPRINT"], "tenancy": config_values["OCI_TENANCY_OCID"],
            "region": config_values["OCI_REGION"]
        }
        oci.config.validate_config(config)
        secrets_client = oci.secrets.SecretsClient(config=config)
        log.info("OCI Secrets Client initialized successfully.")

        # 2. Fetch and Decode Database Credentials from OCI Vault
        db_secret_ocid = _get_config_from_env_str('DB_SECRET_OCID', env_string)
        if not db_secret_ocid:
            raise ValueError("Missing critical configuration: DB_SECRET_OCID")

        secret_bundle = secrets_client.get_secret_bundle(secret_id=db_secret_ocid)
        secret_content = secret_bundle.data.secret_bundle_content.content
        decoded_secret = base64.b64decode(secret_content).decode('utf-8')
        db_creds = json.loads(decoded_secret)
        log.info("Database secret retrieved and decoded from Vault.")

        # 3. Initialize and Test Database Connection Pool
        conn_info = (
            f"host={db_creds['host']} port={db_creds['port']} "
            f"dbname={db_creds['dbname']} user={db_creds['username']} "
            f"password={db_creds['password']}"
        )      
        
        # The pool is created, but no connection is attempted here.
        db_pool = AsyncConnectionPool(conninfo=conn_info, min_size=1, max_size=5)
        log.info("Database connection pool configured. Connection will be established on first request.")
        log.info("--- LIFESPAN SUCCESS: ALL DEPENDENCIES INITIALIZED ---")
        
    except Exception as e:
        log.critical(f"--- FATAL LIFESPAN CRASH: {e}", exc_info=True)
        raise
    finally:
        if key_file_path and os.path.exists(key_file_path):
            os.remove(key_file_path)

    yield
    
    # --- Cleanup on Shutdown ---
    if db_pool:
        await db_pool.close()
        log.info("Database connection pool closed.")

# --- FastAPI Dependency Injection ---
def get_logger(fn_invoke_id: Annotated[str | None, Header(alias="fn-invoke-id")] = None) -> logging.LoggerAdapter:
    invocation_id = fn_invoke_id or str(uuid.uuid4())
    return logging.LoggerAdapter(logger, {'invocation_id': invocation_id})

async def get_db_connection():
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Service Unavailable: DB pool not initialized.")
    try:
        async with db_pool.connection() as conn:
            yield conn
    except psycopg.Error as e:
        raise HTTPException(status_code=503, detail=f"Database connection failed: {e}")

# --- FastAPI Application ---
app = FastAPI(
    title="PostgreSQL Inserter Function",
    description="A function to insert data into a PostgreSQL database.",
    lifespan=lifespan
)

@app.get("/health", tags=["Monitoring"])
async def health_check(
    db_conn: Annotated[psycopg.AsyncConnection, Depends(get_db_connection)],
    log: Annotated[logging.LoggerAdapter, Depends(get_logger)]
):
    """Performs a health check by querying the database version."""
    log.info("Health check requested.")
    try:
        async with db_conn.cursor() as cur:
            await cur.execute("SELECT version();")
            result = await cur.fetchone()
            db_version = result[0] if result else "N/A"
        log.info(f"Health check successful. DB Version: {db_version[:30]}...")
        return {"status": "ok", "database_version": db_version}
    except Exception as e:
        log.error(f"Health check failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Health check failed: {e}")

@app.post("/call", tags=["Data Ingestion"])
async def create_item(
    item: Item,
    db_conn: Annotated[psycopg.AsyncConnection, Depends(get_db_connection)],
    log: Annotated[logging.LoggerAdapter, Depends(get_logger)]
):
    """Receives an item and inserts it into the database."""
    log.info(f"Received request to create item: {item.name}")
    try:
        # For a PoC, we assume a simple 'items' table exists.
        # CREATE TABLE items (id SERIAL PRIMARY KEY, name VARCHAR(255), description TEXT);
        async with db_conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO items (name, description) VALUES (%s, %s) RETURNING id;",
                (item.name, item.description)
            )
            item_id = await cur.fetchone()
            await db_conn.commit()
        
        log.info(f"Successfully inserted item '{item.name}' with new ID: {item_id[0]}")
        return {
            "status": "success",
            "message": "Item created successfully.",
            "item_id": item_id[0]
        }
    except psycopg.Error as e:
        log.error(f"Database error during item insertion: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database Error: {e}")
    except Exception as e:
        log.error(f"An unexpected error occurred: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An internal error occurred: {e}")
