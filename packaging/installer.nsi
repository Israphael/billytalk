; BillyTalk installer (spec §12: NSIS, currentUser, %LOCALAPPDATA%\Programs\BillyTalk,
; без версии в пути).
;
; Собрать:  "C:\Program Files (x86)\NSIS\makensis.exe" packaging\installer.nsi
; Выход:    dist\BillyTalk-Setup.exe
;
; Файл в UTF-8 С BOM — makensis 3 читает так только при наличии BOM, иначе
; примет русские строки за системную кодовую страницу и выдаст мусор.
;
; Всё, что делает установщик, повторяет проверенную логику install.ps1 и ничего
; не добавляет от себя: та же папка, тот же автозапуск, то же WER-исключение.
; Отличий три, и все — в пользу пользователя: один файл вместо команды в консоли,
; вопрос про личные данные при удалении, и отмена на середине, которая
; действительно ничего не ломает — файлы распаковываются в отдельную папку и
; подменяют старые одним переименованием в самом конце.

Unicode true
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

!define APP_NAME     "BillyTalk"
!define APP_VERSION  "0.1.0"
!define APP_EXE      "BillyTalk.exe"
!define APP_PUBLISHER "BillyTalk"
!define APP_URL      "https://github.com/Israphael/billytalk"

!define REG_RUN      "Software\Microsoft\Windows\CurrentVersion\Run"
!define REG_APPROVED "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
!define REG_WER      "Software\Microsoft\Windows\Windows Error Reporting\ExcludedApplications"
!define REG_UNINST   "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
!define STAGING      "_incoming"
; Папка, в которую распаковывается новая версия до подмены старой.

Name "${APP_NAME}"
; Внизу каждой страницы: имя продукта и версия вместо рекламы Nullsoft.
BrandingText "${APP_NAME} ${APP_VERSION}"
OutFile "..\dist\${APP_NAME}-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\${APP_NAME}"
InstallDirRegKey HKCU "${REG_UNINST}" "InstallLocation"

; Спека §12: «Права обычные. Повышенный процесс не сможет вставлять в обычные
; приложения» — установщик от администратора поставил бы программу, которая
; молча не работает, поэтому уровень запрошен явно.
RequestExecutionLevel user

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "FileDescription" "${APP_NAME} — установка"
VIAddVersionKey "FileVersion" "${APP_VERSION}"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "LegalCopyright" "MIT"
VIAddVersionKey "CompanyName" "${APP_PUBLISHER}"

!define MUI_ICON "billytalk.ico"
!define MUI_UNICON "billytalk.ico"
!define MUI_ABORTWARNING
!define MUI_CUSTOMFUNCTION_ABORT OnAbort

; Своя боковая картинка вместо дефолтной синей 8-битной с дизерингом — это
; первое, что человек видит от программы (packaging/make_installer_art.py).
; БЕЗ NOSTRETCH намеренно: на экране с масштабом 125% диалог крупнее, чем
; базовые 164x314, и запрет растяжения оставлял белый провал под картинкой —
; проверено скриншотом. Градиент с глифом растяжение на 17% переживает
; незаметно, а дыра в интерфейсе заметна сразу.
!define MUI_WELCOMEFINISHPAGE_BITMAP "welcome.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "welcome.bmp"

!define MUI_WELCOMEPAGE_TITLE "$(WELCOME_TITLE)"
!define MUI_WELCOMEPAGE_TEXT "$(WELCOME_TEXT)"
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE CheckDirectory
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES

!define MUI_FINISHPAGE_TITLE "$(FINISH_TITLE)"
!define MUI_FINISHPAGE_TEXT "$(FINISH_TEXT)"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "$(FINISH_RUN)"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; Русский первым: на русской Windows он и выберется, на любой другой NSIS
; возьмёт английский. Диалога выбора языка нет намеренно — лишний клик перед
; тем, что человек и так хотел сделать.
!insertmacro MUI_LANGUAGE "Russian"
!insertmacro MUI_LANGUAGE "English"

