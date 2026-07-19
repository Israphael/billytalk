# Harness-слой: как это собирать

**Для сборочной сессии, у которой нет контекста проектирования.**
Спецификация (`02-спецификация-MVP-0.md`) отвечает на вопрос «что». Этот файл —
«как, в каком порядке и когда считать готовым».

## Порядок чтения

1. `../adr/` — почему стек такой, читать первым, экономит споры
2. `02-спецификация-MVP-0.md` — что строим
3. Этот файл — как
4. `../research/07-результаты-спайков.md` — измеренные факты и ловушки

**При расхождении документов приоритет: спецификация → ответы заказчика → разведка.**

---

## 1. Раскладка репозитория

```
billytalk/
  core/                     # процесс ядра, без графики
    __main__.py             # точка входа
    hooks/
      lowlevel.py           # WH_MOUSE_LL / WH_KEYBOARD_LL, поток + GetMessage
      watchdog.py           # живость хука
      keycodes.py           # единое пространство кодов, мышь +0x1000
    machine/
      states.py             # чистая функция (state, event, now) -> (state, [effects])
      events.py             # типы событий
      effects.py            # типы эффектов
      driver.py             # исполнитель эффектов
    audio/
      capture.py            # sounddevice, ранжированный список
      devices.py            # перечисление, WM_DEVICECHANGE, перезагрузка PortAudio
      trim.py               # обрезка тишины
      encode.py             # FLAC через soundfile
      cues.py               # сигналы
    stt/
      base.py               # TranscriptionProvider, AudioClip, TranscriptionResult
      groq.py               # единственная реализация в MVP-0
      errors.py             # таксономия ошибок
    text/
      dictionary.py         # normalize / replace
      polish.py             # опциональное причёсывание
    insert/
      inserter.py           # оркестрация: история -> буфер -> фокус -> вставка -> проверка
      clipboard.py          # сессии, форматы исключения, восстановление
      focus.py              # AttachThreadInput, UIA-откат
      verify.py             # проверка результата
      apprules.py           # правила по имени процесса
    store/
      db.py                 # SQLite, миграции
      history.py            # запросы
      config.py             # чтение/запись конфигурации
      secrets.py            # диспетчер учётных данных
    ipc/
      server.py             # именованный канал, JSON-RPC
      protocol.py           # схемы сообщений, версия
    logging_setup.py        # фильтр редактирования
  ui/                       # процесс интерфейса, wxPython
    __main__.py
    tray.py                 # ctypes, НЕ wx.adv.TaskBarIcon
    overlay.py              # WS_EX_NOACTIVATE
    windows/
      settings.py
      history.py
      hotkey_capture.py     # по образцу NVDA inputGestures.py
      wizard.py             # мастер первого запуска
    ipc/client.py
    i18n/                   # ru, en
tests/
  test_machine.py           # таблица состояний, свойства
  test_dictionary.py
  test_clipboard.py
  test_keycodes.py
  fakes/                    # FakeProvider, FakeClock, FakeInput
packaging/
  billytalk.spec            # PyInstaller, --onedir
  installer.nsi
spikes/  probes/  docs/
```

**Правило границы:** всё, что трогает Windows API ввода, буфер обмена или звук, живёт
в `core/`. `ui/` не импортирует `ctypes` ни для чего, кроме стилей окна.

---

## 2. Процессы и запуск

**Два процесса.** В `HKCU\Run` прописано **ядро**; оно поднимает интерфейс по требованию.

| Событие | Поведение |
|---|---|
| Старт системы | ядро стартует, интерфейс — нет, окно не создаётся |
| Открыть настройки | ядро запускает `ui/`, передаёт имя канала аргументом |
| Падение интерфейса | ядро продолжает диктовать, перезапускает интерфейс не чаще раза в 30 с |
| Падение ядра | интерфейс показывает «остановлен», предлагает перезапуск |
| Выход из трея | интерфейс шлёт `shutdown`, ядро завершает оба |

