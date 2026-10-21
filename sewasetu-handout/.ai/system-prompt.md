# Project-specific AI instructions

You are helping maintain Sewa Setu, a public-service pension portal that handles
real applicants' identity, contact, and bank information.

- Keep changes precise, tested, and consistent with the existing Flask, Jinja,
  PostgreSQL, Docker Compose, and pytest patterns.
- Preserve existing citizen records. Never edit, delete, or regenerate the
  supplied production seed or historical log archive.
- Protect personal data with server-side authorization checks. Never put
  credentials, tokens, or personal data in source control or logs.
- Validate all user input on the server, use parameterized SQL, and report
  operational failures explicitly.
- Keep the application usable on mobile devices and accessible by keyboard.
- Do not claim that a deployment or external service is live unless it has been
  checked.
