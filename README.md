
# System State Record: OCI Serverless Function for PostgreSQL Ingestion

-   **Project**: OCI Serverless Function for PostgreSQL Ingestion
-   **Version**: As of 2025-10-07
-   **Status**: **BLOCKED**. The function deploys successfully but fails on invocation.
-   **Last Known Error**:
    ```json
    {
        "client_version": "Oracle-PythonSDK/2.160.2, Oracle-PythonCLI/3.66.2",
        "code": "FunctionInvokeImageNotAvailable",
        "message": "Failed to pull function image",
        "operation_name": "invoke_function",
        "status": 502,
        "target_service": "functions_invoke",
        "timestamp": "2025-10-07T00:44:03.384303+00:00"
    }
    ```

## 1. Component Manifest

-   **`main.py`**: A Python 3.12 FastAPI application. It uses a `lifespan` context manager to initialize an `oci.secrets.SecretsClient` and a `psycopg_pool.AsyncConnectionPool`. It exposes `/health` (GET) and `/items` (POST) endpoints.
-   **`requirements.txt`**: Defines Python dependencies, including `fastapi`, `oci`, and `psycopg_pool`.
-   **`func.yaml`**: OCI Functions manifest. Defines `memory: 512`, `timeout: 60`, and sets the default invoke endpoint annotation to `fn.oci.oracle.com/fn/invokeEndpoint: "/items"`.
-   **`Dockerfile`**: A multi-stage Dockerfile based on `python:3.12.3-slim-bookworm`. The final stage's `CMD` is `[ "uvicorn", "main:app", "--uds", "/tmp/iofs/lsnr.sock" ]`.

## 2. System Dependencies & Verification

### 2.1. Database Host Environment
This section provides the necessary context to interact with the database host VM without referencing external documentation.
-   **Host OS & Architecture**: Oracle Linux 10 (`aarch64`).
-   **Service Management**: All services (PostgreSQL, PgBouncer) are containerized via Docker and Docker Compose. Interaction must be performed using `docker` commands, not `systemctl`.
-   **Interaction Pattern**: Administrative tasks on services are performed via `docker exec`.
    -   **Example `psql` access**: `docker exec -it <container_name> psql -U <user> -d <database>`
    -   **PostgreSQL Container Name**: The container name can be found via `docker ps` and is expected to be similar to `postgres-stack-postgres-1`.
-   **File System**: All persistent data and configuration are located under the `/srv` directory.

### 2.2. Target Database Schema & Verification
This is the target schema and the success indicator for the function's operation.
-   **Table Definition**: The function is designed to insert data into the `items` table.
    ```sql
    CREATE TABLE items (
        id SERIAL PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        description TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    ```
-   **Permissions Provisioning**: The `librarian_user` initially lacked creation privileges. The following commands were run as the `postgres` superuser to grant the necessary permissions:
    ```sql
    GRANT USAGE ON SCHEMA public TO librarian_user;
    GRANT CREATE ON SCHEMA public TO librarian_user;
    ```
-   **Success Verification**: A successful function invocation is verified by connecting to the database and executing the following query, which should return the inserted record:
    ```sql
    SELECT * FROM items;
    ```

## 3. CI/CD Configuration

A two-phase deployment strategy is implemented via GitHub Actions.
-   **`provision-function.yml`**: Triggered manually (`workflow_dispatch`). It builds and pushes a Docker image, then uses the OCI CLI to perform a create-or-update operation on the function's infrastructure based on settings read from `func.yaml` using `yq`.
-   **`update-application.yml`**: Triggered automatically on push to `main`. It builds and pushes a Docker image, then uses the OCI CLI to update the function with the new image, leaving all other infrastructure settings untouched.

## 4. Deployed Infrastructure Specification

### 4.1. Function (`postgres-inserter`)
-   **OCID**: `ocid1.fnfunc.oc1.eu-frankfurt-1.am["redacted"]ra`
-   **Compartment**: `Sandbox`
-   **Image**: `fra.ocir.io/frpowqeyehes/hello-world-app/postgres-inserter:<git_sha>`
-   **Memory**: `512 MB`
-   **Timeout**: `60 seconds`

