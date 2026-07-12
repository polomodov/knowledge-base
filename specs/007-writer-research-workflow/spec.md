# Feature Specification: Writer/Research Workflow

**Feature Branch**: `codex/007-writer-research-workflow`

**Created**: 2026-07-12

**Status**: Draft

**Input**: User description: "Спроектировать волну 5: writer/research workflow поверх готовых retrieval, GraphRAG, MCP и visualization read models."

**EN summary**: Add a provenance-first local workflow that turns a research topic into a reproducible evidence bundle, hands that bundle to a trusted external writing agent, and validates and stores the returned draft or summary without mixing generated text with canonical knowledge data.

## Clarifications

### Session 2026-07-12

- Q: Входит ли генеративный draft в обязательный scope Feature 007? → A: Да; Feature 007 обязательно включает и research dossier, и генерацию drafts/summaries.
- Q: Где выполняется генерация draft/summary? → A: Во внешнем доверенном writing-agent; `knowledge-base` формирует handoff, проверяет citations и сохраняет возвращённый результат.
- Q: Как writing-agent получает dossier и возвращает результат? → A: Через структурированный файловый round-trip: versioned handoff package наружу и structured writing-output package (`draft` или `summary`) обратно в локальную проверку/импорт.
- Q: Где действует published-only default V5? → A: Только внутри V5 research/dossier/handoff; существующие CLI и MCP read-контракты сохраняют текущее поведение.
- Q: Как пользователь курирует dossier перед handoff? → A: Операциями include/exclude/pin; каждое изменение создаёт новую immutable revision и не переписывает исходную подборку.

## Пользовательские сценарии и проверка

### User Story 1 — собрать исследовательское досье по теме (Priority: P1)

Как автор или исследователь, я хочу задать тему и получить компактное досье из наиболее релевантных фрагментов собственной базы знаний, чтобы начать работу не с ручного повторного поиска, а с проверяемой подборки свидетельств.

**Почему этот приоритет**: это минимальный полезный writer/research workflow; он использует уже готовый поиск и сразу даёт ценность без генерации нового текста.

**Independent Test**: на корпусе с несколькими источниками сформировать досье по теме и проверить, что оно содержит релевантные фрагменты, понятную группировку и разрешимые ссылки на происхождение каждого фрагмента.

**Acceptance Scenarios**:

1. **Given** проиндексированный корпус с опубликованными материалами, **When** пользователь запускает исследование по теме, **Then** он получает человекочитаемое досье и машиночитаемый манифест с одинаковым набором свидетельств.
2. **Given** результаты из нескольких источников, **When** досье сформировано, **Then** каждый фрагмент показывает первичный источник, документ, точное место в документе и контекст импорта.
3. **Given** несколько совпадений из одного документа, **When** формируется досье, **Then** повторы не вытесняют разнообразие источников, а порядок свидетельств остаётся объяснимым.
4. **Given** draft с более сильным retrieval-сигналом, чем опубликованные документы, **When** пользователь не включал drafts, **Then** draft не влияет на V5 evidence selection и не раскрывается через dossier, derived context или handoff.

---

### User Story 2 — проверить, курировать и воспроизвести подборку (Priority: P2)

Как исследователь, я хочу проверить все цитаты, включить, исключить или закрепить evidence и понять, на каком состоянии корпуса и с какими параметрами получена каждая ревизия, чтобы безопасно подготовить подборку для статьи, исследования или writing-agent.

**Почему этот приоритет**: без воспроизводимости и проверки provenance подборка превращается в ещё один непрозрачный generated output.

**Independent Test**: создать новую revision операциями include/exclude/pin, убедиться, что исходная revision не изменилась и lineage зафиксирован, затем проверить сохранённое досье против неизменённого корпуса и смоделировать missing/changed citation.

**Acceptance Scenarios**:

1. **Given** неизменённый корпус и те же параметры исследования, **When** пользователь повторяет сборку, **Then** содержательная часть и порядок свидетельств совпадают, кроме служебных временных меток и идентификатора запуска.
2. **Given** сохранённая подборка, **When** пользователь запускает её проверку, **Then** каждая цитата либо подтверждается, либо получает явный статус отсутствия, изменения или скрытия текущим visibility scope.
3. **Given** запрос без достаточных свидетельств, **When** workflow завершается, **Then** пользователь получает честный результат «данных недостаточно», а не правдоподобно заполненное досье.
4. **Given** проверенное dossier, **When** пользователь исключает нерелевантный evidence, включает дополнительный evidence или закрепляет ключевой фрагмент, **Then** создаётся новая immutable revision с записью операции и parent revision, а предыдущая остаётся неизменной.

---

