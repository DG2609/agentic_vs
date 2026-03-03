---
name: security-audit
description: Systematic security audit — OWASP Top 10, secrets, injection, auth
version: "1.0"
---

## Security Audit Checklist

Perform a thorough security review of the codebase or the files described in $ARGUMENTS.

Work through each category below. For each finding:
1. State the **severity** (Critical / High / Medium / Low / Info)
2. Cite the **file and line number**
3. Explain the **risk**
4. Suggest a **concrete fix**

---

### A01 — Broken Access Control
- [ ] Are authorization checks present on every endpoint / action?
- [ ] Can users access or modify other users' data?
- [ ] Are admin routes protected?

### A02 — Cryptographic Failures
- [ ] Is sensitive data (passwords, tokens, PII) stored in plaintext?
- [ ] Are weak algorithms used (MD5, SHA1, DES, ECB mode)?
- [ ] Are TLS/HTTPS enforced for data in transit?

### A03 — Injection
- [ ] SQL injection: are all queries parameterised?
- [ ] Command injection: is `shell=True` used with user input?
- [ ] LDAP / XPath / template injection risks?

### A04 — Insecure Design
- [ ] Are rate limits present on auth endpoints?
- [ ] Is business logic bypassable?
- [ ] Are file uploads validated (type, size, path)?

### A05 — Security Misconfiguration
- [ ] Debug mode enabled in production?
- [ ] Default credentials or example configs present?
- [ ] Verbose error messages exposing stack traces?

### A06 — Vulnerable Components
!`grep -rn "requirements\|package.json\|Cargo.toml\|go.mod" . --include="*.txt" --include="*.json" --include="*.toml" -l 2>/dev/null | head -5`

- [ ] Are dependencies pinned to specific versions?
- [ ] Are known-vulnerable versions in use?

### A07 — Auth & Session
- [ ] Are tokens validated on every request?
- [ ] Is session fixation possible?
- [ ] Are failed login attempts rate-limited?

### A08 — Software & Data Integrity
- [ ] Is user-controlled data deserialised (pickle, YAML.load, eval)?
- [ ] Are CI/CD pipelines protected from script injection?

### A09 — Logging & Monitoring
- [ ] Are security events (auth fail, permission denied) logged?
- [ ] Are secrets/PII excluded from logs?

### A10 — SSRF
- [ ] Is user-supplied URL input used in outbound requests?
- [ ] Are internal network addresses blocked?

---

### Secret Scanning

!`grep -rn "password\s*=\s*['\"][^'\"]\|api_key\s*=\s*['\"][^'\"]\|secret\s*=\s*['\"][^'\"]" . --include="*.py" --include="*.js" --include="*.env" 2>/dev/null | grep -v "test\|example\|sample\|TODO" | head -20`

### Hardcoded Paths / Credentials

!`grep -rn "127\.0\.0\.1\|localhost\|0\.0\.0\.0" . --include="*.py" --include="*.js" 2>/dev/null | grep -v "test\|comment\|#" | head -10`

---

### Output Format

After completing the checklist, produce a report:

```
## Security Audit Report

### Summary
- Critical: N
- High:     N
- Medium:   N
- Low:      N

### Findings

#### [SEVERITY] <Title> — <file>:<line>
**Risk:** ...
**Fix:** ...
```

$ARGUMENTS
