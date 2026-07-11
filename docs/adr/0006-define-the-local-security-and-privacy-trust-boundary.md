# 0006. Зафиксировать локальную границу безопасности и приватности / Define the local security and privacy trust boundary

```json adr-meta
{
  "id": "0006",
  "titleRu": "Зафиксировать локальную границу безопасности и приватности",
  "titleEn": "Define the local security and privacy trust boundary",
  "status": "accepted",
  "date": "2026-07-11",
  "deciders": [
    "knowledge-base maintainer"
  ],
  "tags": [
    "security",
    "privacy",
    "local-first",
    "ssrf"
  ],
  "supersedes": [],
  "supersededBy": []
}
```

## RU

### Контекст и проблема

`knowledge-base` обрабатывает личные тексты и owner archives на машине владельца. При этом CLI умеет получать публичные snapshots по URL, читать directory/`.zip`, подключаться к ArangoDB с Basic Auth и создавать JSONL exports с полным нормализованным текстом. Даже локальный single-user инструмент имеет внешние границы: специально подобранный URL может обратиться к localhost/cloud metadata или сменить DNS-ответ между проверкой и соединением; строковый путь вложения либо symlink в directory archive может вывести чтение за корень архива; неограниченный response или archive может исчерпать память/CPU; dev credentials или export могут быть случайно опубликованы; integration tests могут засорить реальный corpus.

Защитные меры были добавлены после implementation audit и ревью PR #9/#10, но архитектурная причина была распределена между планом исправлений, кодом и тестами. Этот ADR принят ретроспективно по состоянию на 2026-07-11, не являлся предварительным одобрением изменений и описывает реально действующую local-first границу; он не утверждает, что проект уже имеет remote/multi-user security model.

### Y-statement

В контексте локальной персональной базы знаний с network fetch, archive ingest, ArangoDB Basic Auth и экспортом полного текста, столкнувшись с рисками SSRF/DNS rebinding, path traversal, утечки credentials/exports и загрязнения личной базы тестами, мы решили выбрать local-first trust boundary с defense-in-depth для network target, path, credential, output и test boundaries, чтобы безопаснее поддерживать single-user workflow, принимая предупреждения вместо полного запрета для некоторых operator overrides, доверие к local OS user/host и выбранным inputs в части resource usage, а также отсутствие per-client/remote auth и audit модели.

### Драйверы решения

- Личный corpus и локальные archive refs не должны становиться доступны по сети из-за dev defaults.
- Live fetch должен обращаться только к явно допустимому публичному HTTP(S) endpoint и не превращаться в чтение локальных файлов или внутренних сервисов.
- Проверка hostname до запроса должна защищать и от DNS rebinding, а не только от literal private IP.
- Недоверенное строковое path value из `result.json` или ZIP member name не должно приводить к lexical traversal или archive extraction; сам owner-supplied directory archive остаётся доверенным input v1.
- Runtime secrets должны задаваться локально с понятным precedence и не коммититься в git.
- Экспорт и integration tests должны явно учитывать, что база содержит персональные данные.
- Защита должна оставаться совместимой с воспроизводимым локальным CLI и synthetic CI fixtures.

### Рассмотренные варианты

- **Считать локального оператора и все inputs доверенными.** Почти не добавляет кода, но одна ошибка URL/path/config может раскрыть файлы, внутренние сервисы, credentials или личный corpus.
- **Полагаться только на container/OS sandbox.** Снижает часть ущерба, но не задаёт application-level правила для redirects, архивных refs, exports и test database.
- **Сразу строить remote-first service с TLS, identity, RBAC и audit log.** Даёт более сильную многопользовательскую модель, но существенно расширяет scope персонального stdio/CLI проекта.
- **Local-first defense-in-depth.** Ограничить default network exposure и валидировать fetch/path/output/test boundaries в приложении, сохранив явные operator overrides там, где строгий запрет мешал бы локальной работе.

### Итоговое решение

Выбран вариант: local-first defense-in-depth со следующими обязательными границами.

ArangoDB и credentials:

- Compose runtime публикует ArangoDB только на `127.0.0.1`; default client URL также loopback. Доступный по сети shared/remote deployment не является поддержанной безопасной конфигурацией v1 и требует отдельного решения об authentication, TLS и audit.
- Реальные credentials задаются process environment, explicit TOML или gitignored `config/arangodb.env`; секреты не коммитятся. Tracked example/default допустим только как явно локальный dev credential при loopback binding.
- Для обычных settings действует precedence `process env > explicit TOML > config/arangodb.env > dev default`. Для password сначала проверяется `KB_ARANGO_PASSWORD`, затем env var из `password_env`, literal TOML password, gitignored env file и только потом local dev default.
- Если оператор направляет Basic Auth на non-loopback `http://`, client выдаёт явное предупреждение о cleartext credentials и рекомендует HTTPS. Предупреждение не превращает удалённый HTTP в безопасный режим.

