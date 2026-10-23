# Security Policy

## Supported Versions

Security updates are provided for the current release series:

| Version | Supported |
| ------- | --------- |
| 0.7.x   | Yes       |
| < 0.7   | No        |

---

## Reporting a Vulnerability

If you discover a security vulnerability in MvgeOS, please report it responsibly rather than opening a public issue.

- **Email**: Report vulnerabilities privately via GitHub Private Vulnerability Reporting on the repository, or open a private security advisory.
- **Include**:
  - A description of the vulnerability.
  - Steps to reproduce or proof-of-concept.
  - Affected components (mvgeos-core, mvgeos-agent, mvgeos-cli, mvgeos-gui, mvgeos-provider, mvgeos-runes, mvgeos-tome).
  - Potential impact and suggested mitigations.

You will receive an acknowledgment within 48 hours, followed by updates on triage, remediation, and public disclosure coordination.

---

## Threat Model & Local Execution Guidelines

MvgeOS is an autonomous agent architecture that executes tools ("Spells") and generates code on behalf of the user ("Summoner"). When running MvgeOS, note the following security invariants:

### 1. Command and Code Execution
The `coding_mvge` agent package provides tools for shell execution (bash) and file system manipulation. By design, these tools execute with the permissions of the user running the process.
- **Untrusted Repositories**: Do not run MvgeOS against untrusted repositories without isolation. Running an autonomous agent against code containing adversarial instructions can lead to indirect prompt injection.
- **Sandboxing**: For untrusted workloads, run MvgeOS inside an isolated container, VM, or restricted user account.

### 2. Prompt Injection & Input Validation
LLM-based autonomous agents are inherently susceptible to direct and indirect prompt injection attacks. Malicious instructions embedded in files, commit messages, or tool outputs can attempt to alter agent behavior. MvgeOS implements invariant scaffolding layers to enforce role integrity, but process isolation remains the primary defense.

### 3. API Key Hygiene
- API keys (e.g., OPENROUTER_API_KEY) are resolved from environment variables or the host OS credential store (Keyring).
- API keys are never persisted to session logs ("Tomes") or committed to git.
- Never commit .env files or session storage containing sensitive credentials.
