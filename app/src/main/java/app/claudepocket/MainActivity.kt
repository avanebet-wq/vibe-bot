package app.claudepocket

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.PopupMenu
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

class MainActivity : Activity() {

    private lateinit var paths: Paths
    private val main = Handler(Looper.getMainLooper())

    private lateinit var installPanel: View
    private lateinit var loginPanel: View
    private lateinit var chatPanel: View
    private lateinit var onboarding: Onboarding
    private lateinit var chat: ChatController
    private lateinit var btnLoginOpen: Button
    private lateinit var btnLoginSubmit: Button
    private lateinit var btnLoginCopy: TextView
    private lateinit var btnLoginBrowser: TextView
    private var loginOpened = false
    private var lastClip: String? = null
    private lateinit var loginCode: EditText
    private lateinit var loginStatus: TextView
    private lateinit var detailsToggle: TextView
    private var login: LoginFlow? = null
    private var loginUrl: String? = null
    private lateinit var btnInstall: Button
    private lateinit var stepText: TextView
    private lateinit var hintText: TextView
    private lateinit var progress: ProgressBar
    private lateinit var logScroll: ScrollView
    private lateinit var logText: TextView

    private var installing = false
    private val logLines = ArrayDeque<String>()
    private var logDirty = false

    // ---------------------------------------------------------------- lifecycle

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        paths = Paths(this)
        setContentView(R.layout.activity_main)

        installPanel = findViewById(R.id.install_panel)
        loginPanel = findViewById(R.id.login_panel)
        chatPanel = findViewById(R.id.chat_panel)
        onboarding = Onboarding(this, findViewById(R.id.onb_panel)) { route() }
        chat = ChatController(this, chatPanel, { showChatMenu(it) }, { chooseModel() })
        btnLoginOpen = findViewById(R.id.btn_login_open)
        btnLoginSubmit = findViewById(R.id.btn_login_submit)
        btnLoginCopy = findViewById(R.id.btn_login_copy)
        btnLoginBrowser = findViewById(R.id.btn_login_browser)
        loginCode = findViewById(R.id.login_code)
        loginStatus = findViewById(R.id.login_status)
        detailsToggle = findViewById(R.id.details_toggle)
        btnInstall = findViewById(R.id.btn_install)
        stepText = findViewById(R.id.step_text)
        hintText = findViewById(R.id.hint_text)
        progress = findViewById(R.id.progress)
        logScroll = findViewById(R.id.log_scroll)
        logText = findViewById(R.id.log_text)