LangString WELCOME_TITLE ${LANG_RUSSIAN} "Установка BillyTalk"
LangString WELCOME_TITLE ${LANG_ENGLISH} "Install BillyTalk"
LangString WELCOME_TEXT ${LANG_RUSSIAN} \
  "BillyTalk — диктовка голосом: удерживаете кнопку, говорите, отпускаете, текст появляется в том поле, где стоял курсор.$\r$\n$\r$\nПрограмма ставится только для вас, без прав администратора, и живёт в трее.$\r$\n$\r$\nПосле установки откроется мастер первого запуска: микрофон, кнопка, ключ и живая проверка."
LangString WELCOME_TEXT ${LANG_ENGLISH} \
  "BillyTalk is voice dictation: hold the button, speak, release, and the text appears wherever the caret was.$\r$\n$\r$\nIt installs for you only, without administrator rights, and lives in the tray.$\r$\n$\r$\nA setup wizard opens afterwards: microphone, button, key and a live test."

LangString FINISH_TITLE ${LANG_RUSSIAN} "BillyTalk установлен"
LangString FINISH_TITLE ${LANG_ENGLISH} "BillyTalk is installed"
; Коротко: у страницы завершения текстовая область фиксированной высоты, сразу
; под ней чекбокс, и длинный текст обрезается на полуслове — это видно на
; скриншоте, а в исходнике не видно никак.
LangString FINISH_TEXT ${LANG_RUSSIAN} \
  "Значок микрофона появится возле часов, и BillyTalk будет запускаться вместе с Windows.$\r$\n$\r$\nПри первом запуске откроется мастер: семь шагов, последний проверяет диктовку вживую."
LangString FINISH_TEXT ${LANG_ENGLISH} \
  "The microphone icon appears next to the clock, and BillyTalk will start with Windows.$\r$\n$\r$\nOn the first run a wizard opens: seven steps, the last one tests dictation live."
LangString FINISH_RUN ${LANG_RUSSIAN} "Запустить BillyTalk"
LangString FINISH_RUN ${LANG_ENGLISH} "Run BillyTalk"

LangString ASK_USERDATA ${LANG_RUSSIAN} \
  "Удалить историю диктовок, аудио и настройки?$\r$\n$\r$\nДА — будет стёрто всё, включая текст ваших диктовок.$\r$\nНЕТ — данные останутся на месте и подхватятся, если поставить BillyTalk снова.$\r$\n$\r$\nКлюч Groq в диспетчере учётных данных не трогается в любом случае."
LangString ASK_USERDATA ${LANG_ENGLISH} \
  "Delete the dictation history, audio and settings?$\r$\n$\r$\nYES — everything goes, including the text of your dictations.$\r$\nNO — the data stays and is picked up again if you reinstall BillyTalk.$\r$\n$\r$\nThe Groq key in the Credential Manager is left alone either way."

LangString RUNNING_STOPPED ${LANG_RUSSIAN} "Закрываю запущенный BillyTalk…"
LangString RUNNING_STOPPED ${LANG_ENGLISH} "Closing the running BillyTalk…"

LangString STILL_RUNNING ${LANG_RUSSIAN} \
  "BillyTalk не удалось закрыть — он всё ещё запущен.$\r$\n$\r$\nЗакройте его через меню значка возле часов («Выход») и запустите установку снова. Продолжать сейчас нельзя: получилась бы смесь старых и новых файлов."
LangString STILL_RUNNING ${LANG_ENGLISH} \
  "BillyTalk could not be closed — it is still running.$\r$\n$\r$\nQuit it from the tray icon menu and run the installer again. Continuing now would leave a mix of old and new files."

LangString FOREIGN_DIR ${LANG_RUSSIAN} \
  "В этой папке уже что-то лежит, и это не BillyTalk:$\r$\n$INSTDIR$\r$\n$\r$\nУстановка перезапишет её содержимое. Выбрать другую папку?"
