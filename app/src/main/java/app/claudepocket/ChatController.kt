package app.claudepocket

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.method.LinkMovementMethod
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject

/** Экран чата: пузыри сообщений, карточки действий Claude и запросы разрешений. */
class ChatController(
    private val act: Activity,
    private val root: View,
    private val onMenu: (View) -> Unit,
    private val onModel: () -> Unit,
    private val onEffort: () -> Unit,
    private val onPlus: (View) -> Unit,
    private val onFile: (ChatEngine.Msg.File, String) -> Unit,
) : ChatEngine.Listener {

    private val scroll: ScrollView = root.findViewById(R.id.chat_scroll)
    private val list: LinearLayout = root.findViewById(R.id.chat_list)
    private val status: TextView = root.findViewById(R.id.chat_status)
    private val input: EditText = root.findViewById(R.id.chat_input)
    private val send: Button = root.findViewById(R.id.chat_send)
    private val views = HashMap<ChatEngine.Msg, View>()
    private val plus: TextView = root.findViewById(R.id.chat_plus)
    private val attachScroll: View = root.findViewById(R.id.attach_scroll)
    private val attachRow: LinearLayout = root.findViewById(R.id.attach_row)
    private val pendingAttachments = ArrayList<Files.Attachment>()
    private val pillModel: TextView = root.findViewById(R.id.chat_model_pill)
    private val pillEffort: TextView = root.findViewById(R.id.chat_effort_pill)

    private val accent = act.getColor(R.color.accent)
    private val panel = act.getColor(R.color.panel)
    private val keyColor = act.getColor(R.color.key)
    private val textColor = act.getColor(R.color.text)
    private val muted = act.getColor(R.color.muted)

    init {
        root.findViewById<View>(R.id.chat_menu).setOnClickListener { onMenu(it) }
        val composer: View = root.findViewById(R.id.composer)
        composer.background = GradientDrawable().apply {
            setColor(panel)
            cornerRadius = dp(26).toFloat()
            setStroke(dp(1), keyColor)
        }
        pillModel.background = rounded(keyColor, dp(22))
        plus.background = GradientDrawable().apply {
            setColor(keyColor)
            shape = GradientDrawable.OVAL
        }
        plus.setOnClickListener { onPlus(it) }
        pillEffort.background = rounded(keyColor, dp(22))
        pillModel.setOnClickListener { onModel() }
        pillEffort.setOnClickListener { onEffort() }
        send.background = GradientDrawable().apply {
            setColor(accent)
            shape = GradientDrawable.OVAL
        }
        send.setOnClickListener {
            if (ChatEngine.busy) {
                ChatEngine.stop()
            } else {
                val text = input.text.toString().trim()
                if (text.isNotEmpty() || pendingAttachments.isNotEmpty()) {
                    val files = ArrayList(pendingAttachments)
                    pendingAttachments.clear()
                    renderChips()
                    input.setText("")
                    ChatEngine.send(act, text, files)
                }
            }
        }
        refreshSend()
    }

    /** Подставляет текст в поле ввода (например, заготовку запроса). */
    fun prefill(text: String) {
        input.setText(text)
        input.setSelection(text.length)
        input.requestFocus()
    }

    /** Обновляет подписи на кнопках модели и уровня мышления. */
    fun setPills(model: String, effort: String, effortEnabled: Boolean) {
        pillModel.text = "$model ▾"
        pillEffort.text = "🧠 $effort"
        pillEffort.alpha = if (effortEnabled) 1f else 0.4f
    }

    /** Подключает контроллер к движку и отрисовывает уже накопленную переписку. */
    fun attach() {
        ChatEngine.listener = this
        render()
    }

    fun detach() {
        if (ChatEngine.listener === this) ChatEngine.listener = null
    }

    private fun render() {
        list.removeAllViews()
        views.clear()
        if (ChatEngine.messages.isEmpty()) list.addView(welcome())
        ChatEngine.messages.forEach { addView(it, scrollDown = false) }
        status.visibility = if (ChatEngine.busy) View.VISIBLE else View.GONE
        refreshSend()
        scrollToEnd()
    }

    // ------------------------------------------------------------------ Listener

    override fun added(m: ChatEngine.Msg) {
        if (views.isEmpty() && list.childCount == 1) list.removeAllViews() // убрать приветствие
        addView(m, scrollDown = true)
    }

    override fun changed(m: ChatEngine.Msg) {
        val v = views[m] ?: return
        val stick = nearBottom()
        bind(m, v)
        if (stick) scrollToEnd()
    }

    override fun busyChanged(busy: Boolean) {
        status.visibility = if (busy) View.VISIBLE else View.GONE
        refreshSend()
    }

    override fun cleared() {
        render()
    }

    override fun removed(m: ChatEngine.Msg) {
        views.remove(m)?.let { list.removeView(it) }
    }

    /** Добавляет выбранный файл в полоску вложений над полем ввода. */
    fun addAttachment(a: Files.Attachment) {
        pendingAttachments.add(a)
        renderChips()
    }

    private fun renderChips() {
        attachRow.removeAllViews()
        attachScroll.visibility = if (pendingAttachments.isEmpty()) View.GONE else View.VISIBLE
        pendingAttachments.forEach { a ->
            val chip = TextView(act)
            chip.text = (if (a.isImage) "🖼 " else "📎 ") + a.name.take(26) + "  ✕"
            chip.setTextColor(textColor)
            chip.textSize = 13f
            chip.maxLines = 1
            chip.setPadding(dp(12), dp(8), dp(12), dp(8))
            chip.background = rounded(keyColor, dp(16))
            val lp = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            lp.rightMargin = dp(6)
            chip.layoutParams = lp
            chip.setOnClickListener {
                pendingAttachments.remove(a)
                renderChips()
            }
            attachRow.addView(chip)
        }
    }

    override fun modelsChanged() {
        onModelsChanged?.invoke()
    }

    var onModelsChanged: (() -> Unit)? = null

    // ------------------------------------------------------------------ views

    private fun addView(m: ChatEngine.Msg, scrollDown: Boolean) {
        val v = create(m)
        views[m] = v
        list.addView(v)
        bind(m, v)
        if (scrollDown) scrollToEnd()
    }

    private fun welcome(): View = TextView(act).apply {
        text = "Привет! Я Claude Code.\n\nНапиши, что нужно сделать, — например:\n• «Создай страницу-визитку и открой её»\n• «Объясни, что в этой папке»\n• «Исправь ошибку в main.py»\n\nЧтобы выполнить команду напрямую, начни сообщение с «!», например: !ls или !apt install nano\n\nФайлы создаются в папке ~/work."
        setTextColor(muted)
        textSize = 15f
        setPadding(dp(8), dp(24), dp(8), dp(8))
    }

    private fun create(m: ChatEngine.Msg): View = when (m) {
        is ChatEngine.Msg.User -> bubble(true)
        is ChatEngine.Msg.Assistant -> bubble(false)
        is ChatEngine.Msg.Tool -> card()
        is ChatEngine.Msg.Permission -> card(accentBorder = true)
        is ChatEngine.Msg.Info -> card()
        is ChatEngine.Msg.Shell -> card()
        is ChatEngine.Msg.File -> card(accentBorder = true)
    }

    private fun bubble(user: Boolean): View {
        val t = TextView(act)
        t.setTextColor(if (user) Color.WHITE else textColor)
        t.textSize = 16f
        t.setLineSpacing(0f, 1.1f)
        t.setPadding(dp(14), dp(10), dp(14), dp(10))
        t.setTextIsSelectable(true)
        t.movementMethod = LinkMovementMethod.getInstance()
        t.background = rounded(if (user) accent else panel, dp(16))
        val lp = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        lp.setMargins(if (user) dp(48) else 0, dp(6), if (user) 0 else dp(24), dp(6))
        lp.gravity = if (user) Gravity.END else Gravity.START
        t.layoutParams = lp
        return t
    }

    private fun card(accentBorder: Boolean = false): View {
        val c = LinearLayout(act)
        c.orientation = LinearLayout.VERTICAL
        c.setPadding(dp(12), dp(10), dp(12), dp(10))
        c.background = GradientDrawable().apply {
            setColor(keyColor)
            cornerRadius = dp(12).toFloat()
            if (accentBorder) setStroke(dp(1), accent)
        }
        val lp = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        lp.setMargins(0, dp(4), 0, dp(4))
        c.layoutParams = lp
        return c
    }

    private fun bind(m: ChatEngine.Msg, v: View) {
        when (m) {
            is ChatEngine.Msg.User -> (v as TextView).text = m.text
            is ChatEngine.Msg.Assistant -> {
                val sp = android.text.SpannableStringBuilder(Markdown.render(Files.strip(m.text), 0x33808080))
                android.text.util.Linkify.addLinks(sp, android.text.util.Linkify.WEB_URLS) // ссылки становятся нажимаемыми
                (v as TextView).text = sp
            }
            is ChatEngine.Msg.Tool -> bindTool(m, v as LinearLayout)
            is ChatEngine.Msg.Permission -> bindPermission(m, v as LinearLayout)
            is ChatEngine.Msg.Shell -> bindShell(m, v as LinearLayout)
            is ChatEngine.Msg.File -> bindFile(m, v as LinearLayout)
            is ChatEngine.Msg.Info -> {
                val c = v as LinearLayout
                c.removeAllViews()
                c.addView(label(m.text, if (m.error) Color.parseColor("#FF6B6B") else muted, 14f))
            }
        }
    }

    private fun bindFile(m: ChatEngine.Msg.File, c: LinearLayout) {
        c.removeAllViews()
        val f = Files.resolveGuest(act, m.guestPath)
        if (f == null) {
            c.addView(label("Файл больше недоступен: " + m.guestPath.substringAfterLast('/'), muted, 14f))
            return
        }
        val mime = Files.mimeOf(f.name)
        if (mime.startsWith("image/")) {
            try {
                val opts = android.graphics.BitmapFactory.Options().apply { inSampleSize = 4 }
                val bmp = android.graphics.BitmapFactory.decodeFile(f.path, opts)
                if (bmp != null) {
                    val iv = android.widget.ImageView(act)
                    iv.setImageBitmap(bmp)
                    iv.adjustViewBounds = true
                    iv.maxHeight = dp(260)
                    iv.scaleType = android.widget.ImageView.ScaleType.FIT_START
                    iv.setPadding(0, 0, 0, dp(8))
                    c.addView(iv)
                }
            } catch (_: Throwable) {
            }
        }
        c.addView(label("📎 " + f.name, textColor, 15f, bold = true))
        c.addView(label(Files.humanSize(f.length()) + " · " + mime, muted, 12f))
        val row = LinearLayout(act)
        row.orientation = LinearLayout.HORIZONTAL
        row.setPadding(0, dp(8), 0, 0)
        row.addView(actionButton("Открыть", accent) { onFile(m, "open") })
        row.addView(actionButton("Сохранить", keyColor) { onFile(m, "save") })
        row.addView(actionButton("Отправить", keyColor) { onFile(m, "share") })
        c.addView(row)
    }

    private fun bindShell(m: ChatEngine.Msg.Shell, c: LinearLayout) {
        c.removeAllViews()
        val head = when (m.state) {
            ChatEngine.OK -> "✓ "
            ChatEngine.FAILED -> "✗ "
            else -> "… "
        }
        c.addView(label(head + m.cmd, textColor, 14f, bold = true, mono = true))
        if (m.output.isNotEmpty()) {
            val out = TextView(act)
            out.text = if (m.output.length > 4000) "…\n" + m.output.takeLast(4000) else m.output
            out.setTextColor(muted)
            out.textSize = 12f
            out.typeface = Typeface.MONOSPACE
            out.setTextIsSelectable(true)
            out.setPadding(0, dp(4), 0, dp(2))
            c.addView(out)
        }
        if (m.state == ChatEngine.FAILED && m.exit != 0) c.addView(label("Код завершения: ${m.exit}", Color.parseColor("#FF6B6B"), 12f))
    }

    private fun bindTool(m: ChatEngine.Msg.Tool, c: LinearLayout) {
        c.removeAllViews()
        val (title, detail) = describe(m.name, m.input)
        val mark = when (m.state) {
            ChatEngine.OK -> "✓ "
            ChatEngine.FAILED -> "✗ "
            else -> "… "
        }
        c.addView(label(mark + title, if (m.state == ChatEngine.FAILED) Color.parseColor("#FF6B6B") else muted, 14f, bold = true))
        if (detail.isNotEmpty()) c.addView(label(detail, muted, 13f, mono = true))
    }

    private fun bindPermission(m: ChatEngine.Msg.Permission, c: LinearLayout) {
        c.removeAllViews()
        val (title, detail) = describe(m.tool, m.input)
        c.addView(label("Claude просит разрешения:\n$title", textColor, 15f, bold = true))
        if (detail.isNotEmpty()) c.addView(label(detail, muted, 13f, mono = true))
        when (m.decision) {
            ChatEngine.ALLOWED -> c.addView(label("✓ Разрешено", Color.parseColor("#7BD88F"), 14f))
            ChatEngine.DENIED -> c.addView(label("✗ Отклонено", Color.parseColor("#FF6B6B"), 14f))
            else -> {
                val row = LinearLayout(act)
                row.orientation = LinearLayout.HORIZONTAL
                row.setPadding(0, dp(10), 0, 0)
                row.addView(actionButton("Разрешить", accent) { ChatEngine.respond(m, true) })
                row.addView(actionButton("Отклонить", keyColor) { ChatEngine.respond(m, false) })
                c.addView(row)
                if (m.suggestion != null) {
                    val always = actionButton("Разрешать правки файлов без вопросов", keyColor) { ChatEngine.respond(m, true, rememberEdits = true) }
                    (always.layoutParams as LinearLayout.LayoutParams).apply { width = ViewGroup.LayoutParams.MATCH_PARENT; topMargin = dp(6); leftMargin = 0 }
                    c.addView(always)
                }
            }
        }
    }

    private fun actionButton(text: String, color: Int, onClick: () -> Unit): Button {
        val b = Button(act)
        b.text = text
        b.isAllCaps = false
        b.setTextColor(Color.WHITE)
        b.textSize = 14f
        b.minHeight = 0
        b.minimumHeight = dp(44)
        b.background = rounded(color, dp(10))
        val lp = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        lp.leftMargin = dp(6)
        lp.rightMargin = dp(0)
        b.layoutParams = lp
        b.setOnClickListener { onClick() }
        return b
    }

    private fun label(text: String, color: Int, size: Float, bold: Boolean = false, mono: Boolean = false): TextView {
        val t = TextView(act)
        t.text = if (text.length > 600) text.take(600) + "…" else text
        t.setTextColor(color)
        t.textSize = size
        if (bold) t.typeface = Typeface.DEFAULT_BOLD
        if (mono) t.typeface = Typeface.MONOSPACE
        t.setPadding(0, dp(2), 0, dp(2))
        return t
    }

    private fun describe(name: String, input: JSONObject): Pair<String, String> {
        fun s(k: String) = input.optString(k)
        return when (name) {
            "Bash" -> "Выполняет команду" to s("command")
            "Write" -> "Создаёт файл" to s("file_path")
            "Edit", "MultiEdit" -> "Правит файл" to s("file_path")
            "NotebookEdit" -> "Правит блокнот" to s("notebook_path")
            "Read" -> "Читает файл" to s("file_path")
            "Glob" -> "Ищет файлы" to s("pattern")
            "Grep" -> "Ищет в файлах" to s("pattern")
            "WebFetch" -> "Открывает страницу" to s("url")
            "WebSearch" -> "Ищет в интернете" to s("query")
            "Task", "Agent" -> "Запускает помощника" to s("description")
            else -> name to ""
        }
    }

    // ------------------------------------------------------------------ misc

    private fun refreshSend() {
        send.text = if (ChatEngine.busy) "■" else "↑"
    }

    private fun nearBottom(): Boolean {
        val child = scroll.getChildAt(0) ?: return true
        return child.bottom - (scroll.scrollY + scroll.height) < dp(120)
    }

    private fun scrollToEnd() {
        scroll.post { scroll.fullScroll(View.FOCUS_DOWN) }
    }

    fun hideKeyboard() {
        val imm = act.getSystemService(android.content.Context.INPUT_METHOD_SERVICE) as InputMethodManager
        imm.hideSoftInputFromWindow(input.windowToken, 0)
    }

    private fun dp(v: Int) = (v * act.resources.displayMetrics.density).toInt()

    private fun rounded(color: Int, radius: Int) = GradientDrawable().apply {
        setColor(color)
        cornerRadius = radius.toFloat()
    }
}
