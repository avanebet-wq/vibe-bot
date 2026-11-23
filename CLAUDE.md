# Claude Pocket — руководство по проекту

Это инструкция для Claude (и для человека), который продолжает разработку. Читай её первой.

## Что это
Android-приложение **Claude Pocket** (`app.claudepocket`). Одна кнопка ставит на телефон Ubuntu (через proot, без Termux), внутри ставит Claude Code и даёт обычный экран чата вместо терминала. Репозиторий: `avanebet-wq/vibe-bot`, рабочая ветка `claude/claude-on-mobile-tmsuxw`.

## Как общаться с владельцем
- Отвечать по-русски, коротко, без восторгов. Делать только то, что попросили.
- После каждого изменения приложения: собрать APK, закоммитить, **запушить в рабочую ветку** и отправить APK пользователю. Пулреквесты не создавать, пока не попросят.
- Честно говорить, что не проверено на телефоне. Эмулятора на сервере нет (нет KVM), реальный вид стекла видит только пользователь. Скриншот или запись экрана от него — единственная проверка.
- Цвета только системные (Material You), в приложении нет настройки цвета. Ограничений Claude (запросов разрешений) не добавлять: приложение запускает Claude в режиме `bypassPermissions` сознательно.
- Версию в `app/build.gradle.kts` (`versionCode`, `versionName`) увеличивать при каждой сборке для пользователя.

## Карта проекта (`app/src/main/java/app/claudepocket/`)
| Файл | За что отвечает |
|---|---|
| `MainActivity.kt` | Хозяин всех экранов-панелей: установка, вход, чат, навыки, профиль, онбординг. Маршрутизация в `route()`. Базовый класс `ComponentActivity` (нужен для Compose). В `attachBaseContext` задаётся масштаб интерфейса (`COMPACT`), шрифт не зависит от системного масштаба и «жирного текста». |
| **`ChatScreen.kt`** | **Весь экран чата на Compose**: список сообщений, карточки, поле ввода, стеклянные кнопки, живой фон. Главный файл для визуальных правок. |
| `ChatController.kt` | Мост между `ChatEngine` и Compose (`ChatUi`). Методы `attach/detach/prefill/addAttachment/hideKeyboard`. |
| `ChatEngine.kt` | Синглтон: запускает `claude -p --input-format stream-json` внутри proot, разбирает вывод в сообщения, отправка/остановка/ответы на запросы, выбор модели и уровня мышления, прямые команды через `!`. Тут же `liveLine` (строка о реальном процессе внутри Ubuntu). |
| `ChatStore.kt` | Сохранение переписки в `chat.json`. |
| `Proot.kt`, `Installer.kt`, `Paths.kt` | Запуск proot, установка Ubuntu и Claude Code, пути и настройки. |
| `LoginFlow.kt` | Вход в аккаунт через `claude auth login` (ссылка и код). |
| `Files.kt` | Вложения, отправка файлов от Claude (`[[send:путь]]`), текст-памятка для Claude (`SYSTEM_PROMPT`, `MEMORY`). |
| `Markdown.kt` | Разбор Markdown в `Spanned`; в `ChatScreen.kt` он превращается в `AnnotatedString` (`toAnnotated`). |
| `Skills*.kt`, `Sheets.kt`, `GlassMenu.kt`, `Onboarding.kt`, `Views.kt` | Остальные экраны и мелкие View (пока не на Compose): навыки, нижний лист выбора модели, меню профиля, слайды настройки, аватар с пузырями. |
| `res/layout/activity_main.xml` | Все панели. Чат — это `chat_panel` с одним `ComposeView` (`chat_compose`) и двумя невидимыми «якорями» (`menu_anchor`, `plus_anchor`) для всплывающих меню. |
| `res/values*/colors.xml` | Цвета: системные (`system_*`) в `-v31`, запасные в обычных папках. Токены: `bg, panel, key, line, accent, on_accent, text, muted, error, success`. |
| `res/font/` | Шрифт Inter (OFL), семейство `inter.xml`; подключён темой `AppTheme` (`android:fontFamily`). |
| `assets/` | Скрипты для Ubuntu (`setup.sh`, `launch.sh`, `install_skill.sh`) и `models.json`. |