### 4.2. Networking (`sandbox-vcn`)
-   **VCN OCID**: `ocid1.vcn.oc1.eu-frankfurt-1.am["redacted"]hq`
-   **VCN CIDR**: `10.1.0.0/16`
-   **Subnet OCID**: `ocid1.subnet.oc1.eu-frankfurt-1.aa["redacted"]la`
-   **Subnet CIDR**: `10.1.1.0/24` (Private)
-   **Service Gateway OCID**: `ocid1.servicegateway.oc1.eu-frankfurt-1.aa["redacted"]6a`
    -   **Attached Service**: `all-fra-services-in-oracle-services-network`
-   **Route Table Rule** (`ocid1.routetable.oc1.eu-frankfurt-1.aa["redacted"]gq`):
    -   **Destination**: `all-fra-services-in-oracle-services-network`
    -   **Target Type**: `Service Gateway`
    -   **Target**: `ocid1.servicegateway.oc1.eu-frankfurt-1.aa["redacted"]6a`

### 4.3. IAM Configuration
-   **Dynamic Group (`PostgresInserterFunctionDG`)**:
    -   **Location**: Root Compartment
    -   **Rule**: `ALL {resource.type = 'fnfunc', resource.compartment.id = '<Sandbox_Compartment_OCID>'}`
-   **Policies**:

| Policy Name | Location | Statements |
| :--- | :--- | :--- |
| `CICD-Deployers-Policy` | **`Sandbox` Compartment** | `Allow group CI-CD-Deployers to manage functions-family in compartment Sandbox` |
| `PostgresInserterFunction-Runtime-Policy` | Root Compartment | `Allow dynamic-group PostgresInserterFunctionDG to use virtual-network-family in compartment Sandbox`<br>`Allow dynamic-group ... to read secret-bundles in compartment Sandbox where ...`<br>`Allow dynamic-group ... to use keys in compartment Sandbox where ...` |
| `Tenancy-Wide-Service-Policies` | Root Compartment | `Allow group CI-CD-Deployers to manage repos in tenancy`<br>`Allow service faas to use virtual-network-family in compartment Sandbox` |

## 5. Operational Runbook (Invocation)

-   **Limitation**: The `oci fn function invoke` CLI command does not support specifying an HTTP method or path. It can only invoke the default endpoint (`/items`) configured in `func.yaml`.
-   **Command**:
    1.  Create payload: `echo '{"name": "Test", "description": "Test"}' > payload.json`
    2.  Invoke: `oci fn function invoke --function-id ocid1.fnfunc.oc1.eu-frankfurt-1.am["redacted"]ra --body file://payload.json --file -`

## 6. Architectural Record & Known Issues

### 6.1. [CRITICAL] IAM Anomaly: Container Repository Location
-   **Observation**: The CI/CD pipeline is only able to create and push container images to the **root compartment's** OCI Container Registry. Attempts to restrict the `CI-CD-Deployers` group to `manage repos in compartment Sandbox` resulted in `Invalid Image` errors during deployment.
-   **Workaround Implemented**: A policy `Allow group CI-CD-Deployers to manage repos in tenancy` was created at the root compartment level.
-   **Consequence**: The principle of least privilege is violated. The CI/CD user has overly broad permissions, and the container images are not co-located with their function in the `Sandbox` compartment.

### 6.2. [BLOCKER] Invocation Failure: `FunctionInvokeImageNotAvailable`
-   **Observation**: The function fails at invocation time with `Failed to pull function image`.
-   **State of System at Failure**:
    1.  The container image exists in the root compartment's registry.
    2.  The function runs in a private subnet with a correctly configured Service Gateway and route rule, providing a network path to OCI services.
    3.  A policy `Allow service faas to use virtual-network-family in compartment Sandbox` exists at the root, granting the Functions platform network permissions.
    4.  The function's dynamic group was **not** explicitly enabled via `oracle.com/oci/auth/principal: "dynamic_group"` annotation, as the OCI CLI did not support this parameter.
-   **Hypothesis**: The failure is due to an unresolved IAM or networking permission issue. The function's execution environment, despite the Service Gateway, cannot establish a connection to OCIR to pull its image. The lack of an explicit Resource Principal annotation is the most likely cause, but no tool was found to apply it successfully.

### 6.3. [DEBT] Manual Function Configuration
-   **State**: The function's environment variables (`DB_SECRET_OCID`, `OCI_*` credentials) are configured manually in the OCI Console.
-   **Risk**: This creates the possibility of configuration drift and makes redeploying the function in a new environment a manual, error-prone process.