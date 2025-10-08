
To improve security, we will replace the temporary, overly broad permissions with a dedicated group and narrowly scoped policies.

**Step 1: Create a Dedicated Group**
*   Create a new IAM group named `Function-User-Group`.
*   Add the user principal (`ocid1.user.oc1..aaad["redacted]26za`) to this new group.

**Step 2: Add Least-Privilege Policies**
*   Add the following three policy statements to a policy in the **Root Compartment**. These grant the new group the minimum required permissions.
    ```
    Allow group 'Function-User-Group' to read vaults in compartment Sandbox
    Allow group 'Function-User-Group' to read secret-bundles in compartment Sandbox
    Allow group 'Function-User-Group' to use keys in compartment Sandbox where target.key.id = 'ocid1.key.oc1.eu-frankfurt-1.e["redacted"]q'
    ```

**Step 3: Remove Over-Privileged Policies**
*   **Remove** the following temporary policies that were applied to the `CI-CD-Deployers` group:
    ```
    # REMOVE THIS:
    Allow group 'CI-CD-Deployers' to read vaults in compartment Sandbox
    # REMOVE THIS:
    Allow group 'CI-CD-Deployers' to read secret-bundles in compartment Sandbox
    # REMOVE THIS:
    Allow group 'CI-CD-Deployers' to use keys in compartment Sandbox where target.key.id = 'ocid1.key.oc1.eu-frankfurt-1.e["redacted"]q'