LangString FOREIGN_DIR ${LANG_ENGLISH} \
  "This folder already has something in it, and it is not BillyTalk:$\r$\n$INSTDIR$\r$\n$\r$\nInstalling will overwrite its contents. Choose another folder?"

LangString PREVIOUS_INSTALL ${LANG_RUSSIAN} \
  "BillyTalk уже установлен в другой папке:$\r$\n$0$\r$\n$\r$\nУдалить прежнюю копию? Если нет, она останется на диске и её нельзя будет удалить через «Параметры → Приложения»."
LangString PREVIOUS_INSTALL ${LANG_ENGLISH} \
  "BillyTalk is already installed in another folder:$\r$\n$0$\r$\n$\r$\nRemove the previous copy? If you do not, it stays on disk and Settings > Apps will no longer be able to remove it."

LangString LEFTOVERS ${LANG_RUSSIAN} \
  "Часть файлов удалить не удалось — их держит другая программа:$\r$\n$INSTDIR$\r$\n$\r$\nОни будут удалены при следующей перезагрузке. Запись в «Параметры → Приложения» оставлена, чтобы удаление можно было повторить."
LangString LEFTOVERS ${LANG_ENGLISH} \
  "Some files could not be removed — another program is holding them:$\r$\n$INSTDIR$\r$\n$\r$\nThey will go on the next restart. The Settings > Apps entry is kept so the removal can be retried."

LangString SWAP_FAILED ${LANG_RUSSIAN} \
  "Не удалось заменить файлы прежней версии — их держит другая программа (антивирус, проводник, или сам BillyTalk запущен от имени администратора).$\r$\n$\r$\nПрежняя версия осталась рабочей и нетронутой. Закройте BillyTalk и запустите установку снова."
LangString SWAP_FAILED ${LANG_ENGLISH} \
  "The previous version's files could not be replaced — another program is holding them (an antivirus, Explorer, or BillyTalk itself running elevated).$\r$\n$\r$\nThe previous version is untouched and still works. Close BillyTalk and run the installer again."

LangString ABORTED ${LANG_RUSSIAN} \
  "Установка не завершена. Ничего не сломано: прежняя версия (если была) осталась на месте.$\r$\n$\r$\nЗапустите установщик снова, когда будет удобно."
LangString ABORTED ${LANG_ENGLISH} \
  "The installation did not finish. Nothing is broken: the previous version, if any, is untouched.$\r$\n$\r$\nRun the installer again whenever you like."

; --------------------------------------------------------------------------- ;
; общее
; --------------------------------------------------------------------------- ;
;
; У КАЖДОГО MessageBox есть /SD. В тихом режиме NSIS не пропускает окно без
; него, а ПОКАЗЫВАЕТ — и установка из скрипта или из системы управления
; парком машин виснет на диалоге, который некому нажать. Поймано тем, что
; тест простоял десять минут.

!macro StopBillyTalk TAG
  ; Файлы нельзя перезаписать, пока exe запущен. taskkill есть в любой Windows,
  ; так что плагин не нужен — но его код возврата ничего не доказывает: он
  ; говорит «попросил», а не «закрылся», и на процессе с повышенными правами
  ; возвращает отказ, который раньше просто выбрасывался. Проверено: старая
  ; версия при залоченном файле молча оставляла 145 новых файлов и один
  ; старый — ровно ту смесь версий, которую комментарий ниже называет
  ; «загрузкой чужого кода в наш процесс» (ревью установщика, medium).
  ; Поэтому дожидаемся, пока процесс исчезнет из списка, и отказываемся
  ; продолжать, если он не исчез. TAG — суффикс меток: макрос разворачивается
  ; дважды, а ${__LINE__} внутри него даёт РАЗНЫЕ значения на строке перехода
  ; и на строке метки, так что уникальность приходится передавать снаружи.
  DetailPrint "$(RUNNING_STOPPED)"
  nsExec::Exec 'taskkill /IM "${APP_EXE}" /F'
  Pop $0
  StrCpy $1 0
  wait_gone_${TAG}:
    nsExec::ExecToStack 'cmd /c tasklist /FI "IMAGENAME eq ${APP_EXE}" /NH | find /I "${APP_EXE}"'
    Pop $2
    Pop $3
    StrCmp $2 "0" 0 gone_${TAG}   ; find нашёл процесс → ещё живой
    IntOp $1 $1 + 1
    IntCmp $1 24 give_up_${TAG} 0 give_up_${TAG}   ; ~6 секунд
    Sleep 250
    Goto wait_gone_${TAG}
  give_up_${TAG}:
    MessageBox MB_OK|MB_ICONSTOP "$(STILL_RUNNING)" /SD IDOK
    Abort
  gone_${TAG}:
