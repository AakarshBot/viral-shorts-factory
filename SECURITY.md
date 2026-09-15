# Security

## Local API keys

Keep real API keys in a local `.env` file only. Do not commit `.env` or other credential files to GitHub.

The repository ignores `.env`, local Google authentication files, databases, generated output and asset caches.

If a credential is ever committed to a public repository, revoke or replace it immediately and then remove the credential from the current source tree.

## Streamlit deployment

For Streamlit Cloud, configure required secrets through the deployment's Secrets settings rather than committing a `.env` file to the repository.
