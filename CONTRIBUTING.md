# Contributing

Thanks for contributing.

## Before submitting a change

- Keep existing working modules intact unless the change specifically targets them.
- Do not commit secrets or private customer data.
- Do not include `.env`, `.venv`, `__pycache__`, or generated credentials.
- Test the affected bot flow locally.
- Run syntax checks for changed Python files.
- Review database migrations carefully before adding or changing SQL.

## Pull requests

A pull request should explain:

1. What changed
2. Why it changed
3. Which modules are affected
4. How it was tested
5. Whether a database migration is required

For changes involving payments, subscriptions, Telegram access, or Supabase data, include the relevant regression tests or manual test steps.
