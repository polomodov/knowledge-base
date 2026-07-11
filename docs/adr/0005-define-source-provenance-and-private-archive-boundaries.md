# 0005. Зафиксировать границы источников, provenance и приватных архивов / Define source, provenance, and private archive boundaries

```json adr-meta
{
  "id": "0005",
  "titleRu": "Зафиксировать границы источников, provenance и приватных архивов",
  "titleEn": "Define source, provenance, and private archive boundaries",
  "status": "accepted",
  "date": "2026-07-11",
  "deciders": [
    "knowledge-base maintainer"
  ],
  "tags": [
    "sources",
    "provenance",
    "privacy",
    "raw-data"
  ],
  "supersedes": [],
  "supersededBy": []
}
```

## RU

### Контекст и проблема

Первые реальные source adapters появились последовательно: публичный блог `tellmeabout.tech`, публичные snapshots Telegram-канала «Книжный куб», полный владельческий Telegram Desktop archive и Medium account export. Вместе они принесли разные границы доверия и хранения: публичный URL можно получить по сети, а owner/account archive содержит приватный контекст и должен оставаться локальным; небольшой текстовый snapshot можно сохранить inline, но media binaries и полный Medium export нельзя без необходимости копировать в ArangoDB или git.

До этого ADR решения были реализованы и описаны в specs 002–005, `README.md` и коде адаптеров, но не имели отдельной записи о причинах и компромиссах. Этот ADR принят ретроспективно: он фиксирует уже действующий контракт по состоянию на 2026-07-11, а не изображает предварительное согласование реализации.

Без общей границы новый адаптер может потерять воспроизводимость, смешать raw с normalized/generated данными, импортировать чувствительные разделы экспорта как документы, создать коллизии идентификаторов или вернуть результаты без точного пути к первоисточнику.

### Y-statement

В контексте импорта публичных публикаций и приватных владельческих архивов, столкнувшись с разными privacy-рисками, форматами и требованиями к воспроизводимости, мы решили выбрать source-specific adapters с общим provenance-контрактом и явными raw/processed/generated границами, чтобы сохранять происхождение каждого результата и минимизировать копирование личных данных, принимая v1-компромисс логического, а не полного физического разделения данных внутри ArangoDB.

### Драйверы решения

- Каждый document и chunk должен иметь путь назад к `source_key`, raw snapshot, import run, исходному идентификатору и URL/локальному ref, когда он существует.
- Реальные owner/account exports, media и локальные пути не должны попадать в git; в репозитории допустимы только synthetic fixtures.
- Повторный ingest идентичного normalized input не должен создавать дубликаты canonical documents/chunks, а document identity не зависит от времени запуска или локального пути архива. Invocation-level import history в v1 не гарантируется.
- Адаптер должен импортировать только явно выбранный содержательный scope источника, а не все данные, случайно присутствующие в экспорте.
- Поиск одного логического источника должен означать точное сравнение `source_key`, без неявного смешивания разных корпусов; режим импорта и privacy policy задаются отдельными полями и границами.
- Raw inputs, normalized knowledge и generated outputs должны оставаться различимыми и иметь разные правила жизненного цикла.

### Рассмотренные варианты

- **Скопировать весь экспорт и binaries в ArangoDB.** Упрощает автономность базы, но дублирует чувствительные данные, раздувает storage и превращает processed runtime в хранилище приватного архива.
- **Хранить только нормализованные documents без raw provenance.** Минимизирует storage, но не позволяет проверить происхождение, повторно прогнать нормализацию на сохранённом input или объяснить расхождения после обновления parser-а.
- **Применить один универсальный import ко всему содержимому каждого экспорта.** Уменьшает число правил, но делает profile, sessions, IP history, drafts и media случайной частью knowledge corpus.
- **Использовать source-specific adapters с общим provenance-контрактом и минимально необходимым raw представлением.** Сохраняет различия источников и privacy-by-default, но требует явно документировать scope каждого нового адаптера.

### Итоговое решение

Выбран вариант: source-specific adapters с общим provenance-контрактом и минимально необходимым raw представлением.

Граница входов:

- Публичные источники принимают проверенный public HTTP(S) URL или локально сохранённый snapshot. Проверка URL защищает network target, но не подтверждает источник: v1 не применяет expected-host/channel allowlist, и operator-selected public URL/redirect доверяется как authentic content для фиксированного `source_key`. Локальный snapshot является предпочтительным входом для повторного ingest.
- Владельческие Telegram и Medium archives принимаются только как явно указанные локальные directory/`.zip`; адаптеры не получают токены, не входят в аккаунты и не обходят ограничения платформ.
- Оригинальные реальные snapshots/archives и media хранятся локально: предпочтительно в gitignored `data/raw/` для project-local inputs либо вне репозитория. В git коммитятся только synthetic fixtures, specs, схемы и документация.

