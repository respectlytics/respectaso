# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in RespectASO, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

### Contact

Email: [respectlytics@loheden.com](mailto:respectlytics@loheden.com)

Subject line: `[SECURITY] [RespectASO] Brief description`

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if you have one)

### Response Timeline

- **Acknowledgment:** Within 48 hours
- **Assessment:** Within 7 days
- **Fix timeline:** Depends on severity, typically within 30 days

### Scope

In scope:
- Cross-site scripting (XSS), cross-site request forgery (CSRF)
- Data exposure via API endpoints
- Docker container escape or privilege escalation
- Credential leakage in logs or error messages

Out of scope:
- Denial of service attacks (this is a local-only tool)
- Social engineering
- Issues in third-party dependencies (report upstream)
- Issues requiring physical access to the machine running Docker

## Security Design

RespectASO is designed as a **local-only tool** running in Docker on the user's machine:

- **No remote access** — binds to localhost only
- **No authentication** — single-user tool, no auth needed
- **No credentials stored** — all data comes from the public iTunes Search API, no API keys needed
- **No data exfiltration** — all API calls go directly from the user's machine to Apple/iTunes
- **No telemetry** — no data is sent to Respectlytics or any third party

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest release | Yes |
| Previous release | Security fixes only |
| Older versions | No |

## Acknowledgments

We appreciate security researchers who help keep RespectASO safe. With your permission, we'll credit you in our release notes.