**Ядро владеет скрытым окном верхнего уровня** — оно нужно для значка трея,
`TaskbarCreated` и `WM_DEVICECHANGE`. Значок рисует ядро, меню наполняет интерфейс
через IPC. Так значок жив при отсутствующем интерфейсе.

---

## 3. Протокол IPC

**Транспорт:** именованный канал `\\.\pipe\billytalk-{sid}-{session_id}`.
Кадрирование: 4 байта длины little-endian, затем UTF-8 JSON.

⚠️ **Только SID недостаточно.** Пространство имён каналов **общемашинное, не
посессионное**: один пользователь в двух сессиях (консоль + RDP) получил бы одно имя,
и интерфейс из второй сессии подключился бы к ядру первой — которое попыталось бы
вставить текст в окно чужой сессии. `session_id` берётся из `ProcessIdToSessionId`.

⚠️ **`lpSecurityAttributes` обязателен и не может быть `NULL`.** По умолчанию Windows
даёт доступ на чтение группе «Все» и анонимной учётной записи — а по каналу идут
расшифровки. DACL: только текущий пользователь и `LocalSystem`.

⚠️ **`FILE_FLAG_FIRST_PIPE_INSTANCE`** на первом экземпляре: занятое имя означает уже
работающее ядро или попытку подмены. Без флага чужой процесс, создавший канал первым,
становится «ядром», и интерфейс подключится к нему.

Плюс `PIPE_REJECT_REMOTE_CLIENTS`. Интерфейс после подключения сверяет
`GetNamedPipeServerProcessId` с путём образа: рукопожатие `hello` подлинности
не устанавливает.

**Рукопожатие обязательно первым сообщением:**

```json
{"type":"hello","protocol":1,"role":"ui","app_version":"0.1.0"}
{"type":"hello_ack","protocol":1,"core_version":"0.1.0"}
```

Несовпадение `protocol` → ядро отвечает `{"type":"error","code":"protocol_mismatch"}`
и закрывает канал; интерфейс показывает «нужно перезапустить приложение». Это
реальный случай при обновлении на живом процессе.

### Ядро → интерфейс

| Сообщение | Полезная нагрузка |
|---|---|
| `state_changed` | `{state, queue_len, detail}` |
| `transcription_ready` | `{id, text, delivery_status, target_app}` |
| `error` | `{code, message_key, recoverable, action}` |
| `usage_updated` | `{words_this_week}` |
| `device_list_changed` | `{inputs:[], outputs:[]}` |
| `hotkey_captured` | `{codes:[], display}` |

### Интерфейс → ядро

| Сообщение | Полезная нагрузка |
|---|---|
| `get_config` / `set_config` | `{}` / `{patch}` |
| `history_search` | `{query, limit, offset}` |
| `history_insert` | `{id}` — **вставляет ядро**, у него сохранён целевой HWND |
| `history_export` | `{format, path}` |
| `dictionary_get` / `dictionary_set` | |
| `capture_hotkey_start` / `capture_hotkey_stop` | `{action}` |
| `test_key` | `{provider, key}` |
| `toggle_dictation` | `{enabled}` |
| `shutdown` | `{}` |

**`history_insert` исполняет ядро, не интерфейс.** Окно истории само в фокусе, поэтому
нужен тот же механизм захвата и возврата фокуса, что и при обычной доставке.

---

## 4. Схема базы

`%LOCALAPPDATA%\BillyTalk\history.db`, режим WAL.