### User Story 3 — подготовить цитируемый draft или summary (Priority: P3)

Как автор, я хочу передать выбранное исследовательское досье доверенному writing-agent, а затем принять и проверить его черновик или summary, чтобы ускорить письмо и при этом сохранить различие между источниками, извлечёнными фрагментами и сгенерированным текстом.

**Почему этот приоритет**: генерация полезна только после появления надёжного слоя evidence и citations, поэтому реализуется после них, но остаётся обязательной частью завершённой Feature 007.

**Independent Test**: сформировать versioned handoff package из заранее проверенного досье, передать его доверенному writing-agent и по отдельности вернуть structured writing-output packages видов `draft` и `summary`; для каждого проверить схему, маркировку как generated output и наличие у каждого содержательного раздела ссылок на evidence либо явной отметки «не подтверждён корпусом».

**Acceptance Scenarios**:

1. **Given** проверенное исследовательское досье, **When** пользователь создаёт handoff package, передаёт его writing-agent и импортирует возвращённый writing-output package вида `draft` или `summary`, **Then** package проверяется, результат сохраняется отдельно от досье и canonical данных и ссылается на конкретную версию подборки.
2. **Given** фрагмент черновика без опоры в досье, **When** результат проверяется, **Then** такой фрагмент явно отмечается как неподтверждённый, а не получает фиктивную цитату.
3. **Given** недоступный writing-agent, **When** пользователь готовит handoff для черновика, **Then** проверенное досье и handoff остаются пригодными к ручному использованию и не повреждаются.

### Edge Cases

- Запрос пуст, слишком короток или состоит только из служебных символов.
- По запросу нет результатов либо все результаты отсеяны порогом релевантности.
- Один документ или один массовый топик доминирует в выдаче и снижает разнообразие evidence.
- Документ найден, но его chunk или исходная ссылка больше не разрешаются.
- Derived-индексы отсутствуют, частично недоступны или выглядят старее корпуса.
- В базе есть imported drafts, но пользователь не дал явного разрешения включить их.
- Пользователь явно включает drafts; итоговый артефакт должен заметно отражать расширенную приватную область.
- Выходной путь находится вне выделенной зоны generated outputs.
- Сборка прерывается после создания части файлов.
- Цитируемый текст содержит Unicode, emoji, Markdown/HTML-разметку или управляющие символы.
- Два запуска используют одинаковый запрос, но разные параметры или состояние индексов.
- Черновик запрошен для пустого, непроверенного или устаревшего досье.
- Handoff или writing-output package имеет неподдерживаемую версию схемы, повреждён, ссылается на другое dossier либо содержит неизвестные evidence identifiers.
- Curation operation ссылается на отсутствующий evidence, повторяет уже применённое состояние либо выполняется над revision, которая перестала проходить validation.

## Требования

### Функциональные требования

