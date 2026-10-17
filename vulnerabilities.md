# Security review findings

Initial review: 2026-10-17

Last updated: 2026-10-17, after the database-role, Patroni, etcd, build-context, and NGINX changes.

## Current status

Five of the original seven findings have been remediated. Two remain open. The additional hardening gaps below also remain open, including browser HTTPS and PostgreSQL transport encryption.

| ID | Finding | Original severity | Current status |
| --- | --- | --- | --- |
| V-01 | Web application uses the PostgreSQL superuser | High | Remediated |
| V-02 | Unprotected Patroni management API | High | Remediated |
| V-03 | Unauthenticated, unencrypted etcd | High | Remediated |
| V-04 | Unrestricted Docker socket access in Ansible | High, conditional on a usable socket | **Open** |
| V-05 | Unauthenticated Mailpit verification inbox | Medium | **Open** |
| V-06 | Unbounded HTTP request threads and socket waits | Medium | Remediated |
| V-07 | Backups included in Docker build contexts | Medium | Remediated |

Severity describes the original finding's potential impact when its prerequisites are met. A resolved finding's original severity is not a claim of an outstanding vulnerability.

## Scope and verification limits

The initial review examined source code, Dockerfiles, Compose configuration, cluster configuration, scripts, and CI. Docker access was unavailable during that first review. Access subsequently became available, and targeted runtime checks were performed during remediation. This update combines current source inspection with those recorded checks; it is not a new penetration test or dependency vulnerability scan.

The project remains an educational local lab. Published ports bind to `127.0.0.1`, limiting external exposure. Network separation now distinguishes the NGINX edge, internal web frontend, database/application network, Patroni management network, and etcd coordination network. Docker daemon administrators remain trusted: they can access secrets and attach containers to networks.

## Open findings

### V-04 — Ansible controller receives unrestricted Docker socket access

**Status: Open. Severity: High, conditional on a usable socket mount.**

**Current evidence:** [compose/compose.ansible.yaml](compose/compose.ansible.yaml) mounts `/var/run/docker.sock` into the Ansible controller and includes it in the default project. The controller also has a writable mount of the Ansible source directory. No Docker API authorization restriction is configured for this mount.

**Impact:** If the socket is usable, compromise of this container permits control of other containers and access to their secrets and daemon-accessible files. Host impact depends on whether Docker runs directly on the host, inside a VM, or with additional restrictions. Network segmentation does not constrain an attacker who controls the Docker daemon.

**Recommended action:** Make the controller an opt-in administrative service. Remove unrestricted socket access where possible, or enforce narrowly scoped Docker API authorization. Treat any remaining socket-enabled controller as privileged administrative tooling.