!macroend

Function CheckDirectory
  ; Через «Обзор» опасности мало — NSIS сам дописывает \BillyTalk. Но путь
  ; можно вписать руками, а установка сносит `_internal` целиком: у другого
  ; приложения на PyInstaller папка называется так же (ревью установщика, low).
  IfFileExists "$INSTDIR\${APP_EXE}" directory_ok    ; наша же установка — можно
  IfFileExists "$INSTDIR\*.*" 0 directory_ok        ; папки нет — можно
  ; Папка есть и она не наша: пуста ли она?
  FindFirst $0 $1 "$INSTDIR\*.*"
  scan_entry:
    StrCmp $1 "" scan_done
    StrCmp $1 "." next_entry
    StrCmp $1 ".." next_entry
    FindClose $0
    MessageBox MB_YESNO|MB_ICONEXCLAMATION "$(FOREIGN_DIR)" /SD IDNO IDNO directory_ok
    Abort   ; «Да, выбрать другую» — остаёмся на странице выбора папки
  next_entry:
    FindNext $0 $1
    Goto scan_entry
  scan_done:
  FindClose $0
  directory_ok:
FunctionEnd

; Своя .onUserAbort объявлена быть не может: её определяет сам MUI, когда задан
; MUI_ABORTWARNING (Interface.nsh:327). Штатная точка подключения —
; MUI_CUSTOMFUNCTION_ABORT, которую MUI вызывает изнутри своей.
Function OnAbort
  ; NSIS сам не откатывает ничего. Раньше отмена посреди копирования
  ; оставляла нулевой BillyTalk.exe, пустой _internal и ЖИВУЮ запись
  ; автозапуска от прошлой установки — Windows каждый вход пыталась бы
  ; запустить сломанный exe (ревью установщика, high). Теперь файлы едут
  ; в отдельную папку, так что убрать надо только её.
  RMDir /r "$INSTDIR\${STAGING}"
  MessageBox MB_OK|MB_ICONINFORMATION "$(ABORTED)" /SD IDOK
FunctionEnd

Function .onInstFailed
  RMDir /r "$INSTDIR\${STAGING}"
FunctionEnd

Function .onInit
  ; Реестр пишем в 64-битное представление явно: установщик 32-битный (NSIS
  ; такие и собирает), и без этого часть веток уехала бы в Wow6432Node, где
  ; их не ищет ни Windows, ни наша же программа.
  SetRegView 64
FunctionEnd

Function un.onInit
  SetRegView 64
FunctionEnd

; --------------------------------------------------------------------------- ;
; установка
; --------------------------------------------------------------------------- ;