- **FR-001**: Система MUST позволять локальному пользователю начать исследование с темы и необязательных ограничений по источнику, периоду и объёму результата.
- **FR-002**: Система MUST строить подборку только из нормализованных материалов и доступных read-моделей, не используя raw payload как текст для цитирования.
- **FR-003**: Система MUST создавать согласованную пару: человекочитаемое исследовательское досье и машиночитаемый манифест той же подборки.
- **FR-004**: Каждый evidence fragment MUST иметь точную цитату, позволяющую восстановить source, document, chunk или эквивалентный фрагмент, позицию в тексте, исходную ссылку и доступный import/raw provenance context.
- **FR-005**: Внутри V5 research workflow система MUST по умолчанию включать только опубликованные документы; drafts MAY включаться только явным действием пользователя, которое отражается в metadata и видимой маркировке результата.
- **FR-006**: Система MUST NOT помещать в досье или handoff структурные config/env credentials, cookies, raw snapshot payload, archive references или локальные file paths. Exact excerpts считаются потенциально чувствительным пользовательским текстом; система MUST NOT заявлять, что они автоматически очищены от секретов.
- **FR-007**: Система MUST устранять точные дубли evidence и ограничивать доминирование одного документа, сохраняя объяснимый порядок по релевантности и разнообразию.
- **FR-008**: Система MUST сохранять исходную формулировку запроса, применённые ограничения, сведения о состоянии корпуса и read-моделей, а также время сборки.
- **FR-009**: При неизменённых входных данных и параметрах система MUST воспроизводить тот же содержательный набор и порядок evidence, не считая служебных идентификаторов и временных меток.
- **FR-010**: Система MUST записывать generated artifacts атомарно и MUST NOT оставлять внешне валидную частичную подборку после ошибки.
- **FR-011**: Система MUST предупреждать пользователя, если output покидает выделенную generated-зону или если доступные признаки указывают на неполные либо устаревшие derived read models.
- **FR-012**: Пользователь MUST иметь возможность проверить сохранённую подборку; проверка MUST сообщать для каждой citation состояние `valid`, `missing`, `changed` или `hidden` и итоговый статус всего досье.
- **FR-013**: Сборка и проверка research artifacts MUST NOT изменять canonical collections, raw data, processed documents или derived indexes.
- **FR-014**: При отсутствии достаточных свидетельств система MUST возвращать явный no-evidence результат и MUST NOT подменять его сгенерированным содержанием.
- **FR-015**: Исследовательское досье MUST различать дословные excerpts, служебные группировки и generated summaries, если последние присутствуют.
- **FR-016**: Система MUST принимать generated draft или summary от доверенного внешнего writing-agent только вместе со ссылкой на явно выбранную версию досье и её evidence identifiers.
- **FR-017**: Каждый содержательный раздел generated draft или summary MUST иметь хотя бы одну разрешимую citation либо явную маркировку, что раздел не подтверждён корпусом.
- **FR-018**: Generated draft или summary MUST сохранять сведения о версии досье, output kind, доступном контексте внешнего agent run и времени создания и MUST быть однозначно обозначен как generated output, а не source of truth.
- **FR-019**: Недоступность внешнего writing-agent MUST NOT мешать сборке, чтению и проверке extractive research dossier или созданию переносимого handoff.
- **FR-020**: Повторная сборка MUST создавать новую адресуемую revision и MUST NOT перезаписывать ранее сохранённое dossier на месте.
- **FR-021**: Feature 007 MUST считаться завершённой только после независимой приёмки как research dossier/citation workflow, так и generated draft/summary workflow.
- **FR-022**: `knowledge-base` MUST NOT непосредственно вызывать генеративную модель, хранить credentials её provider или переносить write-операции в существующий read-only MCP server.
- **FR-023**: Система MUST создавать из выбранного проверенного dossier переносимый versioned handoff package, содержащий evidence, citation contract и достаточный context для writing-agent, но не содержащий raw payload, структурные credentials/cookies или локальные приватные пути.
- **FR-024**: Система MUST принимать только structured writing-output package (`draft` или `summary`) поддерживаемой версии, связанный с ожидаемым dossier revision; package с неизвестными citations, несовпадающим dossier identifier или нарушенной структурой MUST быть отклонён без сохранения валидного generated artifact.
- **FR-025**: V5 visibility scope MUST применяться до ранжирования кандидатов, graph expansion и формирования derived context; скрытый draft не должен влиять на published-only dossier косвенно.
- **FR-026**: Feature 007 MUST NOT менять default visibility существующих CLI search, graph и MCP read surfaces; возможная глобальная политика является отдельным контрактным изменением.
- **FR-027**: Пользователь MUST иметь возможность включить доступный evidence, исключить evidence или закрепить его приоритет перед созданием handoff; система MUST проверять, что операция ссылается на разрешимый evidence в допустимом visibility scope.
- **FR-028**: Каждая curation operation MUST создавать новую immutable dossier revision с parent reference и журналом применённых изменений; изменение существующей revision на месте MUST NOT допускаться.
- **FR-029**: Создание любого handoff MUST требовать явного подтверждения внешнего раскрытия exact excerpts и записывать это подтверждение в package; handoff с drafts MUST дополнительно требовать отдельного подтверждения draft evidence.
- **FR-030**: В default generated-зоне система MUST отказываться следовать symlink-компонентам output path и MUST создавать новые package directories/files с owner-only permissions на поддерживаемой POSIX-платформе; explicit output вне зоны MUST сохранять symlink refusal и добавлять отдельное unsafe-location warning/acknowledgement.
- **FR-031**: Writing-output contract и independent acceptance MUST одинаково покрывать оба output kinds: `draft` и `summary`.

### Границы scope

В V5 входят provenance-first research dossier, проверяемый citation manifest, воспроизводимые ревизии и обязательный round-trip: подготовить handoff для доверенного writing-agent, принять его generated draft или summary, проверить citations и сохранить результат. Feature 007 не считается завершённой без обоих контуров, хотя dossier должен оставаться самостоятельно пригодным без writing-agent.

В V5 не входят:

- ingest новых источников и изменение source contracts;
- автоматический rebuild индексов в рамках research workflow;
- remote или multi-user сервис, совместное редактирование и access-control модель;
- write-операции через существующий read-only MCP server;
- встроенный LLM client, собственный генеративный provider и хранение model credentials;
- сохранение research bundles или drafts как canonical documents в основной базе;
- интернет-фактчекинг, внешнее обогащение источников и автоматическая публикация;
- извлечение works/authors или новая entity-extraction подсистема.

