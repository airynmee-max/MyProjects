# Oracle HCM BI Intelligence — Milestone 1

A local-first prototype for registering Oracle Fusion BI Publisher environments, testing a **user-supplied** CatalogService WSDL, recursively inventorying accessible folders/reports/data models, and persisting catalogue metadata.

> This is an early connector prototype. Validate it against a non-production Fusion environment first. It does not execute reports, query HCM data, or modify catalogue objects.

## Important security note

This project is currently stored in the public `airynmee-max/MyProjects` repository. **Do not commit real environment URLs if they are confidential, credentials, client catalogue exports, SQL, or database files.** The app stores credentials encrypted in the local SQLite database using a key supplied separately through an environment variable. Keep that key private and back it up securely; losing it makes stored credentials unreadable.

For production or client use, move the code to a private repository and deploy behind appropriate access controls. Do not expose the Streamlit port publicly.

## What Milestone 1 includes

- Environment registration with a manually supplied BI Publisher CatalogService WSDL URL.
- Optional explicit SOAP service endpoint override (no endpoint is guessed).
- WSDL reachability/inspection and a separate read-only catalogue test.
- CatalogService `getFolderContents(folderAbsolutePath, userID, password)` calls.
- Recursive folder traversal with a maximum-object safety limit.
- Optional download of report/data-model definitions via CatalogService `getObject`.
- Persistent environment, object metadata, scan history, and encrypted credentials in SQLite.
- No automatic report execution and no catalogue writes.

Oracle documents CatalogService as managing report objects, folders and data models, and documents `getFolderContents` and `getObject` as catalogue operations:
- https://docs.oracle.com/en/cloud/paas/analytics-cloud/acspd/publisher-catalogservice.html
- https://docs.oracle.com/en/cloud/paas/analytics-cloud/acspd/to_getfoldercontents_method.html

Oracle BI Publisher services and authentication arrangements can vary by environment and configuration. The app requires you to provide the exact WSDL URL and lets you supply an explicit service endpoint override. The initial implementation uses the documented SOAP-body username/password arguments for CatalogService operations; HTTP Basic is optional only for retrieving a protected WSDL. OAuth and other authentication patterns are not implemented in this milestone.

## Run locally

Requires Python 3.11+.

1. Clone the repository.
2. Open this directory:
   ```bash
   cd oracle-hcm-bi-intelligence
   ```
3. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   # Windows PowerShell: .venv\\Scripts\\Activate.ps1
   # macOS/Linux: source .venv/bin/activate
   ```
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Generate an encryption key and set it as an environment variable. Keep the key private:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Set the printed value as `APP_ENCRYPTION_KEY`. Also set `APP_DB_PATH` to a persistent local path if desired. See `.env.example`.
6. Start the app:
   ```bash
   streamlit run app.py
   ```

The app loads `.env` if present. The provided `.env.example` is a template; copy it to `.env` and fill in the key. Never commit `.env`.

## Connect an environment

1. Enter a friendly environment name.
2. Paste the exact CatalogService WSDL URL supplied/confirmed by your Oracle administrator or environment documentation.
3. If your WSDL is protected by HTTP Basic, select that option and enter credentials for WSDL retrieval. Otherwise leave it disabled.
4. Enter the BI Publisher username and password used by the documented CatalogService SOAP methods.
5. Click **Test WSDL** first. This checks the supplied URL and inspects the WSDL; it does not guess an endpoint.
6. Click **Scan Catalog** and specify the catalog root path your account is allowed to read (confirm the correct root with your administrator). The app uses only read operations.
7. Review scan status, discovered objects, and saved metadata.

## Known limitations / next steps

- Fusion tenants may differ in WSDL availability, service URL, proxy/load-balancer behavior, and supported authentication. Confirm these with the tenant administrator before scanning.
- Catalog response schemas can vary; the recursive normalizer handles common SOAP object shapes but must be verified against a real tenant.
- Downloaded definitions are stored locally under the configured data directory. Treat them as client-confidential.
- The current version inventories object metadata and optionally saves definition files; SQL parsing, dependency mapping, business-purpose summaries, semantic search, and question answering are later milestones.
- Use a test environment first and keep the scan limit conservative.