Section "BillyTalk" SEC_MAIN
  SectionIn RO
  !insertmacro StopBillyTalk "inst"

  ; --- прежняя копия в другой папке (ревью установщика, low) --- ;
  ReadRegStr $0 HKCU "${REG_UNINST}" "InstallLocation"
  StrCmp $0 "" no_previous
  StrCmp $0 "$INSTDIR" no_previous
  IfFileExists "$0\uninstall.exe" 0 no_previous
  MessageBox MB_YESNO|MB_ICONQUESTION "$(PREVIOUS_INSTALL)" /SD IDNO IDNO no_previous
  ; Тихо и на месте (_?=): молчащий деинсталлятор не спрашивает про личные
  ; данные и не трогает их — это и нужно при переезде в другую папку.
  ExecWait '"$0\uninstall.exe" /S _?=$0'
  RMDir /r "$0"
  no_previous:

  ; --- выкладка в два шага (ревью установщика, high) --- ;
  ; Распаковываем в отдельную папку и подменяем старую версию одним
  ; переименованием в самом конце. Отмена, падение или выключение питания
  ; посреди копирования 85 МБ теперь стоят только временной папки: раньше
  ; в этот момент старый _internal был уже снесён, exe лежал нулевой, а
  ; автозапуск от прошлой установки продолжал его запускать.
  RMDir /r "$INSTDIR\${STAGING}"
  SetOutPath "$INSTDIR\${STAGING}"
  File "..\dist\BillyTalk\${APP_EXE}"
  File /r "..\dist\BillyTalk\_internal"
  File "uninstall-readme.txt"

  ; Всё доехало — теперь подмена. Rename в пределах одного тома это
  ; MoveFile, то есть миллисекунды, а не второе копирование.
  ;
  ; И каждый шаг проверяется. Держать файл может не только наш процесс (его
  ; StopBillyTalk уже дождался), но и антивирус, проводник с открытым
  ; предпросмотром, индексатор. Без проверки установщик в этом случае
  ; рапортовал успех, ничего не заменив: программа осталась прежней версии, а
  ; человек уверен, что обновился. Проверено вживую — файл залочен на
  ; FileShare.Read, код возврата был 0.
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\_internal"
  IfFileExists "$INSTDIR\_internal\*.*" swap_failed
  ClearErrors
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\uninstall-readme.txt"
  Rename "$INSTDIR\${STAGING}\_internal" "$INSTDIR\_internal"
  Rename "$INSTDIR\${STAGING}\${APP_EXE}" "$INSTDIR\${APP_EXE}"
  Rename "$INSTDIR\${STAGING}\uninstall-readme.txt" "$INSTDIR\uninstall-readme.txt"
  IfErrors swap_failed
  RMDir "$INSTDIR\${STAGING}"
  Goto swap_ok
  swap_failed:
    ; Прежняя версия цела: до этой точки её не трогали, а если тронули — RMDir
    ; не смог, то есть файлы на месте. Уносим только временную папку.
    RMDir /r "$INSTDIR\${STAGING}"
    MessageBox MB_OK|MB_ICONSTOP "$(SWAP_FAILED)" /SD IDOK
    Abort
  swap_ok:

  ; --- автозапуск ядра (спека §12) --- ;
  WriteRegStr HKCU "${REG_RUN}" "${APP_NAME}" '"$INSTDIR\${APP_EXE}"'
  ; Запись «выключено», оставшаяся от прошлой установки или от «Параметры →
  ; Автозагрузка», молча отменила бы только что созданный автозапуск: программа
  ; была бы «установлена с автозапуском» и не стартовала. Установка — явная
  ; просьба, поэтому вето снимается (спека §12: проверка отключения — byte0 & 1).
  DeleteRegValue HKCU "${REG_APPROVED}" "${APP_NAME}"

  ; --- Windows Error Reporting (спека §13) --- ;
  ; Без этого полный дамп памяти с аудиобуфером, расшифровкой и ключом уходит
  ; в %LOCALAPPDATA%\CrashDumps и, возможно, в Microsoft.
  WriteRegDWORD HKCU "${REG_WER}" "${APP_EXE}" 1

  ; --- ярлык (спека §11: без него уведомления не показываются вовсе) --- ;
  CreateDirectory "$SMPROGRAMS"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0

  ; --- «Параметры → Приложения» --- ;
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "${REG_UNINST}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${REG_UNINST}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${REG_UNINST}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${REG_UNINST}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKCU "${REG_UNINST}" "URLInfoAbout" "${APP_URL}"
  WriteRegStr HKCU "${REG_UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${REG_UNINST}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${REG_UNINST}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegDWORD HKCU "${REG_UNINST}" "NoModify" 1
  WriteRegDWORD HKCU "${REG_UNINST}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${REG_UNINST}" "EstimatedSize" "$0"