Network fetch:

- Source adapters разрешают live fetch только по `http`/`https`; `file:`, `ftp:`, `data:` и другие схемы отклоняются до открытия.
- Проверка подтверждает public network destination, но не authenticity источника: expected host/channel allowlist отсутствует, redirects могут перейти на другой public host, а результат всё равно импортируется под фиксированным `source_key`. Operator-selected URL доверяется как identity/content input.
- Hostname резолвится явно, и каждый полученный адрес должен быть public: private, loopback, link-local, reserved, multicast и unspecified ranges запрещены.
- Соединение открывается на проверенный pinned IP, сохраняя исходный hostname для TLS SNI/certificate validation. Это закрывает окно DNS rebinding между validation и connect.
- Каждый redirect повторно проходит URL/address validation и pinning; системные proxy handlers отключены, чтобы request не получил неявный альтернативный маршрут.
- Blocked/unreachable fetch возвращается через структурированный source error и предлагает использовать локальный snapshot, а не ослаблять проверки.

Archives и пути:

- Owner/account archives читаются только с явно переданного локального пути. Directory archive в v1 считается доверенным owner-supplied input. ZIP members читаются как streams и не извлекаются на файловую систему, поэтому ingestion не выполняет archive extraction по member name.
- Attachment refs из Telegram JSON принимаются только как относительные archive paths. Абсолютные POSIX paths, Windows drive/UNC paths и любой `..` lexical traversal отклоняются до `stat()` и сохранения metadata.
- Directory walkers, чтение файлов и `stat()` используют обычные filesystem APIs и могут последовать за symlink-файлом за пределы archive root. Это принятое ограничение v1: нельзя передавать недоверенные или содержащие symlinks directory archives. Поддержка таких inputs требует resolved containment/no-follow policy; ZIP flow этого directory-symlink риска не имеет.
- Binary attachment payloads не копируются в ArangoDB; сохраняются только прошедшие lexical validation refs и доступные metadata. Новые операции extraction/write обязаны отдельно обеспечить resolved-path containment и защиту от symlink traversal.

Availability и resource usage:

- Live fetch читает response body целиком; Telegram ZIP читает `result.json` целиком и stream-хеширует все members; Medium читает posts целиком и также обходит archive для manifest. В v1 нет max response bytes, file-count/total-size quotas или compression-ratio guard от ZIP bomb.
- Это принятое ограничение local operator workflow: выбранные URL и archives считаются доверенными в части availability/resource consumption и не должны обрабатываться unattended, если источник недоверенный. Public-address validation и отсутствие extraction не являются защитой от oversized content.

Outputs и tests:

- `kb export jsonl` содержит полный normalized document/chunk text и document metadata, включая возможные локальные attachment refs, но не raw snapshot payload. Нормальная зона вывода — gitignored `data/generated/`; запись в другое место разрешена только с явным warning о риске commit/share.
- Integration tests по умолчанию используют отдельную `knowledge_base_integration_test` database и сбрасывают её перед прогоном; CI дополнительно использует отдельный service container. Явно заданный `KB_ARANGO_DATABASE` считается осознанной ответственностью оператора и автоматически не подменяется.
- В репозитории остаются только synthetic fixtures. Реальные archives, generated exports, local configs и secrets покрыты `.gitignore` и не должны добавляться вручную.

Local host, access и data at rest:

- Loopback ArangoDB и stdio/read-only MCP уменьшают network/mutation surface, но не авторизуют отдельные local clients. Любой процесс под тем же OS account, получивший config/env/DB credentials, может читать normalized personal text через CLI, прямой DB client или MCP; per-client authentication/authorization и audit log отсутствуют.
- `data/raw/`, Docker volume ArangoDB и `data/generated/` хранят данные в plaintext под host filesystem permissions и обычной backup policy. Application-level encryption at rest, retention enforcement и secure deletion отсутствуют; v1 полагается на доверенный OS account, disk encryption и настройки backups владельца.

### Последствия