```sql
PRAGMA journal_mode=WAL;
PRAGMA user_version=1;

CREATE TABLE history (
  id                INTEGER PRIMARY KEY,
  seq               INTEGER NOT NULL,          -- порядок нажатия, для упорядоченной доставки
  created_at        INTEGER NOT NULL,          -- unix ms, момент нажатия
  -- ⚠️ NULL до расшифровки: строка создаётся в момент StopCapture (спека §3)
  text_raw          TEXT,
  text_final        TEXT,
  language          TEXT,
  provider_id       TEXT,
  duration_ms       INTEGER NOT NULL,          -- известна сразу
  billed_seconds    REAL,
  latency_ms        INTEGER,
  target_app        TEXT,                      -- имя процесса, НЕ заголовок окна
  target_window_cls TEXT,
  delivery_status   TEXT    NOT NULL,
  error_code        TEXT,                      -- из таксономии §7
  retry_count       INTEGER NOT NULL DEFAULT 0,-- переживает перезапуск процесса
  polished          INTEGER NOT NULL DEFAULT 0,
  audio_path        TEXT,                      -- NULL после уборки
  audio_release_at  INTEGER,                   -- отсчёт часа; NULL = удерживать
  CHECK (delivery_status IN (
    'pending_transcribe','pending_retry','inserted','left_on_clipboard',
    'focus_lost','verify_impossible','blocked_secure','transcribe_failed',
    'cancelled','too_short','empty'))
);

CREATE INDEX idx_history_created ON history(created_at DESC);
-- ⚠️ Колонка называется audio_release_at. В ранней редакции здесь стояло
-- audio_delivered_at, которой в таблице нет: SQLite отвечает "no such column",
-- и DDL не исполняется ЦЕЛИКОМ. Поймано при сборке (OPEN-QUESTIONS §7).
CREATE INDEX idx_history_audio   ON history(audio_release_at)
       WHERE audio_path IS NOT NULL;

CREATE VIRTUAL TABLE history_fts USING fts5(
  text_final, content='history', content_rowid='id', tokenize='unicode61'
);
-- ⚠️ ВНЕШНЕЕ СОДЕРЖИМОЕ: обычные UPDATE/DELETE по history_fts НЕ РАБОТАЮТ.
-- Проверено на живом SQLite 3.50.4: они молча не срабатывают, ошибки нет,
-- integrity-check проходит, а поиск возвращает удалённые строки и старый текст.
-- Обязательна специальная форма 'delete' со СТАРЫМИ значениями.
CREATE TRIGGER history_ai AFTER INSERT ON history BEGIN
  INSERT INTO history_fts(rowid, text_final) VALUES (new.id, new.text_final);
END;
CREATE TRIGGER history_ad AFTER DELETE ON history BEGIN
  INSERT INTO history_fts(history_fts, rowid, text_final)
    VALUES('delete', old.id, old.text_final);
END;
CREATE TRIGGER history_au AFTER UPDATE OF text_final ON history BEGIN
  INSERT INTO history_fts(history_fts, rowid, text_final)
    VALUES('delete', old.id, old.text_final);
  INSERT INTO history_fts(rowid, text_final) VALUES (new.id, new.text_final);
END;

CREATE TABLE dictionary (
  id    INTEGER PRIMARY KEY,
  type  TEXT NOT NULL CHECK(type IN ('normalize','replace')),
  pat   TEXT NOT NULL,
  repl  TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1
);
```

**FTS5, не `LIKE`** — история бессрочна, поиск обязан оставаться быстрым.

`delivery_status` ∈ `inserted` · `left_on_clipboard` · `focus_lost` ·
`verify_impossible` · `blocked_secure` · `transcribe_failed` · `pending_retry` ·
`cancelled` · `too_short`

**Миграции:** `PRAGMA user_version`, последовательные функции `migrate_1_to_2` и т. д.
Схема заводится сразу целиком, чтобы MVP-0 не требовал миграции при добавлении режима 2.

**Уборка** (спека §3, срок из конфигурации, не литерал):

```sql
DELETE ... WHERE audio_release_at IS NOT NULL
             AND audio_release_at < :now - :retention_minutes * 60000
```

`audio_release_at` ставится при **любом терминальном исходе, где текст дошёл до
пользователя** — `inserted`, `left_on_clipboard`, `focus_lost`, `verify_impossible`.
`NULL` означает «удерживать»: расшифровки ещё не было.

