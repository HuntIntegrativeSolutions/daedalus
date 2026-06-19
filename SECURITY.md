# Security Policy

## Scope

`daedalus` communicates with live Allen-Bradley PLCs and is built around a
**write-safety doctrine**: the library is read-only unless the caller explicitly
arms a `WritePolicy`. Security issues include, but are not limited to:

- Any bug that bypasses the `WritePolicy` gate and causes an unintended write to a
  PLC — treat this as **critical**.
- Credential or session-token exposure in logs or error messages.
- Unauthenticated network paths that allow an untrusted caller to trigger writes.
- Denial-of-service via malformed CIP/EtherNet-IP frames.

Out of scope: the library will never perform online program edits, firmware flashing,
program download, or keyswitch changes — reports claiming these are possible are
considered critical by definition.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Use GitHub's [private security advisory](https://github.com/HuntIntegrativeSolutions/daedalus/security/advisories/new)
to report confidentially. Alternatively, email the maintainers directly (address on
file with the GitHub organization).

Include:
- A description of the vulnerability and its potential impact.
- Steps to reproduce or a minimal proof-of-concept.
- The daedalus version, Python version, and OS where you reproduced it.

## Response timeline

- **Acknowledgement:** within 3 business days.
- **Initial assessment:** within 7 business days.
- **Fix / advisory:** timeline depends on severity; critical write-bypass issues
  receive priority treatment.

We will credit reporters in the advisory unless anonymity is requested.