## Где «те кнопки» (стекло) — `ChatScreen.kt`
Ищи по этим именам:
- **`GlassBox`** — одна стеклянная плашка. Внутри `drawBackdrop` из библиотеки Backdrop (Kyant): `vibrancy()`, `blur(2dp)`, `lens(12dp, 24dp)`, подсветка края `Highlight`, поверхность `onDrawSurface`. Здесь же упругая реакция на касание (`layerBlock` + `Interactive`).
- **Кнопка профиля сверху справа** — в `ChatScreen`, блок `GlassBox(... CircleShape ...)` с `Icon(R.drawable.ic_user)`; нажатие вызывает `onMenu` и в `MainActivity` открывает `GlassMenu` с пунктом «Профиль».
- **Скрепка / стоп слева от поля** и **поле ввода-таблетка** — функция `Composer`. Кнопка отправки находится внутри поля справа. Рамка с бегущим бликом при работе — `drawGlow`, индикатор — `Spinner`.
- **Живой фон под стеклом** — `Aurora` (пятна цвета системы, сдвигаются при прокрутке). Стекло преломляет именно то, что лежит внутри `layerBackdrop(backdrop)`: сообщения и `Aurora`. Элементы управления в этот слой класть нельзя (получится рекурсия).
- **Блик от наклона телефона** — `rememberTiltAngle` (акселерометр). **Время кадров** — `rememberFrameTime` (не зависит от системной настройки «Анимация»).
- **Реакция на касание** — класс `Interactive` (по мотивам `InteractiveHighlight` из каталога Kyant).
- Параметры вида: число `COMPACT` в `MainActivity.kt` (общий масштаб интерфейса), прозрачность вуали стекла — переменная `surface` в `ChatScreen`.

Как найти что-то быстро: `grep -n "GlassBox\|drawBackdrop\|Aurora\|Composer" app/src/main/java/app/claudepocket/ChatScreen.kt`.

## Стекло и ограничения Android
- Библиотека: `io.github.kyant0:backdrop:1.0.6` (Compose, Apache-2.0), эталон — https://github.com/Kyant0/AndroidLiquidGlass (смотреть `LiquidButton.kt`, `Lens.kt`, `InteractiveHighlight.kt`).
- Линза (преломление) работает только на **Android 13+**. На старых версиях остаётся размытие без линзы. Всегда проверять, что на API < 33 ничего не падает.
- Версии подобраны под библиотеку и менять их осторожно: AGP 8.13.0, Kotlin 2.3.10 (плагин Compose той же версии), Gradle 8.14.3, compileSdk 36, Compose 1.10.3. Библиотека требует `minCompileSdk 36`.
- **`targetSdk = 28` сделан намеренно** (как у Termux): только так приложение может запускать файлы из своей папки данных (proot). Не поднимать.
- Только `arm64-v8a`: proot лежит в `jniLibs/arm64-v8a/` как `libproot.so` и др. (`useLegacyPackaging = true`).

## Сборка
- Нужны JDK 17+, Android SDK (platform 36, build-tools 35+). На самом телефоне собрать APK тяжело, основная сборка идёт в GitHub Actions: `.github/workflows/build-apk.yml` собирает на каждый пуш и обновляет релиз «latest-apk».
- Локально: `export ANDROID_HOME=/путь/к/sdk && ./gradlew assembleRelease` → `app/build/outputs/apk/release/app-release.apk`.
- Подпись фиксированная (`app/debug.keystore`), чтобы новая версия ставилась поверх старой.

## Известные хвосты
- Чат на Compose и стекло **не проверены на реальном телефоне** (только сборка). Возможные места: положение меню по нажатию на профиль и на скрепку (привязаны к невидимым якорям), высота поля ввода и отступ списка под ним, нагрузка на батарею от живого фона и стекла.
- Остальные экраны (установка, вход, навыки, профиль, онбординг, лист модели) ещё на обычных View. Их можно переносить на Compose по одному, не переписывая всё сразу.
- Старые методы `showChatMenu`, `showSkills`, `choosePermissionMode` в `MainActivity` недостижимы (пункты меню спрятаны по просьбе владельца).

## Сторонние материалы
Inter (OFL 1.1), Backdrop (Apache-2.0). Ссылки есть в `README.md`.