Provenance и raw representation:

- Каждый ingest upsert-ит import-run record со стабильным `source_key`, input kind/ref, content/manifest SHA-256 и timestamps; связанный raw-snapshot record хранит media type, content/manifest hash, payload или storage ref. Documents/chunks связываются с raw snapshot и source явными metadata/edges. `import_run_key` включает source, input kind, hash и день (`now[:10]`), поэтому повтор того же input в тот же день обновляет одну запись, а не сохраняет отдельное событие каждого invocation.
- Этот provenance обеспечивает source traceability, но не полный computational lineage: import run не фиксирует code/parser/schema revision, полный effective config, chunking parameters или embedding fingerprint. Даже при сохранённом archive точный historical replay после изменения кода/config в v1 не гарантирован.
- Небольшой публичный или synthetic текстовый snapshot, а также `result.json` Telegram archive могут храниться в `raw_snapshots` inline; локальный archive остаётся исходником, а запись содержит его ref и hash/manifest context.
- Для Medium account export raw snapshot содержит только детерминированный manifest с relative paths, sizes и content hashes. Полные HTML/profile/session/IP/social payloads не копируются в raw snapshot; опубликованные `posts/*.html` читаются из локального архива для нормализации.
- Telegram/Medium image, video и file attachments сохраняются только как metadata references с relative path/type/size/hash context, когда он доступен. Binary payloads не записываются в ArangoDB и git.

Normalized scope и идентичность:

- Публичные публикации импортируются со статусом `published`. Medium drafts исключены по умолчанию и появляются только при явном `--include-drafts` со статусом `draft`; profile, sessions, IP history, notes, bookmarks, claps и following lists не становятся documents.
- Canonical IDs выводятся из устойчивого origin identifier: Telegram message id, Medium post id, а для feed posts — URL path с hash-компонентом от точного path и fallback на GUID с hash-компонентом. Scheme, host и query не входят в feed canonical ID. Итоговый document key включает `source_key`, поэтому одинаковые origin ids разных логических источников не конфликтуют.
- Идентичность topic-узлов, напротив, намеренно общая для всего corpus: один Unicode-safe canonical `topic_key` по нормализованному label переиспользуется всеми adapters, чтобы одинаковая тема связывала разные источники, а кириллические labels не схлопывались в один fallback key.
- `source_key` является точным идентификатором логического источника/corpus, а не конкретного adapter entry point, input mode, ACL или privacy label. Публичный snapshot и owner archive «Книжного куба» намеренно используют один `source_key="book-cube"`, чтобы одинаковые сообщения сходились к одной document identity; input kind/ref и raw/import provenance различают способы получения. Source-scoped text/semantic/hybrid/graph retrieval использует exact single-source match, но не является контролем доступа; multi-source selection требует отдельного расширения контракта.
- Схождение public/owner imports ограничено document identity. Document upsert обновляет текущую запись, но ingest не удаляет старые chunks, topic links или raw/provenance edges; поскольку chunk key зависит от text hash, отличающаяся HTML/JSON-нормализация одного сообщения может оставить прежние чанки доступными retrieval. Versioning и stale-representation reconciliation в v1 отсутствуют.
- `--include-drafts` является только ingest opt-in. Однажды импортированный draft не удаляется повторным ingest без флага и участвует в обычных text/semantic/hybrid/local/global/MCP reads и JSONL export: query-time draft filter или ACL в v1 отсутствует. Для приватного draft corpus владелец должен не импортировать drafts либо использовать отдельную БД до появления visibility policy.

Границы зон:

- On-disk `data/raw/`, `data/processed/` и `data/generated/` имеют разные назначения и игнорируются git. `data/processed/` пока не материализуется: normalized documents/chunks живут в ArangoDB, а exports — в `data/generated/`.
- ArangoDB v1 физически содержит отдельные коллекции raw snapshots, normalized records и derived indexes. Это явное ограничение единого runtime, а не отказ от логических границ; generated outputs не являются источником истины и должны ссылаться на исходные documents/provenance.

### Последствия