⚠️ **Уборка не запускается вовсе, пока нет сети**, и возобновляется через 10 минут
после первой успешной расшифровки. Верхний предел удержания — 500 записей или 2 ГБ,
дальше вытесняются самые старые с уведомлением.

**Сборка мусора при старте:** файлы в `audio\` без ссылающейся строки удаляются;
строки в `pending_transcribe`/`pending_retry` ставятся в очередь повтора.

`VACUUM` еженедельно при простое, **только если свободного места ≥ 2× размера базы**.
`PRAGMA busy_timeout=5000`, `PRAGMA secure_delete=ON`.

---

## 5. Конфигурация и секреты

`%APPDATA%\BillyTalk\config.json`, UTF-8, **запись атомарная**: во временный файл,
затем `os.replace`.

- Файла нет → создать из умолчаний
- `schema_version` больше нашей → **не запускаться**, сообщить «конфиг от более новой
  версии»
- Разбор упал → переименовать в `config.corrupt-{ts}.json`, стартовать с умолчаний,
  уведомить

**Секреты в конфиг не пишутся никогда.** Диспетчер учётных данных,
`CRED_TYPE_GENERIC`, цели `BillyTalk/groq-api-key`, `BillyTalk/polish-api-key`.

---

## 6. Логи и инвариант редактирования

`%LOCALAPPDATA%\BillyTalk\logs\core.log`, ротация 5 × 2 МБ.

Три механизма (Python не компилируется, поэтому «ошибка компиляции» недостижима):

```python
class Sensitive:
    """Базовый тип для аудио и расшифровок."""
    def __repr__(self):  raise TypeError("Sensitive value must never be rendered")
    __str__ = __repr__
    def __format__(self, spec): raise TypeError("...")

class RedactionFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.msg, Sensitive):  return False
        if any(isinstance(a, Sensitive) for a in (record.args or ())):  return False
        return True
```

Плюс правило линтера в CI против `logging.*` с этими типами.

**Никогда:** аудио, текст расшифровки, любая его подстрока, хеш или длина, заголовок
и содержимое целевого окна, содержимое буфера, нажатия клавиш сверх хоткея.
**Можно:** имя процесса, коды ошибок, задержки, длительность аудио, счётчики.

**Разграничение:** база — не лог. `target_app` в базе допустим, в логе нет.

---

## 7. Таксономия ошибок

Код стабилен, текст локализуется по ключу.

| Код | Восстановима | Действие пользователя |
|---|---|---|
| `mic_denied` | да | открыть параметры приватности |
| `mic_busy` | да | выбрать другое устройство |
| `no_api_key` | да | открыть настройки |
| `key_invalid` | нет | заменить ключ |
| `rate_limited` | да | ждать |
| `network_down` | да | ничего, повторим |
| `provider_error` | да | ничего, повторим |
| `paste_failed` | да | **Ctrl+Alt+Z** |
| `focus_lost` | да | **Ctrl+Alt+Z** |
| `secure_field` | да | вставить вручную |
| `hook_dead` | да | автопереустановка |
| `clip_too_long` | нет | говорить короче |

Каждое сообщение об отказе **несёт готовое действие**, а не констатацию.

---

## 8. Тесты

### Машина состояний — главное

Реализуется чистой функцией, поэтому вся таблица проверяется без железа.

```python
def test_release_while_initialized_defers_not_cancels():
    s = initial_state()
    s, fx = step(s, PressPTT(), now=0.0)          # -> Initialized
    s, fx = step(s, ReleasePTT(), now=0.010)      # рано!
    assert s.phase is Phase.Initialized           # НЕ отменено
    s, fx = step(s, CaptureStarted(), now=0.050)
    assert s.phase is Phase.Finalizing            # отложенное применилось
    assert StopCapture in types(fx)
