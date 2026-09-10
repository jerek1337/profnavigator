# Архитектура

## Границы

Между платформой и логикой стоят два объекта:

- `Incoming` — входящее событие: кто, откуда, что сделал (`start` / `text` / `action`);
- `Reply` — ответ бота: текст, кнопки, метаданные. Полей, специфичных для платформы, нет.

Адаптер переводит формат мессенджера в `Incoming`, движок возвращает `Reply`, адаптер рисует его средствами своей платформы. Поэтому один сценарий работает в MAX, Telegram, вебе и консоли, а новая платформа подключается отдельным файлом.

## Компоненты

```mermaid
flowchart TB
    subgraph clients["Пользователи"]
        MAXU["Ученик в MAX"]
        TGU["Ученик в Telegram"]
        WEBU["Веб-версия и виджет"]
        TEACH["Учитель: дашборд"]
    end

    subgraph adapters["adapters/"]
        AMAX["max_bot.py<br/>long polling"]
        ATG["telegram_bot.py<br/>long polling"]
        AWEB["web.py<br/>HTTP-сервер"]
        ACLI["cli.py<br/>консоль"]
    end

    subgraph core["core/ — не зависит от платформы"]
        ENG["engine.py<br/>сценарий диалога"]
        MATCH["matching.py<br/>подбор профессий"]
        CONT["content.py<br/>вопросы, профессии, тексты"]
        AI["ai.py + prompts.py<br/>цепочка провайдеров"]
        TPL["templates.py<br/>ответы без ИИ"]
        STOR["storage.py<br/>SQLite"]
        ANL["analytics.py"]
        SCH["scheduler.py<br/>напоминания"]
        LNK["links.py<br/>подписанные ссылки"]
    end

    subgraph ext["Внешние сервисы"]
        MAXAPI["MAX Bot API"]
        TGAPI["Telegram Bot API"]
        LLM["YandexGPT → GigaChat → OpenAI"]
    end

    MAXU <--> AMAX <--> MAXAPI
    TGU <--> ATG <--> TGAPI
    WEBU <--> AWEB
    TEACH --> AWEB

    AMAX -- Incoming/Reply --> ENG
    ATG -- Incoming/Reply --> ENG
    AWEB -- Incoming/Reply --> ENG
    ACLI -- Incoming/Reply --> ENG

    ENG --> MATCH --> CONT
    ENG --> AI --> LLM
    AI -. при сбое .-> TPL --> ENG
    ENG <--> STOR
    ENG --> LNK
    AWEB --> ANL --> STOR
    SCH --> ENG
    SCH --> AMAX
    SCH --> ATG
```

| Модуль | Отвечает за | Не отвечает за |
|---|---|---|
| `adapters/base.py` | контракт адаптера, фоновые задачи, снятие устаревших клавиатур, отсев дублей | логику диалога |
| `adapters/max_bot.py` | MAX Bot API: опрос обновлений, клавиатуры, отправка, редактирование | что отвечать |
| `adapters/telegram_bot.py` | то же для Telegram Bot API | — |
| `adapters/web.py` | HTTP: чат, виджет, дашборд, плакат, `/health` | — |
| `core/engine.py` | сценарий: команды, вопросы, результат, маршрут, вопросы к ИИ | форматы платформ |
| `core/matching.py` | подбор 2 профессий и профиля олимпиады | тексты |
| `core/content.py` | вопросы, 8 профессий, 2 профиля, тексты | логику |
| `core/ai.py` | провайдеры, таймауты, повторы, circuit breaker | содержание промптов |
| `core/storage.py` | пользователи, события, служебные значения | бизнес-правила |
| `core/scheduler.py` | кто и когда получает напоминание | как его отправить |

## Блок-схема сценария

```mermaid
flowchart TD
    START(["Ученик открыл бота"]) --> HELLO["Приветствие<br/>«Начать» / «Как это работает»"]
    HELLO --> CHECK{"Есть незавершённый тест?"}
    CHECK -- да --> RESUME["Продолжить с сохранённого вопроса"]
    CHECK -- нет --> Q0["Вопрос 1: любимый предмет"]
    RESUME --> Q0

    Q0 --> INPUT{"Как ответил?"}
    INPUT -- "кнопка" --> SAVE["Сохранить ответ в БД"]
    INPUT -- "текст распознан" --> SAVE
    INPUT -- "текст не распознан" --> HINT["Подсказка + повтор вопроса"] --> INPUT

    SAVE --> MORE{"Остались вопросы?"}
    MORE -- да --> QN["Следующий вопрос<br/>прогресс + «Назад»"] --> INPUT
    MORE -- нет --> WAIT["Индикатор «анализирую»"]

    WAIT --> MATCH["Подбор 2 профессий<br/>по правилам, без ИИ"]
    MATCH --> AIQ{"ИИ доступен?"}
    AIQ -- да --> ADVICE["Объяснение выбора от ИИ"]
    AIQ -- "нет / таймаут" --> TPLADV["Шаблонное объяснение"]
    ADVICE --> SAVERES
    TPLADV --> SAVERES["Сохранить результат в БД"]

    SAVERES --> RESULT["Результат: 2 профессии + объяснение"]
    RESULT --> FIN["«Финатлон»: профиль + ссылка на регистрацию"]
    FIN --> CHOICE{"Что дальше?"}
    CHOICE -- "«Маршрут»" --> ROUTE["Маршрут на 3 месяца<br/>сохраняется, повторно не генерируется"]
    CHOICE -- "свой вопрос" --> QA["Ответ ИИ с опорой на ответы теста<br/>дневной лимит"]
    CHOICE -- "«Пройти заново»" --> Q0
    CHOICE -- "ничего" --> IDLE["Пауза"]

    ROUTE --> IDLE
    QA --> IDLE
    IDLE --> REM{"Прошло 7 дней<br/>и напоминания включены?"}
    REM -- да --> PUSH["Напоминание с новым советом<br/>максимум 4 раза"] --> CHOICE
    REM -- нет --> END(["Конец"])
```