- Хорошо: любой импортированный документ и retrieval-result можно связать с конкретным источником, snapshot/archive и import run.
- Хорошо: приватные archives и binaries остаются локальными, а Medium по умолчанию не превращает account metadata и drafts в searchable corpus.
- Хорошо: stable canonical IDs и exact logical `source_key` не дают повтору идентичного input дублировать canonical documents/chunks, сводят public/owner imports к одной document identity и делают source-scoped retrieval предсказуемым.
- Плохо: для любого повторного прогона владелец должен сохранить локальный archive/snapshot; это необходимо, но недостаточно для точного historical replay без code/config fingerprints.
- Плохо: новый источник требует отдельного adapter contract и решения, какие части export-а являются documents, raw-only данными или исключаются полностью.
- Плохо: ingest opt-in для drafts не обеспечивает дальнейшую изоляцию; импортированный draft доступен всем локальным read surfaces текущей БД.
- Плохо: changed representation того же document id может оставить stale chunks/edges; отсутствие invocation-level run history и code/config fingerprints не позволяет гарантировать полный audit trail или точный historical replay.
- Нейтрально: inline raw payloads и normalized records находятся в одном ArangoDB runtime v1, хотя остаются разными типами данных и коллекциями.
- Нейтрально: attachment references могут стать недействительными после перемещения локального архива; hash/manifest provenance сохраняет идентичность, но не гарантирует доступность файла.
- Нейтрально: feed canonical ID игнорирует scheme/host/query; разные URL с одинаковым path внутри одного `source_key` сходятся к одной identity.

### План пересмотра

Пересмотреть решение, если corpus потребует object storage для raw/binaries, появится multi-user, remote или unattended ingest, понадобится индексировать binary media, expected-host/source authenticity allowlist, несколько source filters в одном запросе, query-time visibility/ACL для drafts, invocation-level import audit, content versioning/stale-chunk reconciliation, полный computational-lineage fingerprint либо единый ArangoDB runtime перестанет обеспечивать достаточную физическую изоляцию. Для каждого нового типа owner/account export отдельно проверить privacy scope, provenance fields, draft policy и стратегию хранения raw до реализации адаптера.

### Ссылки

