package app.claudepocket

import android.animation.ValueAnimator
import android.app.Activity
import android.graphics.Bitmap
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.text.TextUtils
import android.text.method.LinkMovementMethod
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.graphics.ColorUtils
import org.json.JSONObject

/** Экран чата в стиле «Профиль»: живая шапка, пузыри сообщений, карточки действий и поле ввода со свечением. */
class ChatController(
    private val act: Activity,
    private val root: View,
    private val onMenu: (View) -> Unit,
    private val onModel: () -> Unit,
    private val onPlus: (View) -> Unit,
    private val onFile: (ChatEngine.Msg.File, String) -> Unit,
) : ChatEngine.Listener {

    private val scroll: ScrollView = root.findViewById(R.id.chat_scroll)
    private val list: LinearLayout = root.findViewById(R.id.chat_list)
    private val status: TextView = root.findViewById(R.id.chat_status)
    private val working: View = root.findViewById(R.id.chat_working)
    private val field: GlowFrame = root.findViewById(R.id.chat_field)
    private val fieldIcon: View = root.findViewById(R.id.chat_field_icon)
    private val activityText: TextView = root.findViewById(R.id.chat_activity)
    private val left: ImageView = root.findViewById(R.id.chat_left)
    private val input: EditText = root.findViewById(R.id.chat_input)
    private val send: ImageView = root.findViewById(R.id.chat_send)
    private val views = HashMap<ChatEngine.Msg, View>()
    private val attachScroll: View = root.findViewById(R.id.attach_scroll)
    private val attachRow: LinearLayout = root.findViewById(R.id.attach_row)
    private val pendingAttachments = ArrayList<Files.Attachment>()

    private val ticker = Handler(Looper.getMainLooper())
    private val tick = object : Runnable {
        override fun run() {
            updateStatusText()
            ticker.postDelayed(this, 500)
        }
    }

    private val accent = act.getColor(R.color.accent)
    private val onAccent = act.getColor(R.color.on_accent)
    private val panel = act.getColor(R.color.panel)
    private val keyColor = act.getColor(R.color.key)
    private val lineColor = act.getColor(R.color.line)
    private val textColor = act.getColor(R.color.text)
    private val muted = act.getColor(R.color.muted)
    private val bgColor = act.getColor(R.color.bg)

    init {
        root.findViewById<View>(R.id.glass_btn).setOnClickListener { onMenu(it) }
        fieldIcon.setOnClickListener { onModel() }

        left.setOnClickListener {
            if (ChatEngine.busy) {
                status.text = "Останавливаю…"
                ChatEngine.stop()
            } else {
                onPlus(it)
            }
        }
        send.setOnClickListener {
            if (ChatEngine.busy) return@setOnClickListener
            val text = input.text.toString().trim()
            if (text.isNotEmpty() || pendingAttachments.isNotEmpty()) {
                val files = ArrayList(pendingAttachments)
                pendingAttachments.clear()
                renderChips()
                input.setText("")
                ChatEngine.send(act, text, files)
            }
        }
        refreshSend()
    }

    // ------------------------------------------------------------------ ввод

    /** Подставляет текст в поле ввода (например, заготовку запроса). */
    fun prefill(text: String) {
        input.setText(text)
        input.setSelection(text.length)
        input.requestFocus()
    }

    /** Подключает контроллер к движку и отрисовывает уже накопленную переписку. */
    fun attach() {
        ChatEngine.listener = this
        render()
    }

    fun detach() {
        if (ChatEngine.listener === this) ChatEngine.listener = null
        ticker.removeCallbacks(tick)
        field.glowing = false
    }

    /** Показывает «кольцо + Думает… N с» в поле, светящуюся обводку и серую подсказку, пока идёт ответ. */
    private fun setThinking(on: Boolean) {
        ticker.removeCallbacks(tick)
        field.glowing = on
        input.visibility = if (on) View.GONE else View.VISIBLE
        fieldIcon.visibility = if (on) View.GONE else View.VISIBLE
        working.visibility = if (on) View.VISIBLE else View.GONE
        activityText.visibility = if (on) View.VISIBLE else View.GONE
        activityText.background = GradientDrawable().apply {
            setColor(panel)
            cornerRadius = dp(16).toFloat()
            setStroke(dp(1), lineColor)
        }
        left.setImageResource(if (on) R.drawable.ic_stop else R.drawable.ic_clip)
        left.contentDescription = if (on) "Остановить" else "Прикрепить"
        if (on) {
            updateStatusText()
            ticker.postDelayed(tick, 500)
        }
        refreshSend()
    }

    private fun updateStatusText() {
        val sec = ((android.os.SystemClock.elapsedRealtime() - ChatEngine.busySince) / 1000).coerceAtLeast(0)
        val time = if (sec < 60) "$sec с" else "${sec / 60} мин ${"%02d".format(sec % 60)} с"
        val last = ChatEngine.messages.lastOrNull()
        val what = when {
            last is ChatEngine.Msg.Shell && last.state == ChatEngine.RUNNING -> "Выполняется"
            last is ChatEngine.Msg.Permission && last.decision == ChatEngine.PENDING -> "Ждёт твоего ответа"
            else -> "Думает"
        }
        status.text = "$what… $time"
        activityText.text = currentActivity()
    }

    /** Подсказка над полем: что Claude делает прямо сейчас. */
    private fun currentActivity(): String {
        val msgs = ChatEngine.messages
        val last = msgs.lastOrNull()
        if (last is ChatEngine.Msg.Shell && last.state == ChatEngine.RUNNING) return "Команда: " + short(last.cmd, 60)
        val perm = msgs.filterIsInstance<ChatEngine.Msg.Permission>().lastOrNull { it.decision == ChatEngine.PENDING }
        if (perm != null) return "Нужно твоё разрешение: " + live(perm.tool, perm.input).lowercase()
        val running = msgs.filterIsInstance<ChatEngine.Msg.Tool>().lastOrNull { it.state == ChatEngine.RUNNING }
        if (running != null) return live(running.name, running.input)
        val ph = ChatEngine.phase
        return when {
            ph == "thinking" -> "Размышляет над задачей"
            ph == "writing" -> "Пишет ответ"
            ph.startsWith("prep:") -> "Готовит действие: " + live(ph.removePrefix("prep:"), JSONObject()).lowercase()
            ph == "after_tool" -> "Разбирает результат"
            else -> "Читает запрос"
        }
    }

    private fun short(s: String, n: Int) = s.replace('\n', ' ').let { if (it.length > n) it.take(n) + "…" else it }

    /** Что делает инструмент, коротко и в настоящем времени. */
    private fun live(name: String, input: JSONObject): String {
        fun file(k: String) = short(input.optString(k).substringAfterLast('/'), 40)
        fun arg(k: String) = short(input.optString(k), 50)
        fun with(base: String, d: String) = if (d.isEmpty()) base else "$base $d"
        return when (name) {
            "Bash" -> with("Выполняет команду:", arg("command")).trimEnd(':', ' ')
            "Write" -> with("Создаёт файл", file("file_path"))
            "Edit", "MultiEdit" -> with("Правит файл", file("file_path"))
            "NotebookEdit" -> with("Правит блокнот", file("notebook_path"))
            "Read" -> with("Читает файл", file("file_path"))
            "Glob" -> with("Ищет файлы:", arg("pattern")).trimEnd(':', ' ')
            "Grep" -> with("Ищет в файлах:", arg("pattern")).trimEnd(':', ' ')
            "WebFetch" -> with("Открывает страницу", arg("url"))
            "WebSearch" -> with("Ищет в интернете:", arg("query")).trimEnd(':', ' ')
            "Task", "Agent" -> with("Запускает помощника:", arg("description")).trimEnd(':', ' ')
            "TodoWrite" -> "Обновляет план"
            else -> name
        }
    }

    private fun render() {
        list.removeAllViews()
        views.clear()
        if (ChatEngine.messages.isEmpty()) list.addView(welcome())
        ChatEngine.messages.forEach { addView(it, scrollDown = false) }
        setThinking(ChatEngine.busy)
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
        setThinking(busy)
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
            chip.text = a.name.take(26) + "   ✕"
            chip.setTextColor(textColor)
            chip.textSize = 13f
            chip.maxLines = 1
            chip.setPadding(dp(14), dp(9), dp(14), dp(9))
            chip.background = GradientDrawable().apply {
                setColor(panel)
                cornerRadius = dp(18).toFloat()
                setStroke(dp(1), lineColor)
            }
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
        text = "Привет! Я Claude Code.\n\nНапиши, что нужно сделать, например:\n• «Создай страницу-визитку и открой её»\n• «Объясни, что в этой папке»\n• «Исправь ошибку в main.py»\n\nЧтобы выполнить команду напрямую, начни сообщение с «!», например: !ls или !apt install nano\n\nФайлы создаются в папке ~/work."
        setTextColor(muted)
        textSize = 15f
        gravity = Gravity.CENTER
        setPadding(dp(16), dp(20), dp(16), dp(8))
    }

    private fun create(m: ChatEngine.Msg): View = when (m) {
        is ChatEngine.Msg.User -> bubble(true)
        is ChatEngine.Msg.Assistant -> bubble(false)
        is ChatEngine.Msg.Tool -> card()
        is ChatEngine.Msg.Permission -> card(accentBorder = true)
        is ChatEngine.Msg.Info -> card()
        is ChatEngine.Msg.Shell -> card()
        is ChatEngine.Msg.File -> card()
    }

    private fun bubble(user: Boolean): View {
        val t = TextView(act)
        t.setTextColor(if (user) onAccent else textColor)
        t.textSize = 16f
        t.setLineSpacing(0f, 1.1f)
        t.setPadding(dp(14), dp(10), dp(14), dp(10))
        t.setTextIsSelectable(true)
        t.movementMethod = LinkMovementMethod.getInstance()
        val r = dp(20).toFloat()
        val tail = dp(6).toFloat()
        t.background = GradientDrawable().apply {
            setColor(if (user) accent else panel)
            // у пузыря один острый нижний угол — «хвостик» в сторону автора
            cornerRadii = floatArrayOf(r, r, r, r, if (user) tail else r, if (user) tail else r, if (user) r else tail, if (user) r else tail)
            if (!user) setStroke(dp(1), lineColor)
        }
        val lp = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        lp.setMargins(if (user) dp(48) else 0, dp(4), if (user) 0 else dp(28), dp(4))
        lp.gravity = if (user) Gravity.END else Gravity.START
        t.layoutParams = lp
        return t
    }

    private fun card(accentBorder: Boolean = false): View {
        val c = LinearLayout(act)
        c.orientation = LinearLayout.VERTICAL
        c.setPadding(dp(14), dp(12), dp(14), dp(12))
        c.background = GradientDrawable().apply {
            setColor(panel)
            cornerRadius = dp(18).toFloat()
            setStroke(dp(1), if (accentBorder) ColorUtils.setAlphaComponent(accent, 140) else lineColor)
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
                c.addView(label(m.text, if (m.error) act.getColor(R.color.error) else muted, 14f))
            }
        }
    }

    /** Круглая кнопка со значком и подписью под ней (как «звонок / видео / поиск» в профиле). */
    private fun actionItem(icon: Int, caption: String, onClick: () -> Unit): View {
        val box = LinearLayout(act)
        box.orientation = LinearLayout.VERTICAL
        box.gravity = Gravity.CENTER_HORIZONTAL
        val iv = ImageView(act)
        iv.setImageDrawable(Icons.get(act, icon, textColor))
        iv.setBackgroundResource(R.drawable.bg_circle)
        iv.setPadding(dp(12), dp(12), dp(12), dp(12))
        iv.contentDescription = caption
        box.addView(iv, LinearLayout.LayoutParams(dp(46), dp(46)))
        val tv = TextView(act)
        tv.text = caption
        tv.setTextColor(muted)
        tv.textSize = 11f
        tv.setPadding(0, dp(4), 0, 0)
        box.addView(tv)
        box.setOnClickListener { onClick() }
        box.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        return box
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
                    val iv = ImageView(act)
                    iv.setImageBitmap(bmp)
                    iv.adjustViewBounds = true
                    iv.maxHeight = dp(260)
                    iv.scaleType = ImageView.ScaleType.FIT_START
                    iv.setPadding(0, 0, 0, dp(10))
                    c.addView(iv)
                }
            } catch (_: Throwable) {
            }
        }
        val head = LinearLayout(act)
        head.orientation = LinearLayout.HORIZONTAL
        head.gravity = Gravity.CENTER_VERTICAL
        val ic = ImageView(act)
        ic.setImageDrawable(Icons.get(act, R.drawable.ic_file, onAccent))
        ic.setBackgroundResource(R.drawable.bg_circle_accent)
        ic.setPadding(dp(11), dp(11), dp(11), dp(11))
        head.addView(ic, LinearLayout.LayoutParams(dp(44), dp(44)))
        val col = LinearLayout(act)
        col.orientation = LinearLayout.VERTICAL
        col.setPadding(dp(12), 0, 0, 0)
        col.addView(label(f.name, textColor, 15f, bold = true))
        col.addView(label(Files.humanSize(f.length()) + " · " + mime, muted, 12f))
        head.addView(col)
        c.addView(head)
        val row = LinearLayout(act)
        row.orientation = LinearLayout.HORIZONTAL
        row.setPadding(0, dp(12), 0, 0)
        row.addView(actionItem(R.drawable.ic_open, "Открыть") { onFile(m, "open") })
        row.addView(actionItem(R.drawable.ic_dl, "Сохранить") { onFile(m, "save") })
        row.addView(actionItem(R.drawable.ic_share, "Отправить") { onFile(m, "share") })
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
        if (m.state == ChatEngine.FAILED && m.exit != 0) c.addView(label("Код завершения: ${m.exit}", act.getColor(R.color.error), 12f))
    }

    private fun bindTool(m: ChatEngine.Msg.Tool, c: LinearLayout) {
        c.removeAllViews()
        val (title, detail) = describe(m.name, m.input)
        val mark = when (m.state) {
            ChatEngine.OK -> "✓ "
            ChatEngine.FAILED -> "✗ "
            else -> "… "
        }
        c.addView(label(mark + title, if (m.state == ChatEngine.FAILED) act.getColor(R.color.error) else muted, 14f, bold = true))
        if (detail.isNotEmpty()) c.addView(label(detail, muted, 13f, mono = true))
    }

    private fun bindPermission(m: ChatEngine.Msg.Permission, c: LinearLayout) {
        c.removeAllViews()
        val (title, detail) = describe(m.tool, m.input)
        c.addView(label("Claude просит разрешения:\n$title", textColor, 15f, bold = true))
        if (detail.isNotEmpty()) c.addView(label(detail, muted, 13f, mono = true))
        when (m.decision) {
            ChatEngine.ALLOWED -> c.addView(label("✓ Разрешено", act.getColor(R.color.success), 14f))
            ChatEngine.DENIED -> c.addView(label("✗ Отклонено", act.getColor(R.color.error), 14f))
            else -> {
                val row = LinearLayout(act)
                row.orientation = LinearLayout.HORIZONTAL
                row.setPadding(0, dp(10), 0, 0)
                row.addView(pill("Разрешить", true, 1f, 0) { ChatEngine.respond(m, true) })
                row.addView(pill("Отклонить", false, 1f, dp(8)) { ChatEngine.respond(m, false) })
                c.addView(row)
                if (m.suggestion != null) {
                    val always = pill("Разрешать правки файлов без вопросов", false, 0f, 0) { ChatEngine.respond(m, true, rememberEdits = true) }
                    (always.layoutParams as LinearLayout.LayoutParams).apply { width = ViewGroup.LayoutParams.MATCH_PARENT; topMargin = dp(8) }
                    c.addView(always)
                }
            }
        }
    }

    /** Кнопка-таблетка высотой 44dp: акцентная (главная) или нейтральная. */
    private fun pill(text: String, primary: Boolean, weight: Float, leftMargin: Int, onClick: () -> Unit): TextView {
        val b = TextView(act)
        b.text = text
        b.gravity = Gravity.CENTER
        b.setTextColor(if (primary) onAccent else textColor)
        b.textSize = 14f
        b.typeface = Typeface.DEFAULT_BOLD
        b.maxLines = 1
        b.ellipsize = TextUtils.TruncateAt.END
        b.setPadding(dp(14), 0, dp(14), 0)
        b.background = GradientDrawable().apply {
            setColor(if (primary) accent else keyColor)
            cornerRadius = dp(22).toFloat()
        }
        val lp = LinearLayout.LayoutParams(if (weight > 0f) 0 else ViewGroup.LayoutParams.WRAP_CONTENT, dp(44), weight)
        lp.leftMargin = leftMargin
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
        send.alpha = if (ChatEngine.busy) 0.45f else 1f
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
}
