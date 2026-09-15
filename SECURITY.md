# Keeping API keys safe

Keep real API keys in a local `.env` file. Never commit `.env` to GitHub.

The repository ignores `.env`, local databases, generated output, and local Google/YouTube authentication files.

If an API key is ever committed publicly, revoke it and create a replacement key.