
<!-- FILENAME: main.py (Section Change) -->
```python
# ... (imports and other code) ...

# --- Lifespan Management for Initialization ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global secrets_client, db_pool
    log = logging.LoggerAdapter(logger, {'invocation_id': 'startup'})
    log.info("--- LIFESPAN START: INITIALIZING DEPENDENCIES ---")

    try:
        # 1. OCI Client Initialization (using Resource Principal)
        log.info("Initializing OCI client using Resource Principal...")
        signer = oci.auth.signers.get_resource_principals_signer()
        secrets_client = oci.secrets.SecretsClient(config={}, signer=signer)
        log.info("OCI Secrets Client initialized successfully using Resource Principal.")

        # 2. Fetch and Decode Database Credentials from OCI Vault
        db_secret_ocid = os.environ.get('DB_SECRET_OCID')
        if not db_secret_ocid:
            raise ValueError("Missing critical configuration: DB_SECRET_OCID")

        secret_bundle = secrets_client.get_secret_bundle(secret_id=db_secret_ocid)
        secret_content = secret_bundle.data.secret_bundle_content.content
        decoded_secret = base64.b64decode(secret_content).decode('utf-8')
        db_creds = json.loads(decoded_secret)
        log.info("Database secret retrieved and decoded from Vault.")

        # 3. Initialize Database Connection Pool
        conn_info = (
            f"host={db_creds['host']} port={db_creds['port']} "
            f"dbname={db_creds['dbname']} user={db_creds['username']} "
            f"password={db_creds['password']}"
        )
        db_pool = AsyncConnectionPool(conninfo=conn_info, min_size=1, max_size=5)
        log.info("Database connection pool configured. Connection will be established on first request.")
        log.info("--- LIFESPAN SUCCESS: ALL DEPENDENCIES INITIALIZED ---")

    except Exception as e:
        log.critical(f"--- FATAL LIFESPAN CRASH: {e}", exc_info=True)
        raise

    yield

    if db_pool:
        await db_pool.close()
        log.info("Database connection pool closed.")

# ... (rest of the file remains the same) ...
```

**Step 2: Configure the Required IAM and Function Settings**

The code change alone is not enough. You must configure OCI to grant the function permission to act as a resource principal.

1.  **Create a Dynamic Group:**
    *   If it doesn't already exist, create a Dynamic Group (e.g., `PostgresInserterFunctionDG`) that uniquely identifies your function. The rule would be:
        ```
        ALL {resource.type = 'fnfunc', resource.compartment.id = '<YOUR_SANDBOX_COMPARTMENT_OCID>'}
        ```

2.  **Create IAM Policies for the Dynamic Group:**
    *   The policies you had for the `Function-User-Group` now need to be applied to this Dynamic Group.
        ```
        Allow dynamic-group 'PostgresInserterFunctionDG' to read vaults in compartment Sandbox
        Allow dynamic-group 'PostgresInserterFunctionDG' to read secret-bundles in compartment Sandbox
        Allow dynamic-group 'PostgresInserterFunctionDG' to use keys in compartment Sandbox where target.key.id = '<VAULT_MASTER_KEY_OCID>'
        ```

3.  **Enable Resource Principal on the Function:**
    *   This is the step that was previously blocked by your OCI CLI version. You must do this in the **OCI Console**.
    *   Navigate to your function (`postgres-inserter`).
    *   Click **Edit**.
    *   Check the box that says **"Enable resource principal"**.
    *   Click **Save Changes**.

**Step 3: Update Function Configuration**

1.  Navigate to your function's **Configuration** section in the OCI Console.
2.  **Delete** the following now-obsolete environment variables:
    *   `OCI_USER_OCID`
    *   `OCI_FINGERPRINT`
    *   `OCI_TENANCY_OCID`
    *   `OCI_REGION`
    *   `OCI_PRIVATE_KEY_CONTENT`
3.  Ensure that `DB_SECRET_OCID` is still present.

After completing these three steps and redeploying the new code, your function will be more secure, easier to manage, and aligned with OCI best practices.