        btnInstall.background = rounded(getColor(R.color.accent), dp(12))
        btnLoginOpen.background = rounded(getColor(R.color.accent), dp(12))
        btnLoginSubmit.background = rounded(getColor(R.color.accent), dp(12))
        btnInstall.setOnClickListener { startInstall() }
        detailsToggle.setOnClickListener {
            val show = logScroll.visibility != View.VISIBLE
            logScroll.visibility = if (show) View.VISIBLE else View.GONE
            detailsToggle.text = if (show) "Скрыть подробности" else "Показать подробности"
        }
        btnLoginOpen.setOnClickListener { loginUrl?.let { openLogin(it, preferClaudeApp = true) } }
        btnLoginBrowser.setOnClickListener { loginUrl?.let { openLogin(it, preferClaudeApp = false) } }
        btnLoginCopy.setOnClickListener { loginUrl?.let { copyToClipboard(it); toast("Ссылка скопирована") } }
        btnLoginSubmit.setOnClickListener { submitCode() }
        findViewById<View>(R.id.login_skip).setOnClickListener {
            paths.loggedIn.writeText("1")
            login?.cancel()
            showChat()
        }
        if (paths.onboardingDone) route() else onboarding.show()
    }

    override fun onResume() {
        super.onResume()
        if (onboarding.isVisible) {
            onboarding.refresh()
            return
        }
        if (!installing && paths.installed.exists()) {
            if (chatPanel.visibility == View.VISIBLE) {
                chat.attach()
            } else if (loginPanel.visibility != View.VISIBLE) {
                route()
            }
        }
    }

    override fun onPause() {
        ChatEngine.flush()
        chat.detach()
        super.onPause()
    }

    // ---------------------------------------------------------------- screens

    private fun route() {
        when {
            !paths.installed.exists() -> showInstall()
            !paths.loggedIn.exists() -> showLogin()
            else -> showChat()
        }
    }

    private fun showLogin() {
        installPanel.visibility = View.GONE
        chatPanel.visibility = View.GONE
        loginPanel.visibility = View.VISIBLE
        startLogin()
    }

    private fun startLogin() {
        loginUrl = null
        btnLoginOpen.isEnabled = false
        btnLoginOpen.text = "Готовлю ссылку…"
        btnLoginCopy.visibility = View.GONE
        btnLoginBrowser.visibility = View.GONE
        loginOpened = false
        btnLoginSubmit.isEnabled = true
        loginStatus.setTextColor(getColor(R.color.muted))
        loginStatus.text = ""
        loginCode.setText("")
        login?.cancel()
        val flow = LoginFlow(applicationContext, object : LoginFlow.Ui {
            override fun onUrl(url: String) {
                main.post {
                    loginUrl = url
                    btnLoginOpen.isEnabled = true
                    btnLoginOpen.text = "Открыть страницу входа"
                    btnLoginCopy.visibility = View.VISIBLE
                    btnLoginBrowser.visibility = View.VISIBLE
                }
            }

            override fun onFinished(error: String?) {
                main.post { onLoginFinished(error) }
            }
        })
        login = flow
        flow.start()
    }

    private fun submitCode() {
        val code = loginCode.text.toString().trim()
        if (code.isEmpty()) {
            toast("Вставь код из браузера")
            return
        }
        btnLoginSubmit.isEnabled = false
        loginStatus.setTextColor(getColor(R.color.muted))
        loginStatus.text = "Проверяю код…"
        login?.submit(code)
    }

    private fun onLoginFinished(error: String?) {
        if (error == null) {
            showChat()
        } else {
            loginStatus.setTextColor(Color.parseColor("#FF6B6B"))
            loginStatus.text = error
            btnLoginOpen.isEnabled = false
            btnLoginOpen.text = "Готовлю новую ссылку…"
            btnLoginCopy.visibility = View.GONE
            main.postDelayed({ if (loginPanel.visibility == View.VISIBLE) startLoginKeepError(error) }, 800)
        }
    }

    /** Перезапускает вход, оставив сообщение об ошибке на экране. */
    private fun startLoginKeepError(error: String) {
        startLogin()
        loginStatus.setTextColor(Color.parseColor("#FF6B6B"))
        loginStatus.text = error
    }

    /** Открывает страницу входа: сначала в приложении Claude (если оно умеет), иначе в браузере. */
    private fun openLogin(url: String, preferClaudeApp: Boolean) {
        loginOpened = true
        if (preferClaudeApp) {
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).setPackage(CLAUDE_APP)
            if (intent.resolveActivity(packageManager) != null) {
                try {
                    startActivity(intent)
                    return
                } catch (_: Exception) {
                }
            }
        }
        openUrl(url)
    }

    /** Если после входа в буфере лежит код входа — подставляем и отправляем его сами. */
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus || !loginOpened || loginPanel.visibility != View.VISIBLE) return
        if (!btnLoginSubmit.isEnabled) return // код уже отправлен, ждём результата
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = cm.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString()?.trim()
        if (clip != null && clip != lastClip && CODE_RE.matches(clip)) {
            lastClip = clip // один и тот же код дважды не отправляем
            loginCode.setText(clip)
            submitCode()
        }
    }

    private fun openUrl(url: String) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (e: Exception) {
            toast("Не удалось открыть ссылку — скопируй её и открой в браузере")
        }
    }

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_SHORT).show()

    private fun showInstall() {
        installPanel.visibility = View.VISIBLE
        loginPanel.visibility = View.GONE
        chatPanel.visibility = View.GONE
    }

    /** Один раз просит доступ к памяти телефона; true — запрос показан, экран откроется после ответа. */
    @Suppress("FunctionOnlyReturningConstant")
    private fun askStorageOnce(): Boolean = false // разрешения запрашивает только экран настройки

    private fun showChat() {
        if (askStorageOnce()) return
        installPanel.visibility = View.GONE
        loginPanel.visibility = View.GONE
        chatPanel.visibility = View.VISIBLE
        KeepAliveService.start(this, "Claude готов к работе")
        ChatEngine.init(this)
        chat.setModelLabel(modelLabel(paths.model))
        chat.attach()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (onboarding.isVisible) {
            onboarding.refresh()
            return
        }
        if (requestCode == REQ_STORAGE) {
            if (hasStorage()) {
                Toast.makeText(this, "Доступ выдан. Новый чат (меню ⋮) подключит /sdcard", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun hasStorage(): Boolean =
        checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED &&
            android.os.Environment.getExternalStorageDirectory().canRead()

    // ---------------------------------------------------------------- install

    private fun startInstall() {
        if (installing) return
        installing = true
        btnInstall.isEnabled = false
        btnInstall.text = "Идёт установка…"
        stepText.setTextColor(getColor(R.color.text))
        stepText.text = "Начинаю…"
        stepText.visibility = View.VISIBLE
        progress.visibility = View.VISIBLE
        progress.isIndeterminate = true
        detailsToggle.visibility = View.VISIBLE
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        KeepAliveService.start(this, "Устанавливаю Claude Code…")

        Installer(applicationContext, object : Installer.Ui {
            override fun log(line: String) {
                main.post { appendLog(line) }
            }

            override fun step(title: String, percent: Int) {
                main.post {
                    stepText.text = title
                    progress.isIndeterminate = percent < 0
                    if (percent >= 0) progress.progress = percent
                }
            }

            override fun finished(error: String?) {
                main.post { onInstallFinished(error) }
            }
        }).start()
    }

    private fun onInstallFinished(error: String?) {
        installing = false
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if (error == null) {
            KeepAliveService.stop(this)
            showLogin()
        } else {
            KeepAliveService.stop(this)
            logScroll.visibility = View.VISIBLE
            detailsToggle.text = "Скрыть подробности"
            btnInstall.isEnabled = true
            btnInstall.text = "Повторить установку"
            progress.visibility = View.GONE
            stepText.setTextColor(Color.parseColor("#FF6B6B"))
            stepText.text = "Ошибка: $error"
            hintText.text = "Можно нажать кнопку ещё раз — уже скачанное повторно не загружается."
        }
    }

    private fun appendLog(raw: String) {
        val line = ANSI.replace(raw, "").trimEnd()
        if (line.isEmpty()) return
        logLines.addLast(line)
        while (logLines.size > 300) logLines.removeFirst()
        if (!logDirty) {
            logDirty = true
            main.postDelayed({
                logDirty = false
                logText.text = logLines.joinToString("\n")
                logScroll.post { logScroll.fullScroll(View.FOCUS_DOWN) }
            }, 200)
        }
    }

    private fun copyToClipboard(text: String) {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("claude", text))
    }

    // ---------------------------------------------------------------- menu

    private fun showChatMenu(anchor: View) {
        val m = PopupMenu(this, anchor)
        m.menu.add(0, 1, 0, "Новый чат")
        m.menu.add(0, 2, 1, "Разрешения: " + if (paths.permissionMode == "acceptEdits") "править без вопросов" else "спрашивать")
        m.menu.add(0, 3, 2, "Доступ к памяти телефона")
        m.menu.add(0, 4, 3, "Переустановить всё")
        m.menu.add(0, 5, 4, "Войти в другой аккаунт")
        m.setOnMenuItemClickListener {
            when (it.itemId) {
                5 -> { paths.loggedIn.delete(); ChatEngine.shutdown(); showLogin() }
                1 -> ChatEngine.newChat(this)
                2 -> choosePermissionMode()
                3 -> requestStorage()
                4 -> confirmReinstall()
            }
            true
        }
        m.show()
    }

    private val models = listOf(
        Triple("", "По умолчанию", "модель по умолчанию для твоего аккаунта"),
        Triple("sonnet", "Sonnet", "сбалансированная: быстро и умно"),
        Triple("opus", "Opus", "для сложных задач"),
        Triple("haiku", "Haiku", "самая быстрая"),
        Triple("fable", "Fable", "новейшая, может потребовать кредиты"),
    )

    private fun modelLabel(alias: String) = models.firstOrNull { it.first == alias }?.second ?: alias

    private fun chooseModel() {
        if (ChatEngine.busy) {
            toast("Дождись конца ответа или останови его")
            return
        }
        val items = models.map { "${it.second} — ${it.third}" }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle("Модель")
            .setSingleChoiceItems(items, models.indexOfFirst { it.first == paths.model }.coerceAtLeast(0)) { d, which ->
                val alias = models[which].first
                if (alias != paths.model) {
                    paths.model = alias
                    ChatEngine.shutdown() // следующее сообщение запустит Claude с новой моделью, разговор сохранится
                    chat.setModelLabel(modelLabel(alias))
                    toast("Модель: ${models[which].second}")
                }
                d.dismiss()
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    private fun choosePermissionMode() {
        val modes = arrayOf("manual", "acceptEdits")
        val names = arrayOf("Спрашивать перед каждым действием", "Править файлы без вопросов (команды — с вопросом)")
        AlertDialog.Builder(this)
            .setTitle("Разрешения Claude")
            .setSingleChoiceItems(names, modes.indexOf(paths.permissionMode)) { d, which ->
                if (paths.permissionMode != modes[which]) {
                    paths.permissionMode = modes[which]
                    ChatEngine.shutdown() // следующий запрос стартует с новым режимом, разговор сохранится
                    toast("Режим изменён")
                }
                d.dismiss()
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    private fun requestStorage() {
        if (hasStorage()) {
            Toast.makeText(this, "Доступ уже есть: папка /sdcard внутри Linux", Toast.LENGTH_LONG).show()
        } else {
            requestPermissions(
                arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE, Manifest.permission.WRITE_EXTERNAL_STORAGE),
                REQ_STORAGE,
            )
        }
    }

    private fun confirmReinstall() {
        AlertDialog.Builder(this)
            .setTitle("Переустановить всё?")
            .setMessage("Система Ubuntu будет удалена и установлена заново. Вход в аккаунт и папка проектов ~/work сохранятся; остальные файлы внутри системы и установленные программы пропадут.")
            .setPositiveButton("Удалить и установить заново") { _, _ ->
                ChatEngine.shutdown()
                KeepAliveService.stop(this)
                Toast.makeText(this, "Удаляю…", Toast.LENGTH_SHORT).show()
                Thread {
                    Installer.wipeRootfs(paths)
                    main.post {
                        btnInstall.isEnabled = true
                        btnInstall.text = "Установить всё"
                        stepText.visibility = View.GONE
                        progress.visibility = View.GONE
                        logScroll.visibility = View.GONE
                        detailsToggle.visibility = View.GONE
                        detailsToggle.text = "Показать подробности"
                        logLines.clear()
                        hintText.text = "Нужен интернет, около 1 ГБ свободного места и 5–15 минут. Не закрывай приложение и не блокируй экран."
                        showInstall()
                    }
                }.start()
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    // ---------------------------------------------------------------- helpers

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun sp(v: Int) =
        android.util.TypedValue.applyDimension(android.util.TypedValue.COMPLEX_UNIT_SP, v.toFloat(), resources.displayMetrics).toInt()

    private fun rounded(color: Int, radius: Int) = GradientDrawable().apply {
        setColor(color)
        cornerRadius = radius.toFloat()
    }

    companion object {
        private const val REQ_STORAGE = 1
        private const val CLAUDE_APP = "com.anthropic.claude"
        private val CODE_RE = Regex("^[A-Za-z0-9_\\-]{10,}#[A-Za-z0-9_\\-]{10,}$")
        private val ANSI = Regex("\u001B\\[[0-9;?]*[ -/]*[@-~]")
    }
}