```

⚠️ **`State` — запись, а не скаляр** (спека §4), поэтому сравнение идёт по `s.phase`,
а не по `s`. В ранней редакции пример был написан как `assert s is Initialized`
и противоречил спецификации.

⚠️ **Порог в 250 мс к отложенному отпусканию не применяется.** Видимое машине время
удержания здесь равно нулю, и порог пометил бы каждую такую диктовку как `too_short` —
то есть сломал бы ровно тот сценарий, ради которого правило отложенного отпускания
и существует. Пустоту клипа определяет звуковой слой и сообщает событием `ClipEmpty`.

**Обязательные имена тестов** — по одному на ячейку таблицы спеки §4:

*Отложенное отпускание и режимы*
`release_while_initialized_defers_not_cancels` · `toggle_ignores_release_in_all_states` ·
`double_press_ignored` · `release_without_press_ignored`

*Успешный путь — его в редакции 2 не было вовсе*
`capture_started_applies_deferred_release` · `transcribe_ok_enters_delivering` ·
`insert_ok_returns_to_idle_or_next_queued`

*Отмена*
`single_esc_passes_through` · `double_esc_in_{initialized,recording,finalizing}_cancels` ·
`double_esc_in_delivering_ignored`

*Отказы и живучесть*
`max_hold_delivers_to_history_not_paste` · `hook_death_finalizes_recording` ·
`suspend_finalizes_recording` · `mic_error_enters_failed_then_idle` ·
`failed_clears_suppression` · `history_write_failure_still_writes_clipboard`

*Очередь*
`second_dictation_queues_and_delivers_in_order` ·
`failed_transcription_releases_its_ordering_slot` ·
`fourth_press_rejected_at_press_time_with_cue`

*Долговечность*
`persist_audio_precedes_transcribe` · `write_history_precedes_insert` ·
`write_clipboard_precedes_insert` · `short_press_recorded_but_silent` ·
`empty_clip_recorded_but_silent`

*Хранилище*
`fts_delete_removes_row_from_search` · `fts_update_removes_old_tokens` ·
`cleanup_skips_rows_with_null_release_at` · `cleanup_paused_while_offline`

**Свойства над случайными последовательностями событий:**

```python
@given(st.lists(st.sampled_from(ALL_EVENTS), max_size=200))
def test_invariants(events):
    ...
    assert count(StartCapture) == count(StopCapture)   # 234 vs 244 у конкурента
    assert never_two_concurrent_captures(trace)
    assert every_insert_preceded_by_successful_transcribe(trace)
    assert history_row_written_before_insert_attempt(trace)