SectionEnd

; --------------------------------------------------------------------------- ;
; удаление
; --------------------------------------------------------------------------- ;

Section "Uninstall"
  !insertmacro StopBillyTalk "uninst"

  DeleteRegValue HKCU "${REG_RUN}" "${APP_NAME}"
  ; Обе записи автозапуска, иначе оставшееся «выключено» затенило бы будущую
  ; установку (спека §12).
  DeleteRegValue HKCU "${REG_APPROVED}" "${APP_NAME}"
  DeleteRegValue HKCU "${REG_WER}" "${APP_EXE}"
  Delete "$SMPROGRAMS\${APP_NAME}.lnk"

  ; /REBOOTOK: файл, который держит антивирус или не убитый процесс, будет
  ; удалён при перезагрузке, а не брошен молча (ревью установщика, medium).
  Delete /REBOOTOK "$INSTDIR\${APP_EXE}"
  Delete /REBOOTOK "$INSTDIR\uninstall-readme.txt"
  RMDir /r /REBOOTOK "$INSTDIR\${STAGING}"
  RMDir /r /REBOOTOK "$INSTDIR\_internal"

  ; Запись в «Параметры → Приложения» снимается ТОЛЬКО если файлы реально
  ; ушли. Раньше она (и сам uninstall.exe) удалялись первыми, и при
  ; залоченном файле человек оставался с папкой на диске, без строки в
  ; «Приложениях» и без деинсталлятора — то есть без единого способа
  ; довести удаление до конца иначе как руками.
  IfFileExists "$INSTDIR\_internal\*.*" leftovers 0
  DeleteRegKey HKCU "${REG_UNINST}"
  Delete /REBOOTOK "$INSTDIR\uninstall.exe"
  ; Только если пусто: если человек положил в папку что-то своё, это его файл.
  RMDir "$INSTDIR"
  Goto files_done
  leftovers:
    IfSilent files_done
    MessageBox MB_OK|MB_ICONEXCLAMATION "$(LEFTOVERS)" /SD IDOK
  files_done:

  ; Личные данные — отдельный вопрос с ответом «нет» по умолчанию. История
  ; диктовок это страховка пользователя (спека §10: текст хранится бессрочно),
  ; и молча стереть её при удалении программы значило бы решить за него.
  ; В тихом режиме (/S) вопроса нет и данные остаются — тихое удаление обязано
  ; быть консервативным.
  ; Прыжок ПО МЕТКЕ, а не «+2»: относительное смещение считается от самой
  ; IfSilent, поэтому +2 перескакивало бы MessageBox и приземлялось ровно на
  ; RMDir — то есть тихое удаление стирало бы историю пользователя молча,
  ; вместо того чтобы её сохранить. Поймано перечитыванием перед запуском
  ; теста, который снёс бы настоящие данные на этой машине.
  IfSilent skip_userdata
  ; /SD IDNO: «нет» — свойство самой инструкции, а не только внешнего
  ; IfSilent. Этот участок уже чинили один раз, и следующая правка не должна
  ; иметь возможности стереть историю диктовок перестановкой строк.
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "$(ASK_USERDATA)" /SD IDNO IDNO skip_userdata
  RMDir /r "$LOCALAPPDATA\${APP_NAME}"
  RMDir /r "$APPDATA\${APP_NAME}"
  skip_userdata:
SectionEnd
