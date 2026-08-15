# Security Policy

## Reporting a vulnerability

Please do not publish credentials, private customer data, payment screenshots, or exploitable details in a public issue.

For a suspected security vulnerability, contact the repository maintainer privately before public disclosure.

## Secrets

Never commit:

- `.env`
- Telegram bot tokens
- `SUPABASE_SECRET_KEY`
- production database credentials
- private customer/payment data

The repository intentionally uses environment variables for secrets.

## If a secret is exposed

1. Revoke or rotate the exposed credential immediately.
2. Remove it from the working tree.
3. Check Git history if it was committed.
4. Review affected Telegram/Supabase resources.
5. Replace the credential in the deployment environment.

## Production data

Do not use real customer data in public examples, tests, screenshots, or documentation.
