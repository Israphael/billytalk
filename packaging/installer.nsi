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
; вопрос про личные данные при удалении, и корректная отмена на середине.

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

; --------------------------------------------------------------------------- ;
; общее
; --------------------------------------------------------------------------- ;

!macro StopBillyTalk
  ; Файлы нельзя перезаписать, пока exe запущен. taskkill есть в любой Windows,
  ; так что плагин для этого не нужен; код возврата не важен — «не найден»
  ; такой же успех, как «закрыт».
  DetailPrint "$(RUNNING_STOPPED)"
  nsExec::Exec 'taskkill /IM "${APP_EXE}" /F'
  Pop $0
  Sleep 500
!macroend

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
  !insertmacro StopBillyTalk

  SetOutPath "$INSTDIR"
  ; Старый бандл сносим целиком: PyInstaller меняет состав _internal между
  ; версиями, а файл, оставшийся от прошлой сборки, — это загрузка чужого кода
  ; в наш процесс.
  RMDir /r "$INSTDIR\_internal"
  File "..\dist\BillyTalk\${APP_EXE}"
  File /r "..\dist\BillyTalk\_internal"
  File "uninstall-readme.txt"

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
  !insertmacro StopBillyTalk

  DeleteRegValue HKCU "${REG_RUN}" "${APP_NAME}"
  ; Обе записи автозапуска, иначе оставшееся «выключено» затенило бы будущую
  ; установку (спека §12).
  DeleteRegValue HKCU "${REG_APPROVED}" "${APP_NAME}"
  DeleteRegValue HKCU "${REG_WER}" "${APP_EXE}"
  DeleteRegKey HKCU "${REG_UNINST}"
  Delete "$SMPROGRAMS\${APP_NAME}.lnk"

  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\uninstall-readme.txt"
  Delete "$INSTDIR\uninstall.exe"
  RMDir /r "$INSTDIR\_internal"
  ; Только если пусто: если человек положил в папку что-то своё, это его файл.
  RMDir "$INSTDIR"

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
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "$(ASK_USERDATA)" IDNO skip_userdata
  RMDir /r "$LOCALAPPDATA\${APP_NAME}"
  RMDir /r "$APPDATA\${APP_NAME}"
  skip_userdata:
SectionEnd
