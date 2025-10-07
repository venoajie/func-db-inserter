
import base64
import json
import logging
import os
import tempfile
import traceback
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

import asyncssh
import oci
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

# --- Pydantic Model for incoming data ---
class VmWriteRequest(BaseModel):
    filename: str = Field(default="hello_world.txt", example="output.log")
    content: str = Field(default="Hello from the serverless function!", example="Log entry.")
    path: str = Field(default="/tmp", example="/home/opc/logs")


# --- Structured Logging Setup ---
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": record.created, "level": record.levelname,
            "message": record.getMessage(), "invocation_id": getattr(record, 'invocation_id', 'N/A'),
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

# --- Global OCI Client ---
secrets_client = None
vm_creds = {}

# --- Lifespan Management for Initialization ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global secrets_client, vm_creds
    log = logging.LoggerAdapter(logger, {'invocation_id': 'startup'})
    log.info("--- LIFESPAN START: INITIALIZING DEPENDENCIES ---")

    try:
        # 1. OCI Client Initialization (using Resource Principal)
        signer = oci.auth.signers.get_resource_principals_signer()
        secrets_client = oci.secrets.SecretsClient(config={}, signer=signer)
        log.info("OCI Secrets Client initialized successfully using Resource Principal.")

        # 2. Fetch and Decode VM Credentials from OCI Vault
        vm_secret_ocid = os.environ.get('VM_SECRET_OCID')
        if not vm_secret_ocid:
            raise ValueError("Missing critical configuration: VM_SECRET_OCID")

        secret_bundle = secrets_client.get_secret_bundle(secret_id=vm_secret_ocid)
        secret_content = secret_bundle.data.secret_bundle_content.content
        decoded_secret = base64.b64decode(secret_content).decode('utf-8')
        vm_creds = json.loads(decoded_secret)
        
        # Validate required keys
        required_keys = ['host', 'username', 'private_key']
        if not all(key in vm_creds for key in required_keys):
            raise ValueError(f"VM secret is missing one of the required keys: {required_keys}")

        log.info(f"VM secret for host {vm_creds['host']} retrieved from Vault.")
        log.info("--- LIFESPAN SUCCESS: ALL DEPENDENCIES INITIALIZED ---")

    except Exception as e:
        log.critical(f"--- FATAL LIFESPAN CRASH: {e}", exc_info=True)
        raise

    yield

# --- FastAPI Dependency Injection ---
def get_logger(fn_invoke_id: Annotated[str | None, Header(alias="fn-invoke-id")] = None):
    return logging.LoggerAdapter(logger, {'invocation_id': fn_invoke_id or str(uuid.uuid4())})

# --- FastAPI Application ---
app = FastAPI(
    title="VM File Writer Function",
    description="A function to write a file to a VM via SSH.",
    lifespan=lifespan
)

@app.get("/health", tags=["Monitoring"])
async def health_check():
    return {"status": "ok"}

@app.post("/write-file", tags=["VM Operations"])
async def write_file_to_vm(
    req: VmWriteRequest,
    log: Annotated[logging.LoggerAdapter, Depends(get_logger)]
):
    """Connects to a VM via SSH and writes a file."""
    if not vm_creds:
        log.error("VM credentials not initialized.")
        raise HTTPException(status_code=503, detail="Service Unavailable: VM credentials not initialized.")

    log.info(f"Request to write '{req.filename}' to {vm_creds['host']}:{req.path}")
    
    # Use a temporary file for the private key for asyncssh
    key_file = tempfile.NamedTemporaryFile(mode='w', delete=False)
    try:
        key_file.write(vm_creds['private_key'])
        key_file.close() # Close it so asyncssh can read it

        conn_options = asyncssh.SSHClientConnectionOptions(client_keys=[key_file.name])

        async with asyncssh.connect(
            vm_creds['host'], username=vm_creds['username'],
            options=conn_options
        ) as conn:
            full_path = os.path.join(req.path, req.filename)
            # Use SFTP to write the file to avoid command injection issues with echo
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(full_path, 'w') as f:
                    await f.write(req.content)
            log.info(f"Successfully wrote file to {full_path}")
            return {"status": "success", "message": f"File '{req.filename}' written to {req.path}."}

    except Exception as e:
        log.error(f"Failed to write file to VM: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"SSH or file operation failed: {e}")
    finally:
        if os.path.exists(key_file.name):
            os.remove(key_file.name)