### Ключевые сущности

- **Research Request**: тема исследования, необязательные ограничения, требуемый объём и политика видимости документов.
- **Research Dossier**: адресуемая ревизия подборки, содержащая сгруппированные evidence fragments, build context и общий validation status.
- **Curation Operation**: адресуемое действие include, exclude или pin над evidence, записанное как причина перехода от parent revision к новой dossier revision.
- **Evidence Fragment**: точный извлечённый фрагмент с релевантностью, стабильной citation identity, границами текста и provenance.
- **Citation**: проверяемая связь evidence или generated section с первичным нормализованным материалом и его происхождением.
- **Handoff Package**: переносимая versioned-проекция одного проверенного dossier с evidence, citation contract и context, предназначенная для внешнего writing-agent.
- **Writing Output Package**: структурированный ответ writing-agent вида `draft` или `summary` с dossier identity, generated content, evidence references и доступным agent-run context, который должен пройти проверку до сохранения.
- **Generated Writing Artifact**: проверенный производный draft или summary для одной версии dossier, с явной маркировкой generated и coverage ссылками на evidence.
- **Validation Result**: результат проверки dossier, handoff, writing-output package или imported writing artifact, включая статусы отдельных ссылок и причины рассогласований.

## Критерии успеха

### Измеримые результаты

- **SC-001**: На текущем корпусе пользователь получает первое пригодное к чтению исследовательское досье не более чем за 30 секунд после запуска workflow.
- **SC-002**: 100% evidence fragments в успешно проверенном досье разрешаются до существующего нормализованного материала и его provenance.
- **SC-003**: Два запуска на неизменённом корпусе с одинаковыми параметрами дают 100% совпадение содержательного набора и порядка evidence после исключения служебных временных полей.
- **SC-004**: Ни один draft-документ не появляется в артефакте без явного opt-in; при opt-in каждый итоговый файл заметно сообщает о наличии drafts.
- **SC-005**: Пользователь может от любого evidence fragment перейти к исходному URL или доступному локальному provenance context не более чем за два действия.
- **SC-006**: Для пустой выдачи, недоступного read-компонента и прерванной записи не создаётся артефакт, который ошибочно выглядит как успешно проверенное досье.
- **SC-007**: 100% содержательных разделов успешно проверенного generated draft или summary имеют хотя бы одну валидную citation либо явную маркировку отсутствия подтверждения.
- **SC-008**: Создание и проверка research artifacts не изменяют количество или содержимое canonical, raw, processed и derived records.
- **SC-009**: 100% curation operations отражены в lineage новой revision, а хэши и содержимое всех parent revisions остаются неизменными.
- **SC-010**: Каждый handoff содержит recorded egress acknowledgement; package directories/files в default generated-зоне создаются owner-only, а symlink output paths отклоняются во всех acceptance tests.

## Допущения

- Основной пользователь — владелец локальной персональной базы знаний; remote и multi-user сценарии отсутствуют.
- Existing retrieval, GraphRAG, MCP и visualization read models считаются входными зависимостями V5, но workflow обязан честно деградировать при недоступности необязательных слоёв.
- Реализация идёт extractive-first: dossier и citations должны работать без генеративной модели; drafts/summaries реализуются следующим зависимым инкрементом, но обязательны для завершения Feature 007.
- Research artifacts по умолчанию хранятся file-first в выделенной generated-зоне и не становятся canonical документами.
- Published-only является безопасным default; включение импортированных drafts — отдельное явное действие локального владельца.
- Published-only ограничен V5 pipeline и применяется внутри всех его retrieval/expansion стадий; legacy CLI/MCP reads сохраняют текущую совместимость и не используются как готовый неотфильтрованный источник handoff.
- Первичный пользовательский entry point — локальный command workflow; существующий MCP сохраняет read-only boundary.
- Генерацию выполняет доверенный внешний writing-agent; `knowledge-base` не выбирает provider, не вызывает модель и не хранит её credentials, а отвечает за handoff, citation validation и generated-artifact boundary.
- Обмен с writing-agent выполняется через versioned files: handoff package создаётся локально, а writing-output package проходит локальную проверку и импорт; прямой MCP write и самостоятельная запись агентом в generated-зону не используются.
- Статус `published` не является разрешением на внешнее раскрытие: пользователь просматривает selected evidence и подтверждает каждый handoff; система исключает структурные secret/path fields, но не обещает автоматическую secret-redaction внутри unstructured excerpts.
- Сохранённые dossier revisions считаются immutable evidence snapshots; новая сборка или curation через include/exclude/pin создаёт новую revision с явным parent lineage.