- [Архитектура: зоны данных и реализованный v1](../architecture.md)
- [Tell Me About Tech Source spec](../../specs/002-tellmeabout-tech-source/spec.md)
- [Book Cube Telegram Source spec](../../specs/003-book-cube-telegram-source/spec.md)
- [Book Cube Owner Archive Import spec](../../specs/004-book-cube-owner-archive-import/spec.md)
- [Medium Export Source spec](../../specs/005-medium-export-source/spec.md)
- [Общие source contracts](../../src/knowledge_base/sources/contracts.py)
- [Детерминированные identifiers](../../src/knowledge_base/ids.py)
- [Первоначальная реализация реальных source adapters](https://github.com/polomodov/knowledge-base/commit/8288872)
- [Первоначальная реализация owner archive ingest](https://github.com/polomodov/knowledge-base/commit/542708b)
- [Первоначальная реализация Medium export ingest](https://github.com/polomodov/knowledge-base/commit/d5bcd3b)
- [PR #2: общий Unicode-safe topic key](https://github.com/polomodov/knowledge-base/pull/2)
- [PR #18: canonical IDs и archive hashing](https://github.com/polomodov/knowledge-base/pull/18)

## EN

### Context and Problem Statement

The first real source adapters arrived incrementally: the public `tellmeabout.tech` blog, public snapshots of the “Book Cube” Telegram channel, the full owner Telegram Desktop archive, and the Medium account export. Together they introduced different trust and storage boundaries: a public URL can be fetched over the network, while an owner/account archive contains private context and must remain local; a small text snapshot can be stored inline, but media binaries and the complete Medium export should not be copied into ArangoDB or git without need.

Before this ADR, the decisions had been implemented and described across specs 002–005, `README.md`, and adapter code, but there was no dedicated record of their rationale and trade-offs. This ADR is accepted retrospectively: it records the contract already in force as of 2026-07-11 rather than presenting the implementation as prospectively approved.

Without a common boundary, a new adapter could lose reproducibility, mix raw data with normalized/generated data, turn sensitive export sections into documents, create identifier collisions, or return results without an exact path back to the primary source.

### Y-statement

In the context of ingesting public publications and private owner archives, facing different privacy risks, formats, and reproducibility requirements, we decided for source-specific adapters with a shared provenance contract and explicit raw/processed/generated boundaries to preserve the origin of every result and minimize copying of personal data, accepting the v1 trade-off of logical rather than complete physical separation inside ArangoDB.

### Decision Drivers

- Every document and chunk must provide a path back to its `source_key`, raw snapshot, import run, original identifier, and URL/local ref when one exists.
- Real owner/account exports, media, and local paths must stay out of git; only synthetic fixtures may be stored in the repository.
- Re-ingesting identical normalized input must not create duplicate canonical documents/chunks, and document identity does not depend on run time or a local archive path. V1 does not guarantee invocation-level import history.
- An adapter must ingest only the explicitly selected content scope of a source, not every datum that happens to be present in an export.
- Searching one logical source must mean an exact `source_key` comparison without silently mixing distinct corpora; import mode and privacy policy are represented by separate fields and boundaries.
- Raw inputs, normalized knowledge, and generated outputs must remain distinguishable and have different lifecycle rules.

### Considered Options

- **Copy every export and binary into ArangoDB.** This makes the database self-contained, but duplicates sensitive data, expands storage, and turns the processed runtime into a private archive store.
- **Keep only normalized documents without raw provenance.** This minimizes storage but prevents origin verification, rerunning normalization on retained input, and explanation of differences after parser changes.
- **Apply one universal import to all content in every export.** This reduces the number of rules but makes profile, sessions, IP history, drafts, and media accidental parts of the knowledge corpus.
- **Use source-specific adapters with a shared provenance contract and a minimally necessary raw representation.** This preserves source differences and privacy by default, but requires explicit scope documentation for each new adapter.

### Decision Outcome

Chosen option: source-specific adapters with a shared provenance contract and a minimally necessary raw representation.

Input boundary:

- Public sources accept a validated public HTTP(S) URL or a locally saved snapshot. URL validation protects the network target but does not authenticate the source: v1 has no expected-host/channel allowlist, and an operator-selected public URL/redirect is trusted as authentic content for the fixed `source_key`. A local snapshot is the preferred input for repeat ingest.
- Owner Telegram and Medium archives are accepted only as an explicitly supplied local directory/`.zip`; adapters do not receive tokens, log into accounts, or bypass platform restrictions.
- Original real snapshots/archives and media are kept locally: preferably under gitignored `data/raw/` for project-local inputs or outside the repository. Only synthetic fixtures, specs, schemas, and documentation are committed to git.

Provenance and raw representation:

- Every ingest upserts an import-run record with a stable `source_key`, input kind/ref, content/manifest SHA-256, and timestamps; the linked raw-snapshot record carries media type, content/manifest hash, and a payload or storage ref. Documents/chunks are connected to the raw snapshot and source through explicit metadata/edges. `import_run_key` includes source, input kind, hash, and day (`now[:10]`), so repeating the same input on the same day updates one record rather than preserving a separate event per invocation.
- This provenance provides source traceability but not complete computational lineage: an import run does not record code/parser/schema revision, the complete effective configuration, chunking parameters, or an embedding fingerprint. Even with the archive retained, exact historical replay after code/config changes is not guaranteed in v1.
- A small public or synthetic text snapshot, as well as a Telegram archive `result.json`, may be stored inline in `raw_snapshots`; the local archive remains the source input, while the record carries its ref and hash/manifest context.
- For a Medium account export, the raw snapshot contains only a deterministic manifest with relative paths, sizes, and content hashes. Full HTML/profile/session/IP/social payloads are not copied into the raw snapshot; published `posts/*.html` files are read from the local archive for normalization.
- Telegram/Medium image, video, and file attachments are kept only as metadata references with relative path/type/size/hash context when available. Binary payloads are not written to ArangoDB or git.

Normalized scope and identity:

- Public publications are ingested with `published` status. Medium drafts are excluded by default and appear only with explicit `--include-drafts` as `draft`; profile, sessions, IP history, notes, bookmarks, claps, and following lists do not become documents.
- Canonical IDs come from a stable origin identifier: a Telegram message id, a Medium post id, or, for feed posts, the URL path with a hash of the exact path and a hashed GUID fallback. Scheme, host, and query are not part of a feed canonical ID. The resulting document key includes `source_key`, so matching origin ids from distinct logical sources do not collide.
- Topic-node identity, by contrast, is deliberately corpus-wide: every adapter reuses one Unicode-safe canonical `topic_key` derived from the normalized label so that the same topic connects different sources and Cyrillic labels do not collapse into one fallback key.
- `source_key` is the exact logical source/corpus identifier, not a particular adapter entry point, input mode, ACL, or privacy label. Public snapshots and the owner archive of “Book Cube” deliberately share `source_key="book-cube"` so matching messages converge on one document identity; input kind/ref and raw/import provenance distinguish acquisition modes. Source-scoped text/semantic/hybrid/graph retrieval uses an exact single-source match but is not access control; multi-source selection requires a separate contract extension.
- Convergence of public/owner imports is limited to document identity. Document upsert updates the current record, but ingest does not remove old chunks, topic links, or raw/provenance edges; because a chunk key includes a text hash, differing HTML/JSON normalization of one message may leave prior chunks visible to retrieval. V1 has no versioning or stale-representation reconciliation.
- `--include-drafts` is only an ingest opt-in. Once imported, a draft is not removed by re-ingesting without the flag and participates in ordinary text/semantic/hybrid/local/global/MCP reads and JSONL export: v1 has no query-time draft filter or ACL. Until a visibility policy exists, the owner must avoid importing a private draft corpus or use a separate database.

Zone boundaries:

- On-disk `data/raw/`, `data/processed/`, and `data/generated/` serve different purposes and are ignored by git. `data/processed/` is not materialized yet: normalized documents/chunks live in ArangoDB, while exports live in `data/generated/`.
- ArangoDB v1 physically holds separate collections for raw snapshots, normalized records, and derived indexes. This is an explicit single-runtime limitation, not a rejection of logical boundaries; generated outputs are not sources of truth and must refer to original documents/provenance.

### Consequences

- Good: every imported document and retrieval result can be traced to a specific source, snapshot/archive, and import run.
- Good: private archives and binaries remain local, while Medium does not turn account metadata and drafts into a searchable corpus by default.
- Good: stable canonical IDs and an exact logical `source_key` prevent repeated identical input from duplicating canonical documents/chunks, converge public/owner imports on one document identity, and keep source-scoped retrieval predictable.
- Bad: any rerun requires the owner to retain the local archive/snapshot; that is necessary but insufficient for exact historical replay without code/config fingerprints.
- Bad: each new source needs an adapter contract deciding which export parts are documents, raw-only data, or excluded entirely.
- Bad: ingest opt-in for drafts does not provide subsequent isolation; an imported draft is available through every local read surface of the current database.
- Bad: a changed representation of the same document id can leave stale chunks/edges; missing invocation-level run history and code/config fingerprints prevent a complete audit trail or guaranteed exact historical replay.
- Neutral: inline raw payloads and normalized records share one ArangoDB v1 runtime, although they remain different data types and collections.
- Neutral: attachment references may become invalid after moving a local archive; hash/manifest provenance preserves identity but does not guarantee file availability.
- Neutral: a feed canonical ID ignores scheme/host/query; different URLs with the same path under one `source_key` converge on one identity.

### Review Plan

Revisit this decision if the corpus requires object storage for raw data/binaries, multi-user, remote, or unattended ingest appears, binary media must be indexed, an expected-host/source-authenticity allowlist is needed, several source filters are required in one query, query-time draft visibility/ACL, invocation-level import audit, content versioning/stale-chunk reconciliation, or a complete computational-lineage fingerprint is required, or the single ArangoDB runtime no longer provides sufficient physical isolation. For each new owner/account export type, review its privacy scope, provenance fields, draft policy, and raw storage strategy before implementing the adapter.

### Links

- [Architecture: data zones and implemented v1](../architecture.md)
- [Tell Me About Tech Source spec](../../specs/002-tellmeabout-tech-source/spec.md)
- [Book Cube Telegram Source spec](../../specs/003-book-cube-telegram-source/spec.md)
- [Book Cube Owner Archive Import spec](../../specs/004-book-cube-owner-archive-import/spec.md)
- [Medium Export Source spec](../../specs/005-medium-export-source/spec.md)
- [Shared source contracts](../../src/knowledge_base/sources/contracts.py)
- [Deterministic identifiers](../../src/knowledge_base/ids.py)
- [Initial real source-adapter implementation](https://github.com/polomodov/knowledge-base/commit/8288872)
- [Initial owner-archive ingest implementation](https://github.com/polomodov/knowledge-base/commit/542708b)
- [Initial Medium-export ingest implementation](https://github.com/polomodov/knowledge-base/commit/d5bcd3b)
- [PR #2: shared Unicode-safe topic key](https://github.com/polomodov/knowledge-base/pull/2)
- [PR #18: canonical IDs and archive hashing](https://github.com/polomodov/knowledge-base/pull/18)