**Reference:** [Docker Engine security](https://docs.docker.com/engine/security/).

### V-05 — Mailpit exposes verification emails without configured authentication

**Status: Open. Severity: Medium.**

**Current evidence:** [compose/web.yaml](compose/web.yaml) publishes Mailpit on `127.0.0.1:8025`, attaches it to `pgnet`, and configures no inbox UI/API authentication. [web/app.py](web/app.py), in `send_verification()`, sends activation links through Mailpit.

**Impact:** A local user or container able to reach Mailpit can read verification links. An attacker could register another person's address with a password the attacker chooses and activate that account without access to the real mailbox. Reading verification email alone does not disclose an existing user's password.

**Recommended action:** Restrict or authenticate the inbox UI/API and keep Mailpit limited to trusted development. Use a suitable authenticated delivery service outside the lab.

## Additional open hardening gaps

These retain their original classification as hardening gaps without formal severity ratings; they are separate from the seven findings above.

### Browser and PostgreSQL transport encryption is not enforced

NGINX still serves HTTP at `http://127.0.0.1:8080`. Verification links use this HTTP base URL, and session/form cookies do not have `Secure`. See [nginx/nginx.conf](nginx/nginx.conf), [compose/web.yaml](compose/web.yaml), and cookie handling in [web/app.py](web/app.py).

PostgreSQL connections do not require verified TLS. Patroni's host rules permit non-TLS database connections, and HAProxy forwards database traffic as plain TCP. SCRAM authentication protects password authentication but does not encrypt database queries or results. See [patroni/patroni-node1.yml](patroni/patroni-node1.yml), the corresponding node 2/3 configurations, [haproxy/haproxy.cfg](haproxy/haproxy.cfg), and [pgbouncer/pgbouncer-1.ini](pgbouncer/pgbouncer-1.ini).

**Recommended action:** Configure browser-trusted HTTPS termination and Secure cookies, update verification links, and require verified TLS for database connections and replication. Patroni API HTTPS and etcd mutual TLS are already implemented; this remaining gap concerns browser/application and PostgreSQL traffic.

### Registration reveals whether an account exists

`authenticate()` in [web/app.py](web/app.py) returns a distinct `409` response for an existing email address. A client can distinguish registered addresses from new ones, subject to authentication rate limiting.

**Recommended action:** Return a generic registration response if account existence is sensitive, while preserving verification and resend behavior.

### Dependency and image provenance controls are incomplete

Container images use mutable tags, including `nginx:stable-alpine`. Python packages in the root and Ansible Dockerfiles and collections in [ansible/requirements.yml](ansible/requirements.yml) lack version/hash locks. CI validates configuration and builds selected images but does not establish dependency/image vulnerability scanning.

**Recommended action:** Adopt reviewed dependency locks, image digest pins where appropriate, automated scanning, and a regular patch process. No specific dependency CVE has been established by this review.

## Remediated findings

### V-01 — Web application uses the PostgreSQL superuser

**Status: Remediated 2026-10-17. Original severity: High.**

**Original issue:** The web process received the cluster administrator password and used `postgres` for requests and schema initialization.

**Current controls:**

- The running web service uses `webapp` and receives only `webapp_password`.
- `webapp` has SELECT, INSERT, UPDATE, and DELETE on public tables in `todo`, schema USAGE, database CONNECT, and required sequence USAGE.
- It lacks superuser, database creation, role creation, replication, and RLS-bypass privileges. Database/schema creation and temporary table creation are denied.
- Administrator-only initialization runs separately in `web-init`; tables remain owned by `postgres`. Default grants cover future public tables/sequences created by `postgres`.

**Evidence:** [compose/web.yaml](compose/web.yaml), [web/app.py](web/app.py), [web/init_db.py](web/init_db.py), and [web/schema.sql](web/schema.sql).

**Verification performed:** Real DML/sequence access succeeded; CREATE, ALTER, DROP, temporary tables, and TRUNCATE were denied. Application workflows passed. The database backup and separate role definitions were restored in an isolated PostgreSQL instance, preserving ownership and grants.

**Checks:** [web/tests/permissions.py](web/tests/permissions.py) and [web/tests/smoke.py](web/tests/smoke.py).

### V-02 — Unprotected Patroni management API

**Status: Remediated 2026-10-17. Original severity: High.**

**Original issue:** Patroni listened on all container interfaces without authentication, TLS, or an administrative allowlist.

**Current controls:** Each API binds only to its `patroni_mgmt` address and uses HTTPS. Unsafe methods require Basic authentication and an allowed source address. The allowlist permits the dedicated admin container and registered cluster members. HAProxy uses certificate/hostname-verified HTTPS health checks, has no API password, and is excluded from unsafe calls. Management ports are not host-published.

**Evidence:** [patroni/patroni-node1.yml](patroni/patroni-node1.yml), matching node 2/3 configurations, [scripts/patroni-entrypoint.sh](scripts/patroni-entrypoint.sh), [compose/admin.yaml](compose/admin.yaml), and [haproxy/haproxy.cfg](haproxy/haproxy.cfg).

**Verification performed:** Missing/invalid credentials were rejected; valid credentials from a non-allowlisted source were rejected; untrusted server certificates failed validation; the web container could not connect to the APIs. Administrative reloads, peer HTTPS communication, replication, and role-aware routing worked.

**Checks:** [patroni_admin.py](test/security/patroni_admin.py), [patroni_denied.py](test/security/patroni_denied.py), and [patroni_isolation.py](test/security/patroni_isolation.py).

### V-03 — Unauthenticated, unencrypted etcd

**Status: Remediated 2026-10-17. Original severity: High.**

**Original issue:** etcd client and peer services used HTTP on the shared application network without authentication bootstrap.

**Current controls:** Authentication is enabled persistently. Patroni has read/write access only to `/service/postgres-lab/`; health checks have read-only access to `/health/`. Separate certificate-only root administration is restricted to bootstrap and opt-in tooling. Client and peer listeners require mutual TLS, with separate CAs and TLS 1.2 as the minimum version. Etcd joins only the internal coordination network. PostgreSQL startup waits for authentication bootstrap to succeed.

**Evidence:** [compose/etcd.yaml](compose/etcd.yaml), [etcd/bootstrap-auth.sh](etcd/bootstrap-auth.sh), [etcd/admin.sh](etcd/admin.sh), [scripts/init-etcd-tls.sh](scripts/init-etcd-tls.sh), and [compose/postgres.yaml](compose/postgres.yaml).

**Verification performed:** Authentication was enabled; Patroni had only its scoped role; namespace reads/writes succeeded; out-of-scope operations and user administration were denied. Missing client certificates and unauthorized peer certificates were rejected. Application containers could not reach client or peer listeners. All three etcd members were healthy, and bootstrap was repeatable.

**Checks:** [etcd_client.py](test/security/etcd_client.py), [etcd_peer_tls.py](test/security/etcd_peer_tls.py), and [etcd_isolation.py](test/security/etcd_isolation.py).

**Operational note:** Certificate compatibility issues caused temporary database/application unavailability during migration. A failed replica's previous data directory was preserved and the replica rebuilt. Leadership, both streaming replicas, and application workflows were restored. See [ETCD-SECURITY.md](docs/ETCD-SECURITY.md) for the migration record, snapshots, and rotation procedures. Leaf certificates expire after 365 days; renewal remains an operational responsibility.

### V-06 — Unbounded HTTP request threads and socket waits

**Status: Remediated 2026-10-17. Original severity: Medium.**

**Original issue:** Python's threaded HTTP server had no application-level thread cap or socket read timeout, allowing incomplete requests to retain unbounded resources.

**Current controls:** NGINX owns the localhost-published web port. Python has no host-published port. NGINX buffers requests, limits bodies to 8 KiB, applies per-client request/connection limits and a global active-request connection limit, and sets header/body/upstream idle timeouts. The backend independently caps request threads at 64 and applies a 10-second socket idle timeout. Client-IP headers are trusted only from the configured proxy address, which overwrites incoming forwarding headers.

NGINX runs unprivileged with a read-only filesystem, temporary writable storage, dropped capabilities, and no privilege escalation. It has no database credentials or database/coordination network membership. Access logs omit query strings; error logging is suppressed for `/verify` to protect verification tokens.

**Evidence:** [nginx/nginx.conf](nginx/nginx.conf), [nginx/proxy.conf](nginx/proxy.conf), [compose/web.yaml](compose/web.yaml), and `BoundedHTTPServer`/`Handler.client_ip()` in [web/app.py](web/app.py).

**Verification performed:** NGINX configuration validated under the deployed filesystem/user restrictions; the host login URL returned HTTP 200; backend concurrency limits, socket expiration, and trusted-header handling passed. Oversized bodies were rejected and incomplete headers expired. Full registration, verification, login, and task workflows passed through NGINX. Verification-token query strings were absent from the checked NGINX logs.

**Checks:** [http_server.py](web/tests/http_server.py), [nginx_proxy.py](test/security/nginx_proxy.py), and [check-nginx.sh](scripts/check-nginx.sh). For proxy application tests, run `web/tests/smoke.py` with `APP_TEST_URL=http://nginx:8080` inside `web`.

**Remaining limit:** Idle timers are not absolute total-request deadlines. These controls bound resources and address the original unbounded-thread issue; they do not eliminate all denial-of-service risks. HTTPS remains open as described above.

### V-07 — Backups included in Docker build contexts

**Status: Remediated 2026-10-17. Original severity: Medium.**

**Original issue:** A local dump under `.tmp/backups/` was not excluded from the root Docker context. Git exclusions alone did not protect build contexts.

**Current controls:** All four contexts use source allowlists, excluding backups, secrets, dumps, and unrelated files by default, regardless of extension. NGINX uses an existing image and read-only configuration mounts rather than an additional local build context.

**Evidence:** [.dockerignore](.dockerignore), [web/.dockerignore](web/.dockerignore), [ansible/.dockerignore](ansible/.dockerignore), and [etcd/.dockerignore](etcd/.dockerignore).

**Verification performed:** Each context was exported through Docker's COPY behavior and checked to contain only required source files. No backups or secrets appeared. This control protects build contexts; local backup access and retention still require appropriate filesystem permissions and operational care.

## Other protections retained

- Published ports are localhost-only.
- Parameterized SQL and task ownership predicates protect database operations.
- HTML escaping, a restrictive Content Security Policy, and session-bound CSRF tokens are present.
- Cookies use HttpOnly and SameSite=Strict; password hashes use salted PBKDF2.
- Session and verification tokens are stored as hashes; verification tokens expire and are single-use.
- The Python container runs as `nobody`; generated local password files are ignored by Git and have restricted permissions.

These controls and targeted checks are not proof that every associated attack class is absent.

## Remaining remediation priorities

1. Restrict Docker socket access and make the Ansible controller opt-in.
2. Add browser HTTPS/Secure cookies and verified PostgreSQL transport encryption before broader deployment.
3. Restrict or authenticate Mailpit's inbox and use an appropriate mail service outside the lab.
4. Address account enumeration and dependency/image assurance; maintain certificate renewal and patching processes.

Completed findings should remain closed unless configuration or runtime evidence shows a regression. Do not treat this document as production certification.