- Хорошо: live source fetch не может штатно читать local files, loopback/cloud metadata или внутренний private endpoint и защищён от DNS rebinding через address pinning.
- Хорошо: loopback binding, gitignored credentials и transport warning снижают вероятность случайно открыть personal database с dev password.
- Хорошо: абсолютные, Windows/UNC и содержащие `..` attachment paths не приводят к обычному lexical traversal, ZIP не извлекается, а tests не засоряют личный corpus по умолчанию.
- Плохо: legitimate intranet/private URLs нельзя использовать через live fetch; их нужно сохранить локально и импортировать как snapshot либо пересмотреть trust model.
- Плохо: warnings для non-loopback cleartext Basic Auth и export вне `data/generated/` не являются hard enforcement и могут быть сознательно проигнорированы.
- Плохо: directory archive не изолирован от symlink escape; manifest hashing, file reads или attachment `stat()` могут последовать за symlink, поэтому v1 доверяет владельцу и самому directory input.
- Плохо: отсутствуют response/archive resource limits; большой ответ, множество файлов или ZIP bomb могут исчерпать память, CPU или время процесса.
- Плохо: local process с credentials может читать corpus без per-client authorization/audit, а данные at rest не имеют app-level encryption, retention или secure-deletion guarantees.
- Плохо: public-address validation не подтверждает expected source; ошибочный или malicious public URL/redirect может загрязнить фиксированный logical corpus.
- Нейтрально: явный remote Arango URL и explicit integration database override остаются возможны для опытного оператора, но выходят за безопасный default.
- Нейтрально: lexical path checks и отказ от ZIP extraction покрывают только соответствующие классы traversal; работа с недоверенными directories, symlinks, распаковкой или записью потребует более сильного filesystem containment.

### План пересмотра

Пересмотреть решение до появления HTTP/MCP remote transport, нескольких local identities, автоматического schedule/daemon ingest, private-network source adapters, shared object storage или hosted ArangoDB. Также пересмотр нужен, если потребуется per-client auth/audit, app-level encryption/retention/secure deletion, expected-host/source allowlist, enforced output/transport policy, secret manager, archive extraction, приём недоверенных URL/archives либо unattended ingest. Такой пересмотр должен определить threat model, authentication/authorization, TLS, audit logging, response/file-count/decompressed-size/compression-ratio limits, encryption/key management и retention личных данных.

### Ссылки

