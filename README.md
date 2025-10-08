
# System State Record: OCI Serverless Function for PostgreSQL Ingestion

-   **Project**: OCI Serverless Function for PostgreSQL Ingestion
-   **Version**: As of 2025-10-07
-   **Status**: **OPERATIONAL**. The function successfully connects to the database and ingests data.

## 1. Final Working Architecture

This section documents the final, correct configuration that enables a serverless function in a separate VCN to connect to a database in a central hub VCN.

### 1.1. Network Topology (Hub and Spoke)

The system uses a hub-and-spoke model to isolate services.

-   **Hub VCN (`shared-infrastructure-vcn`, `10.0.0.0/16`):** Contains the central database VM.
-   **Spoke VCN (`sandbox-vcn`, `10.1.0.0/16`):** Contains the serverless function.
-   **Dynamic Routing Gateway (DRG):** Acts as the central router connecting the two VCNs.

### 1.2. End-to-End Network Path Checklist

For a connection to succeed, **all five** of the following network components must be correctly configured. A failure in any one of these will result in a connection timeout.

1.  **Function's Subnet Route Table (`fn-public-rt`):**
    -   Must have a rule directing traffic for the database VCN (`10.0.0.0/16`) to the **DRG**.

2.  **Database's Subnet Route Table (`route table for private ...`):**
    -   Must have a rule directing traffic for the function's VCN (`10.1.0.0/16`) to the **DRG**.

3.  **DRG's Route Table (`hub-spoke-transit-rt`):**
    -   Must have two rules to enable transit routing:
        -   One rule directing traffic for `10.0.0.0/16` to the database VCN attachment.
        -   One rule directing traffic for `10.1.0.0/16` to the function VCN attachment.

4.  **Database's Subnet Security List (`Default Security List for shared-infrastructure-vcn`):**
    -   Must have an **ingress rule** allowing traffic from the function's subnet (`Source CIDR: 10.1.0.0/24`) on the database port (`Destination Port: 6432/TCP`).

5.  **Function's Subnet Security List (`fn-public-sl`):**
    -   Must have an **ingress rule** allowing return traffic from the database VM (`Source CIDR: 10.0.0.146/32`) for all protocols.

### 1.3. IAM Configuration (Least Privilege)

The function uses a standard OCI user principal for authentication, secured by a dedicated group and narrowly scoped policies.

-   **Dedicated Group:** `Function-User-Group` contains the user whose credentials are used by the function.
-   **Required Policies (in Root Compartment):**
    1.  `Allow group 'Function-User-Group' to read vaults in compartment Sandbox`
    2.  `Allow group 'Function-User-Group' to read secret-bundles in compartment Sandbox`
    3.  `Allow group 'Function-User-Group' to use keys in compartment Sandbox where target.key.id = '<VAULT_MASTER_KEY_OCID>'`

| Policy Name | Location | Statements |
| :--- | :--- | :--- |
| `CICD-Deployers-Policy` | `Sandbox` Compartment | `Allow group CI-CD-Deployers to manage functions-family in compartment Sandbox` |
| `PostgresInserterFunction-Runtime-Policy` | Root Compartment | `Allow dynamic-group PostgresInserterFunctionDG to use virtual-network-family in compartment Sandbox`<br>`Allow dynamic-group ... to read secret-bundles in compartment Sandbox where ...`<br>`Allow dynamic-group ... to use keys in compartment Sandbox where ...`<br>**`Allow dynamic-group PostgresInserterFunctionDG to read repos in tenancy`** |
| `Tenancy-Wide-Service-Policies` | Root Compartment | `Allow group CI-CD-Deployers to manage repos in tenancy`<br>`Allow service faas to use virtual-network-family in compartment Sandbox` |



### 1.4. Vault & Secret Configuration

-   **Secret Content:** The secret stored in OCI Vault must be a plain text JSON object with the correct connection details.
    ```json
    {
      "host": "10.0.0.146",
      "port": 6432,
      "dbname": "librarian_db",
      "username": "librarian_user",
      "password": "YOUR_DATABASE_PASSWORD"
    }
    ```

## 2. Developer Runbook: Creating a New Database Function

Follow this checklist to provision a new function that connects to the database.

1.  **Code:** Use the final `main.py` from this repository as a template. It correctly handles the OCI request format.
2.  **Network:** Ensure the new function is deployed into a **Function Application** that is configured to use the `fn-public-subnet`.
3.  **CI/CD:**
    -   Add a new workflow file or modify the existing `provision-function.yml` for the new function.
    -   Run the workflow to build the image and create the function resource.
4.  **Manual Configuration (OCI Console):**
    -   Navigate to the newly created function.
    -   Go to the **Configuration** section.
    -   Add all required environment variables (`DB_SECRET_OCID`, `OCI_USER_OCID`, etc.).
5.  **Invocation:** Invoke the function using the OCI CLI. The function will be accessible at the `/call` endpoint.

## 3. Key Learnings & Troubleshooting Guide

This project revealed several critical behaviors of the OCI platform.

-   **CRITICAL: Network Ambiguity is Fatal.** The final blocker was an **overlapping subnet CIDR block** (`10.1.0.0/24`). This created an ambiguous network path that is impossible to debug without the **Network Path Analyzer**. An IP Address Management (IPAM) plan is essential to prevent this.
-   **Function Application Networking is Immutable.** The subnet configuration for a Function Application **cannot be changed** after it is created. If it is configured for the wrong subnet, the entire application (and all functions within it) must be deleted and recreated.
-   **OCI Functions Default Endpoint is `/call`.** When an invoke endpoint is not specified via annotations, the platform sends requests to the `/call` path, not the root (`/`).
-   **OCI Functions Payload is Raw JSON.** The request body sent to the function is the raw JSON from your payload file, not a wrapper object.
-   **Tooling Limitations.** The `oci fn` CLI toolchain has limitations. It does not support setting all function properties (like annotations or subnet on update). The OCI Console is the authoritative interface for these settings.
-   **Vault Access Requires Three Policies.** To read a secret, a principal needs `read vaults`, `read secret-bundles`, and `use keys` permissions.

## 4. Known Technical Debt

-   **Manual Environment Configuration:** The function's environment variables are set manually in the OCI Console. This is brittle and not easily reproducible. This should be migrated to an Infrastructure as Code solution.
-   **Lack of Infrastructure as Code (IaC):** The entire network infrastructure was created manually. This led to the overlapping subnet issue. The entire VCN, DRG, and Security List configuration should be managed with Terraform to ensure consistency and prevent errors.
-   **IAM Anomaly: Container Repository Location:** The CI/CD pipeline is only able to create and push container images to the **root compartment's** OCI Container Registry. Attempts to restrict the `CI-CD-Deployers` group to `manage repos in compartment Sandbox` resulted in `Invalid Image` errors during deployment. For work around, a policy `Allow group CI-CD-Deployers to manage repos in tenancy` was created at the root compartment level. which violated The principle of least privilege. The CI/CD user has overly broad permissions, and the container images are not co-located with their function in the `Sandbox` compartment.

