"""Русские строки. The source of truth for the key set: ``en.py`` is checked
against this table by the test suite, so a key added here and forgotten there
fails a test instead of showing up in the product as a Russian label inside an
English window."""

from __future__ import annotations

from typing import Final

STRINGS: Final[dict[str, str]] = {
    # ------------------------------------------------------------------ #
    # общее
    # ------------------------------------------------------------------ #
    "app.name": "BillyTalk",
    "common.back": "Назад",
    "common.next": "Далее",
    "common.skip": "Пропустить",
    "common.done": "Готово",
    "common.close": "Закрыть",
    "common.cancel": "Отмена",
    "common.retry": "Повторить",
    "common.change": "Изменить",
    "common.dash": "—",
    "common.system_default": "Системный по умолчанию",

    # ------------------------------------------------------------------ #
    # трей (ядро)
    # ------------------------------------------------------------------ #
    "tray.idle": "BillyTalk — готов",
    "tray.recording": "BillyTalk — запись",
    "tray.transcribing": "BillyTalk — расшифровка",
    "tray.queue": "BillyTalk — записи в очереди",
    "tray.offline": "BillyTalk — нет связи, {waiting} записей ждут",
    "tray.stopped": "BillyTalk — диктовка выключена",
    "tray.error": "BillyTalk — ошибка",

    # меню трея (интерфейс)
    "menu.settings": "Открыть настройки",
    "menu.history": "История",
    "menu.toggle": "Диктовка включена",
    "menu.exit": "Выход",

    # ------------------------------------------------------------------ #
    # уведомления: что случилось + готовое действие (harness §7)
    # ------------------------------------------------------------------ #
    "err.mic_denied.title": "Нет доступа к микрофону",
    "err.mic_denied.action": "Откройте Параметры → Конфиденциальность → Микрофон",
    "err.mic_busy.title": "Микрофон недоступен",
    "err.mic_busy.action": "Выберите другое устройство записи в настройках",
    "err.no_api_key.title": "Нужен ключ Groq",
    "err.no_api_key.action": "Откройте настройки и сохраните ключ — запись уже сохранена",
    "err.key_invalid.title": "Ключ Groq отклонён",
    "err.key_invalid.action": "Замените ключ в настройках",
    "err.rate_limited.title": "Лимит запросов",
    "err.rate_limited.action": "Подождите — записи ждут и уйдут сами",
    "err.network_down.title": "Нет связи",
    "err.network_down.action": "Записи сохранены, расшифруем, как только связь появится",
    "err.provider_error.title": "Сервис расшифровки недоступен",
    "err.provider_error.action": "Повторим сами; записи не потеряны",
    "err.paste_failed.title": "Вставка не удалась",
    "err.paste_failed.action": "Текст в буфере — Ctrl+V или Ctrl+Alt+Z",
    "err.focus_lost.title": "Окно ушло из фокуса",
    "err.focus_lost.action": "Текст в буфере — вернитесь в поле и нажмите Ctrl+Alt+Z",
    "err.secure_field.title": "Поле пароля",
    "err.secure_field.action": "Мы туда не пишем — вставьте вручную из буфера",
    "err.hook_dead.title": "Перехват ввода переустановлен",
    "err.hook_dead.action": "Кнопка диктовки снова работает",
    "err.clip_too_long.title": "Слишком длинная запись",
    "err.clip_too_long.action": "Предел — 20 минут; говорите короче",
    "err.audio_unreadable.title": "Аудио не прочиталось",
    "err.audio_unreadable.action": "Запись повреждена или удалена — повторите диктовку",

    # ------------------------------------------------------------------ #
    # окно настроек
    # ------------------------------------------------------------------ #
    "settings.title": "BillyTalk — настройки",
    "settings.section.general": "Общие",
    "settings.section.bindings": "Привязки",
    "settings.section.mic": "Микрофон",
    "settings.section.stt": "Расшифровка",
    "settings.section.dictionary": "Словарь",
    "settings.section.about": "О программе",

    "settings.autostart": "Запускать при входе в Windows",
    "settings.autostart.hint": "Автозапуск ядра; значок появляется в трее",
    "settings.autostart.disabled_by_windows":
        "Отключено в Параметрах Windows → Приложения → Автозагрузка",
    "settings.autostart.unavailable": "Недоступно: программа запущена не из установленной папки",
    "settings.autostart.failed": "Windows не дал изменить автозапуск",
    "settings.ui_language": "Язык программы",
    "settings.ui_language.hint": "Язык окон и уведомлений; на язык диктовки не влияет",
    "settings.ui_language.auto": "Как в Windows",
    "settings.plashka": "Плашка во время записи",
    "settings.plashka.hint":
        "Отключение станет доступно после первого использования Ctrl+Alt+Z",

    "settings.binding.ptt": "Диктовка — удержание",
    "settings.binding.fallback": "Диктовка — запасная",
    "settings.binding.toggle": "Диктовка — тумблер",
    "settings.binding.paste": "Вставить последнее",
    "settings.binding.copy": "Скопировать последнее",
    "settings.binding.window": "Главное окно",
    "binding.code": "Код {code}",
    "settings.binding.fixed": "Фиксированное сочетание в MVP-0",
    "settings.binding.note":
        "Отмена — двойной Esc во время записи. Одиночный Esc всегда проходит в приложение.",

    "settings.mic.device": "Устройство записи",
    "settings.mic.hint": "Список обновляется при подключении и отключении устройств",
    "settings.mic.check": "Проверить",
    "settings.mic.checking": "Проверяю…",

    "settings.language": "Язык диктовки",
    "settings.language.hint": "Определяется явно, не автоматически",
    "settings.key": "Ключ API",
    "settings.key.hint": "Диспетчер учётных данных Windows, не в файлах",
    "settings.key.saved": "Сохранён",
    "settings.key.missing": "Не сохранён",
    "settings.key.replace": "Заменить…",
    "settings.polish": "Причёсывание текста",
    "settings.polish.hint": "Отдельный ключ; хранятся оба варианта текста",
    "settings.model": "Модель",
    "settings.model.hint": "Облако Groq, ключ пользователя",

    "settings.rule.type": "Тип",
    "settings.rule.heard": "Слышится",
    "settings.rule.written": "Пишется",
    "settings.rule.enabled": "Вкл",
    "settings.rule.add": "Добавить правило",
    "settings.rule.edit": "Изменить",
    "settings.rule.delete": "Удалить",
    "settings.rule.toggle": "Вкл/выкл",
    "settings.rule.dialog": "Правило словаря",
    "settings.rule.heard_field": "Слышится (варианты через |)",
    "settings.rule.enabled_field": "Включено",
    "settings.rule.incomplete": "Заполните «Слышится» и «Пишется».",
    "settings.rule.type.normalize": "написание",
    "settings.rule.type.replace": "замена",

    "settings.about.version": "BillyTalk {version}",
    "settings.about.repo": "github.com/Israphael/billytalk",
    "settings.about.logs": "Журнал работы",
    "settings.about.logs.hint":
        "Текст диктовок и нажатия клавиш в журнал не попадают никогда",
    "settings.about.logs.open": "Открыть папку логов",
    "settings.about.data": "Данные",
    "settings.about.data.hint": "История и аудио на этом компьютере",
    "settings.about.data.clear": "Очистить историю…",
    "settings.about.data.soon": "Появится вместе с окном подтверждения",
    "settings.about.wizard": "Мастер первого запуска",
    "settings.about.wizard.hint": "Микрофон, кнопка, ключ и проверка — по шагам",
    "settings.about.wizard.run": "Пройти заново",

    "settings.rejected": "Ядро отклонило изменение настроек",
    "settings.rule.rejected": "Правило отклонено — список восстановлен",

    "language.ru": "Русский",
    "language.en": "Английский",

    # ------------------------------------------------------------------ #
    # окно истории
    # ------------------------------------------------------------------ #
    "history.title": "BillyTalk — история",
    "history.search.hint": "Поиск по истории…",
    "history.find": "Найти",
    "history.export": "Экспорт…",
    "history.insert": "Вставить",
    "history.copy": "Копировать",
    "history.column.time": "Время",
    "history.column.text": "Текст",
    "history.column.app": "Приложение",
    "history.column.status": "Статус",
    "history.filter.all": "Все статусы",
    "history.filter.delivered": "Вставлено",
    "history.filter.clipboard": "В буфере",
    "history.filter.waiting": "Ждёт связи",
    "history.filter.other": "Прочее",
    "history.footer.page": "{shown} на экране · {total} записей · текст хранится бессрочно",
    "history.footer.found": "Найдено: {shown} (первые {page})",
    "history.footer.select": "Выберите запись",
    "history.footer.search_failed": "Поиск не ответил — попробуйте ещё раз",
    "history.footer.insert_failed": "Вставка не удалась — текст можно скопировать",
    "history.footer.copied": "Скопировано — вставьте Ctrl+V.",
    "history.footer.clipboard_busy": "Буфер занят другим приложением — попробуйте ещё раз",
    "history.footer.export_failed": "Экспорт не удался — проверьте путь и попробуйте ещё раз",
    "history.footer.exported": "Экспортировано записей: {rows}",
    "history.export.dialog": "Экспорт истории",
    "history.export.file": "billytalk-история",
    "history.export.wildcard": "Текст (*.txt)|*.txt|Таблица CSV (*.csv)|*.csv|JSON (*.json)|*.json",

    "status.inserted": "Вставлено",
    "status.left_on_clipboard": "В буфере · Ctrl+V",
    "status.withheld": "Без вставки",
    "status.focus_lost": "В буфере · фокус ушёл",
    "status.verify_impossible": "Без подтверждения",
    "status.blocked_secure": "Поле пароля",
    "status.pending_transcribe": "Расшифровка…",
    "status.pending_retry": "Ждёт связи",
    "status.transcribe_failed": "Ошибка расшифровки",
    "status.cancelled": "Отменено",
    "status.too_short": "Слишком коротко",
    "status.empty": "Пусто",

    "outcome.inserted": "Вставлено.",
    "outcome.verify_impossible":
        "Отправлено; подтверждения нет — текст и в буфере (Ctrl+V).",
    "outcome.left_on_clipboard": "Текст в буфере — вставьте Ctrl+V.",
    "outcome.focus_lost": "Окно ушло из фокуса — текст в буфере, вставьте Ctrl+V.",
    "outcome.blocked_secure": "Поле пароля: вставьте из буфера вручную.",

    # ------------------------------------------------------------------ #
    # захват кнопки
    # ------------------------------------------------------------------ #
    "capture.title": "BillyTalk — захват кнопки",
    "capture.prompt": "Нажмите кнопку мыши или клавишу для диктовки",
    "capture.hint": "Esc — отмена. Окно закроется само через 30 секунд.\n"
                    "Боковые кнопки мыши подходят лучше всего.",

    # ------------------------------------------------------------------ #
    # мастер первого запуска (спека §12)
    # ------------------------------------------------------------------ #
    "wizard.title": "BillyTalk — первый запуск",
    "wizard.step_of": "Шаг {step} из {total}",
    "wizard.finish": "Готово",
    "wizard.later": "Закончить позже",
    "wizard.reopen_hint": "Мастер можно пройти заново: настройки → О программе.",

    "wizard.mic.title": "Микрофон",
    "wizard.mic.body":
        "Проверим, что BillyTalk вас слышит. Проверка занимает секунду и ничего не "
        "записывает.",
    "wizard.mic.check": "Проверить микрофон",
    "wizard.mic.ok": "Микрофон работает: {device}",
    "wizard.mic.denied":
        "Windows не даёт доступ к микрофону. Откройте параметры и разрешите его "
        "приложениям для настольного компьютера.",
    "wizard.mic.busy":
        "Устройство занято другим приложением или недоступно. Закройте программу, "
        "которая держит микрофон, и проверьте снова.",
    "wizard.mic.none": "Windows не видит ни одного микрофона.",
    "wizard.mic.silent":
        "Микрофон открылся, но звука нет: проверьте, не выключен ли он кнопкой на "
        "гарнитуре и не убран ли уровень в Windows.",
    "wizard.mic.level": "Уровень: {level}%",
    "wizard.mic.settings": "Открыть параметры микрофона",

    "wizard.language.title": "Язык",
    "wizard.language.body":
        "Язык диктовки задаётся явно: автоопределение путает похожие слова чаще, "
        "чем помогает.",
    "wizard.language.dictation": "Язык диктовки",
    "wizard.language.ui": "Язык программы",

    "wizard.hotkey.title": "Кнопка диктовки",
    "wizard.hotkey.body":
        "Работает так: удерживаете кнопку — говорите — отпускаете. Текст появляется "
        "в том поле, где стоял курсор.",
    "wizard.hotkey.current": "Сейчас: {key}",
    "wizard.hotkey.change": "Назначить другую",
    "wizard.hotkey.note":
        "Боковая кнопка мыши подходит лучше всего: она под большим пальцем и почти "
        "не нужна ни в одной программе.",

    "wizard.driver.title": "«Назад» на боковой кнопке",
    "wizard.driver.body":
        "У боковой кнопки обычно есть штатное действие «Назад». Пока BillyTalk "
        "работает, он его подавляет — но подавления нет, если программа упала, "
        "если поверх окно с правами администратора, и в короткий зазор при "
        "переустановке перехвата.",
    "wizard.driver.why":
        "Что это значит на практике: браузер может уйти «Назад» и потерять "
        "заполненную форму. Если снять «Назад» с кнопки в программе вашей мыши "
        "(Razer Synapse, Logitech G HUB, Bloody, Mouse Properties), любой такой "
        "сбой станет безвредным.",
    "wizard.driver.how":
        "Как: откройте программу мыши → профиль → назначения кнопок → боковой "
        "кнопке поставьте «Нет действия» или «Кнопка мыши 4».",
    "wizard.driver.done": "Снял «Назад» с кнопки",
    "wizard.driver.optional": "Шаг необязательный — можно оставить как есть.",

    "wizard.stt.title": "Расшифровка",
    "wizard.stt.warning":
        "BillyTalk расшифровывает речь в облаке. Без интернета или на слабой связи "
        "диктовка не будет работать: записи сохранятся и расшифруются, когда связь "
        "появится, но текст в этот момент вы не получите.",
    "wizard.stt.body":
        "Провайдер MVP-0 — Groq (модель whisper-large-v3-turbo), по вашему ключу. "
        "Локальная расшифровка без интернета появится в версии 1.0.",

    "wizard.key.title": "Ключ Groq",
    "wizard.key.body":
        "Ключ бесплатный и без карты. Откройте console.groq.com, войдите, создайте "
        "API key и вставьте его сюда.",
    "wizard.key.open": "Открыть console.groq.com",
    "wizard.key.field": "Ключ",
    "wizard.key.save": "Сохранить и проверить",
    "wizard.key.stored": "Ключ уже сохранён. Можно вставить новый, чтобы заменить.",
    "wizard.key.checking": "Проверяю ключ…",
    "wizard.key.ok": "Ключ принят.",
    "wizard.key.invalid": "Groq отклонил ключ. Проверьте, что скопирован целиком.",
    "wizard.key.network":
        "Нет связи — ключ сохранён, но проверить его сейчас не получилось.",
    "wizard.key.empty": "Вставьте ключ в поле.",
    "wizard.key.failed": "Не удалось сохранить ключ в диспетчер учётных данных.",
    "wizard.key.privacy":
        "Хранится в диспетчере учётных данных Windows. В файлы, историю и журнал "
        "не попадает никогда.",

    "wizard.test.title": "Проверка",
    "wizard.test.body":
        "Откройте любое поле ввода — Блокнот, чат, строку поиска. Удерживайте {key}, "
        "скажите фразу, отпустите.",
    "wizard.test.waiting": "Жду первую диктовку…",
    "wizard.test.got": "Готово. Последняя диктовка: «{text}»",
    "wizard.test.status": "Состояние: {status}",
    "wizard.test.tray":
        "Совет: закрепите значок у часов — нажмите «^» рядом с треем и перетащите "
        "значок BillyTalk наружу. Значок показывает запись, расшифровку и очередь.",
    "wizard.test.autostart": "Запускать BillyTalk при входе в Windows",

    "wizard.done.title": "Готово",
    "wizard.done.body":
        "BillyTalk живёт в трее. Удержание кнопки — диктовка, Ctrl+Alt+Z — вставить "
        "последнее, Ctrl+Alt+X — скопировать.",
    "wizard.done.nokey":
        "Ключ не сохранён: записи будут копиться и расшифруются, как только ключ "
        "появится в настройках.",
}