- [План implementation audit и security findings](../implementation-audit-plan.md)
- [Safe public URL fetch и DNS pinning](../../src/knowledge_base/net.py)
- [ArangoDB client и transport warning](../../src/knowledge_base/arango.py)
- [Config precedence](../../src/knowledge_base/config.py)
- [Loopback-only Compose runtime](../../compose/arangodb.compose.yml)
- [Generated export boundary](../../src/knowledge_base/exporting.py)
- [Integration database isolation](../../tests/integration/conftest.py)
- [Book Cube archive reader](../../src/knowledge_base/sources/book_cube.py)
- [Medium archive reader](../../src/knowledge_base/sources/medium_export.py)
- [ADR 0004: локальный read-only MCP server](0004-local-read-only-mcp-server-for-knowledge-base.md)
- [PR #9: security hardening](https://github.com/polomodov/knowledge-base/pull/9)
- [PR #10: security review follow-ups](https://github.com/polomodov/knowledge-base/pull/10)
- [PR #28: integration database isolation](https://github.com/polomodov/knowledge-base/pull/28)

## EN

### Context and Problem Statement

`knowledge-base` processes personal writing and owner archives on the owner's machine. At the same time, its CLI can fetch public snapshots by URL, read directories/`.zip` files, connect to ArangoDB with Basic Auth, and create JSONL exports with full normalized text. Even a local single-user tool has external boundaries: a crafted URL can target localhost/cloud metadata or change its DNS answer between validation and connection; an attachment path string or a symlink in a directory archive can move reads outside the archive root; an unbounded response or archive can exhaust memory/CPU; dev credentials or an export can be published accidentally; integration tests can contaminate the real corpus.

The safeguards were added after the implementation audit and PR #9/#10 review, but their architectural rationale was spread across the remediation plan, code, and tests. This ADR is accepted retrospectively as of 2026-07-11, was not prior approval of the changes, and describes the local-first boundary actually in force; it does not claim that the project already has a remote/multi-user security model.

### Y-statement

In the context of a local personal knowledge base with network fetch, archive ingest, ArangoDB Basic Auth, and full-text export, facing SSRF/DNS-rebinding, path-traversal, credential/export leakage, and test contamination risks, we decided for a local-first trust boundary with defense in depth across network-target, path, credential, output, and test boundaries to support the single-user workflow more safely, accepting warnings rather than absolute bans for some operator overrides, trust in the local OS user/host and selected inputs for resource usage, and the absence of per-client/remote authentication and audit models.

### Decision Drivers

- The personal corpus and local archive refs must not become network-accessible because of development defaults.
- Live fetch must reach only an explicitly permitted public HTTP(S) endpoint and must not become a local-file or internal-service reader.
- Hostname validation before a request must protect against DNS rebinding, not only literal private IP addresses.
- An untrusted path value from `result.json` or a ZIP member name must not cause lexical traversal or archive extraction; the owner-supplied directory archive itself remains a trusted v1 input.
- Runtime secrets must be configured locally with clear precedence and must stay out of git.
- Export and integration tests must explicitly account for the database containing personal data.
- Protection must remain compatible with a reproducible local CLI and synthetic CI fixtures.

### Considered Options

- **Trust the local operator and every input.** This adds almost no code, but one URL/path/config mistake can expose files, internal services, credentials, or the personal corpus.
- **Rely only on a container/OS sandbox.** This reduces some impact but does not define application-level rules for redirects, archive refs, exports, and the test database.
- **Build a remote-first service with TLS, identity, RBAC, and an audit log immediately.** This provides a stronger multi-user model but greatly expands the scope of a personal stdio/CLI project.
- **Local-first defense in depth.** Limit default network exposure and validate fetch/path/output/test boundaries in the application while retaining explicit operator overrides where a strict ban would obstruct local work.

### Decision Outcome

Chosen option: local-first defense in depth with the following mandatory boundaries.

ArangoDB and credentials:

- The Compose runtime publishes ArangoDB only on `127.0.0.1`; the default client URL is also loopback. A network-accessible shared/remote deployment is not a supported secure v1 configuration and requires a separate authentication, TLS, and audit decision.
- Real credentials come from the process environment, explicit TOML, or gitignored `config/arangodb.env`; secrets are not committed. A tracked example/default is allowed only as an explicitly local development credential under loopback binding.
- Normal settings use `process env > explicit TOML > config/arangodb.env > dev default` precedence. Password resolution first checks `KB_ARANGO_PASSWORD`, then the environment variable named by `password_env`, a literal TOML password, the gitignored env file, and finally the local development default.
- If the operator points Basic Auth at non-loopback `http://`, the client emits an explicit warning about cleartext credentials and recommends HTTPS. The warning does not make remote HTTP a secure mode.

Network fetch:

- Source adapters allow live fetch only over `http`/`https`; `file:`, `ftp:`, `data:`, and other schemes are rejected before opening.
- Validation proves a public network destination, not source authenticity: there is no expected-host/channel allowlist, redirects may move to another public host, and the response is still imported under the fixed `source_key`. The operator-selected URL is trusted as an identity/content input.
- The hostname is resolved explicitly, and every returned address must be public: private, loopback, link-local, reserved, multicast, and unspecified ranges are forbidden.
- The connection is opened to the validated pinned IP while retaining the original hostname for TLS SNI/certificate validation. This closes the DNS-rebinding window between validation and connection.
- Every redirect repeats URL/address validation and pinning; system proxy handlers are disabled so the request cannot gain an implicit alternate route.
- A blocked/unreachable fetch is returned through the structured source error and recommends a local snapshot rather than weakened checks.

Archives and paths:

- Owner/account archives are read only from an explicitly supplied local path. A directory archive is treated as a trusted owner-supplied input in v1. ZIP members are read as streams and are not extracted onto the filesystem, so ingestion performs no archive extraction based on a member name.
- Attachment refs from Telegram JSON are accepted only as relative archive paths. Absolute POSIX paths, Windows drive/UNC paths, and any lexical `..` traversal are rejected before `stat()` and metadata persistence.
- Directory walkers, file reads, and `stat()` use ordinary filesystem APIs and may follow a symlinked file outside the archive root. This is an accepted v1 limitation: untrusted or symlink-containing directory archives must not be supplied. Supporting such inputs requires resolved-containment/no-follow policy; the ZIP flow does not have this directory-symlink risk.
- Binary attachment payloads are not copied into ArangoDB; only lexically validated refs and available metadata are stored. Any new extraction/write operation must separately enforce resolved-path containment and symlink-traversal protection.

Availability and resource usage:

- Live fetch reads the response body in full; Telegram ZIP reads `result.json` in full and stream-hashes every member; Medium reads posts in full and also walks the archive for its manifest. V1 has no maximum response bytes, file-count/total-size quotas, or compression-ratio guard against ZIP bombs.
- This is an accepted local-operator limitation: selected URLs and archives are trusted for availability/resource consumption and must not be processed unattended when the source is untrusted. Public-address validation and no extraction do not protect against oversized content.

Outputs and tests:

- `kb export jsonl` contains full normalized document/chunk text and document metadata, including possible local attachment refs, but no raw snapshot payload. Its normal output zone is gitignored `data/generated/`; writing elsewhere is allowed only with an explicit warning about commit/share risk.
- Integration tests use a separate `knowledge_base_integration_test` database by default and reset it before the run; CI additionally uses a dedicated service container. An explicitly set `KB_ARANGO_DATABASE` is treated as deliberate operator responsibility and is not replaced automatically.
- Only synthetic fixtures remain in the repository. Real archives, generated exports, local configs, and secrets are covered by `.gitignore` and must not be added manually.

Local host, access, and data at rest:

- Loopback ArangoDB and stdio/read-only MCP reduce network and mutation surface but do not authorize separate local clients. Any process under the same OS account that obtains config/environment/DB credentials can read normalized personal text through the CLI, a direct DB client, or MCP; per-client authentication/authorization and audit logging are absent.
- `data/raw/`, the ArangoDB Docker volume, and `data/generated/` store plaintext data under host filesystem permissions and the ordinary backup policy. Application-level encryption at rest, retention enforcement, and secure deletion are absent; v1 relies on the owner's trusted OS account, disk encryption, and backup settings.

### Consequences

- Good: live source fetch cannot normally read local files, loopback/cloud metadata, or an internal private endpoint and is protected from DNS rebinding through address pinning.
- Good: loopback binding, gitignored credentials, and a transport warning reduce the chance of accidentally exposing the personal database with a development password.
- Good: absolute, Windows/UNC, and `..` attachment paths do not produce ordinary lexical traversal, ZIP archives are not extracted, and tests do not contaminate the personal corpus by default.
- Bad: legitimate intranet/private URLs cannot be used through live fetch; they must be saved locally and imported as a snapshot or the trust model must be revisited.
- Bad: warnings for non-loopback cleartext Basic Auth and exports outside `data/generated/` are not hard enforcement and can be deliberately ignored.
- Bad: a directory archive is not isolated from symlink escape; manifest hashing, file reads, or attachment `stat()` may follow a symlink, so v1 trusts both the owner and the directory input itself.
- Bad: response/archive resource limits are absent; a large response, many files, or a ZIP bomb can exhaust process memory, CPU, or time.
- Bad: a local process with credentials can read the corpus without per-client authorization/audit, while data at rest has no application-level encryption, retention, or secure-deletion guarantees.
- Bad: public-address validation does not prove the expected source; an accidental or malicious public URL/redirect can contaminate the fixed logical corpus.
- Neutral: an explicit remote Arango URL and explicit integration database override remain available to an experienced operator but are outside the safe default.
- Neutral: lexical path checks and no ZIP extraction cover only their respective traversal classes; untrusted directories, symlinks, extraction, or writing require stronger filesystem containment.

### Review Plan

Revisit this decision before adding HTTP/remote MCP transport, multiple local identities, automated scheduled/daemon ingest, private-network source adapters, shared object storage, or hosted ArangoDB. Revisit it as well if per-client auth/audit, application-level encryption/retention/secure deletion, an expected-host/source allowlist, enforced output/transport policy, a secret manager, archive extraction, untrusted URL/archive acceptance, or unattended ingest is required. That review must define a threat model, authentication/authorization, TLS, audit logging, response/file-count/decompressed-size/compression-ratio limits, encryption/key management, and personal-data retention.

### Links

- [Implementation audit plan and security findings](../implementation-audit-plan.md)
- [Safe public URL fetch and DNS pinning](../../src/knowledge_base/net.py)
- [ArangoDB client and transport warning](../../src/knowledge_base/arango.py)
- [Configuration precedence](../../src/knowledge_base/config.py)
- [Loopback-only Compose runtime](../../compose/arangodb.compose.yml)
- [Generated export boundary](../../src/knowledge_base/exporting.py)
- [Integration database isolation](../../tests/integration/conftest.py)
- [Book Cube archive reader](../../src/knowledge_base/sources/book_cube.py)
- [Medium archive reader](../../src/knowledge_base/sources/medium_export.py)
- [ADR 0004: local read-only MCP server](0004-local-read-only-mcp-server-for-knowledge-base.md)
- [PR #9: security hardening](https://github.com/polomodov/knowledge-base/pull/9)
- [PR #10: security review follow-ups](https://github.com/polomodov/knowledge-base/pull/10)
- [PR #28: integration database isolation](https://github.com/polomodov/knowledge-base/pull/28)