## Путь одного сообщения

```mermaid
sequenceDiagram
    participant U as Ученик
    participant M as MAX Bot API
    participant A as MaxAdapter
    participant E as Engine
    participant S as Storage (SQLite)
    participant AI as AI-провайдеры

    U->>M: нажал кнопку ответа
    M-->>A: GET /updates → message_callback
    A->>A: событие уже обрабатывали? → пропустить
    A->>M: POST /answers (отклик на нажатие)
    A->>E: Incoming(kind="action", value="ans:4:0")
    E->>E: блокировка по uid
    E->>S: сохранить ответ
    E-->>A: Reply("анализирую")
    A->>M: снять кнопки у прошлого вопроса (PUT /messages)
    A->>M: POST /messages
    E->>AI: сгенерировать объяснение
    alt провайдер ответил
        AI-->>E: текст
    else таймаут или ошибка
        AI-->>E: пусто → шаблон
    end
    E->>S: сохранить результат
    E-->>A: Reply(результат + кнопки)
    A->>M: POST /messages
    M-->>U: результат
```

Три свойства цепочки заданы намеренно:

1. Ответ и результат попадают в базу до обращения к сети — обрыв связи на выдаче результата не откатывает ученика назад.
2. `POST /messages` не повторяется при сетевой ошибке: ответ мог потеряться уже после доставки, и повтор дал бы дубль. Повторяются чтение обновлений и другие идемпотентные запросы; при 429 повтор безопасен, потому что сообщение точно не доставлено.
3. Блокировка по uid не даёт двум быстрым нажатиям выполниться одновременно.

## Модель данных

```mermaid
erDiagram
    USERS {
        text uid PK "платформа:идентификатор"
        text chat_id
        text name
        text state "new | q:N | done"
        text answers "JSON: 5 ответов"
        text result "JSON: профессии, профиль, совет, маршрут"
        real created_at
        real updated_at
        real completed_at
        real reminded_at
        int reminder_count
        int reminders_off
    }
    EVENTS {
        int id PK
        text uid FK
        text channel "max | tg | web | cli"
        text name "start, begin, answer, completed, ..."
        text data "JSON без персональных данных"
        real ts
    }
    KV {
        text key PK "указатели long polling, секрет подписи"
        text value
    }
    USERS ||--o{ EVENTS : "порождает"
```

`uid` = `<платформа>:<идентификатор>`, поэтому один человек в MAX и в Telegram — разные профили, а веб-версия открывается по подписанной ссылке с тем же `uid`, что и мессенджер.

Аналитика целиком считается из `events`. Отдельных таблиц под метрики нет: новый разрез добавляется без изменения схемы.

## Отказоустойчивость ИИ

```mermaid
flowchart LR
    REQ["Запрос на генерацию"] --> BUDGET{"Бюджет времени<br/>не исчерпан?"}
    BUDGET -- нет --> FALLBACK["Шаблонный ответ"]
    BUDGET -- да --> NEXT["Следующий провайдер<br/>YandexGPT → GigaChat → OpenAI"]
    NEXT --> BREAKER{"Предохранитель<br/>открыт?"}
    BREAKER -- да --> NEXT
    BREAKER -- нет --> CALL["Вызов с таймаутом и повтором"]
    CALL -- успех --> OK["Текст пользователю"]
    CALL -- ошибка --> MARK["Отметить сбой,<br/>при серии отключить на время"] --> NEXT
    NEXT -- "провайдеры кончились" --> FALLBACK
```

Худший исход — шаблонный текст того же смысла, помеченный в аналитике как `provider: template`.

## Точки расширения

| Задача | Что сделать | Где |
|---|---|---|
| Добавить мессенджер (VK, Сферум, школьный портал) | реализовать `run()` и `send_to_user()` | новый файл в `adapters/` |
| Добавить команду или кнопку | `engine.register_command()` / `register_action()` | без правки движка |
| Изменить вопросы, профессии, тексты | отредактировать структуры | `core/content.py` |
| Изменить правила подбора | таблица сочетаний и веса | `core/matching.py` |
| Добавить ИИ-провайдера | класс с `complete()` + запись в `PROVIDER_FACTORIES` | `core/ai.py` |
| Перейти на PostgreSQL | реализовать методы `Storage` | `core/storage.py` |
| Добавить метрику | новый разрез по `events` | `core/analytics.py` |

## Запуск

`main.py` поднимает выбранные адаптеры, веб-сервер и планировщик как независимые задачи под супервизором: упавший компонент перезапускается с задержкой, остальные продолжают работать. Без `MAX_TOKEN` адаптер MAX просто не поднимается, в лог пишется предупреждение.