```

Эти тридцать строк проверяют ровно тот дефект, ради которого существует продукт.

### Остальное

- `test_dictionary` — границы слов в кириллице, длинные правила раньше коротких,
  правило не срабатывает внутри слова
- `test_keycodes` — круговое преобразование, извлечение `HIWORD`
- `test_clipboard` — порядок операций на подставном буфере, отмена восстановления
  при смене sequence number
- **Подставные объекты обязательны:** `FakeProvider` (управляемая задержка и отказы),
  `FakeClock` (иначе тесты станут спать и мигать), `FakeInput`

### Что нельзя автоматизировать

Проверять руками: удержание Mouse 4 в реальном окне · отвал гарнитуры ·
убийство проводника · плашка не крадёт фокус · автозапуск в Параметрах.

---

## 9. Порядок сборки

**Вертикальный срез, не слои.** В конце первого цикла продукт работает по главной
функции.

### Цикл 1 — «зажал, сказал, текст появился»

Без интерфейса вообще, запуск `python -m billytalk.core`.

1. `logging_setup`, `store/config`, `store/secrets` — минимально
2. `hooks/` — **только Mouse 4, режим удержания**, подавление, автоповтор
3. **`machine/` целиком** плюс симулятор и все инварианты
4. `audio/` — устройство по умолчанию, без ранжирования и горячей замены, FLAC
5. `stt/groq` за интерфейсом с тремя объявленными реализациями
6. `text/dictionary`
7. `insert/` — буфер, VK `0x56`, защита от модификатора, возврат фокуса по HWND,
   правило терминалов. **Без проверки вставки**
8. `store/db` — запись с настоящей DDL, чтобы потом не мигрировать

Готово, когда: удержание Mouse 4 в блокноте даёт текст, а тесты машины состояний
зелёные.

> Пока нет трея, выхода нет — предусмотреть завершение по консоли.

### Цикл 2 — интерфейс

Трей на ctypes · плашка · окна настроек и истории · захват хоткея · мастер ·
ранжированный микрофон · остальные привязки и режимы · проверка вставки · UIA-откат.

### Цикл 3 — поставка

Установщик · автозапуск со всей семантикой `StartupApproved` · PyInstaller `--onedir` ·
`ru`/`en` · прогон приёмки · затем неделя реального использования.

**Итоговый критерий достижим не раньше конца третьего цикла плюс семь дней.**

---

## 10. Спайки, перенесённые в сборку

Две проверки требуют живых окон заказчика, их закрывать **первым делом в цикле 1**:

- **S1 — возврат фокуса.** Замерить долю успеха `AttachThreadInput` +
  `SetForegroundWindow` против Chrome, PuTTY, Windows Terminal, VS Code, Claude.
  Провал не блокирует: спецификация уже считает невозврат штатным исходом.
- **S2 — проверка вставки.** Есть ли надёжный сигнал. **Ограничить тремя часами.**
  Провал не блокирует: политика «молчать, но всегда оставлять путь отхода» уже принята.

---

## 11. Сборка

```bash
uv venv --python 3.14 .venv
uv pip install -r requirements.txt
python -m billytalk.core                 # ядро
python -m billytalk.ui                   # интерфейс
pytest -q                                # тесты
pyinstaller packaging/billytalk.spec     # ТОЛЬКО --onedir
makensis packaging/installer.nsi
```

⚠️ **`--onefile` запрещён.** Он распаковывает пакет во временную папку при каждом
запуске: превышает порог влияния на запуск Windows и подгружает модули с удержанием
GIL во время работы хука.

---

## 12. Готовность модуля

Модуль готов, когда: покрыт тестами с подставными зависимостями; все пути отказа
возвращают код из таксономии; ничего чувствительного не попадает в логи; публичные
функции имеют аннотации типов; ловушки из `research/07` учтены явно.

---

## 13. Список ловушек — держать под рукой

| Ловушка | Правило |
|---|---|
| `PeekMessage` не получает колбэки хука | только блокирующий `GetMessage` |
| Нулевое смещение мыши отбрасывается | сторож шлёт ±1 пиксель |
| `SendInput` рапортует несостоявшуюся вставку | проверять эффект, не код возврата |
| `SetForegroundWindow` игнорируется из фона | `AttachThreadInput`, **не с потока хука** |
| `SetWindowLongW` обрезает 64 бита | `SetWindowLongPtrW` с явными `LONG_PTR` |
| `frame.Show()` активирует окно | `ShowWindow(SW_SHOWNOACTIVATE)` |
| `wx.adv.TaskBarIcon` не даёт версию 4 | трей на ctypes |
| `NIF_GUID` привязан к пути файла | `hWnd` + `uID` |
| Подсказка трея требует `NIF_SHOWTIP` | под версией 4 обязательно |
| `TaskbarCreated` не приходит в message-only окно | настоящее окно верхнего уровня |
| `SetClipboardData(fmt, NULL)` = отложенная отрисовка | реальный `GlobalAlloc` |
| Перечисление всех форматов буфера блокирует | явный список по именам |
| `StartupApproved` бывает `06`/`07` | проверять `byte0 & 1`, не `== 3` |
| Cloudflare режет `urllib` | всегда слать `User-Agent` |
| Промпт списком портит пунктуацию | промпт нормальным предложением |
| `\b` в Python по умолчанию ASCII | `re.UNICODE` для кириллицы |
| PortAudio замораживает список устройств | перезагрузка библиотеки при остановленном потоке |
| Три замера — не замер | сравнения чередованием, минимум 8 пар |
