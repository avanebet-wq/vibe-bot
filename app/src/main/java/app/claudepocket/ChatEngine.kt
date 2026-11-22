package app.claudepocket

import android.content.Context
import android.os.Handler
import android.os.Looper
import org.json.JSONArray
import org.json.JSONObject
import java.io.OutputStream
import java.util.concurrent.Executors

/**
 * Запускает Claude Code в режиме stream-json (без терминала) и превращает его вывод в список
 * сообщений для чата. Запросы разрешений приходят как данные, и на них отвечает пользователь.
 */
object ChatEngine {

    sealed class Msg {
        class User(val text: String) : Msg()
        class Assistant(var text: String) : Msg()
        class Tool(val id: String, val name: String, val input: JSONObject, var state: Int = RUNNING) : Msg()
        class Permission(
            val requestId: String,
            val toolUseId: String,
            val tool: String,
            val input: JSONObject,
            val suggestion: JSONObject?,
            var decision: Int = PENDING,
        ) : Msg()
        class Info(val text: String, val error: Boolean = false) : Msg()
    }

    const val RUNNING = 0
    const val OK = 1
    const val FAILED = 2
    const val PENDING = 0
    const val ALLOWED = 1
    const val DENIED = 2

    interface Listener {
        fun added(m: Msg)
        fun changed(m: Msg)
        fun busyChanged(busy: Boolean)
        fun cleared()
    }

    val messages = ArrayList<Msg>()
    var listener: Listener? = null
    var busy = false
        private set

    private val main = Handler(Looper.getMainLooper())
    private val writer = Executors.newSingleThreadExecutor()
    private var proc: Process? = null
    private var stdin: OutputStream? = null
    private var current: Msg.Assistant? = null
    private var gotOutput = false
    private var resumed = false
    private var pending: String? = null
    private val tail = ArrayDeque<String>()

    // ------------------------------------------------------------------ public API

    private var loaded = false

    /** Загружает сохранённую переписку (один раз за запуск процесса). */
    fun init(ctx: Context) {
        if (loaded) return
        loaded = true
        messages.addAll(ChatStore.load(Paths(ctx.applicationContext).chatFile))
    }

    /** Немедленно записывает переписку на диск (при сворачивании приложения). */
    fun flush() = ChatStore.saveNow(messages)

    fun isAlive(): Boolean = proc?.isAlive == true

    fun send(ctx: Context, text: String) {
        if (busy) return
        add(Msg.User(text))
        setBusy(true)
        val paths = Paths(ctx.applicationContext)
        if (!isAlive()) start(ctx.applicationContext, paths)
        pending = text
        write(userLine(text))
    }

    fun stop() {
        write(
            JSONObject()
                .put("type", "control_request")
                .put("request_id", "stop-" + System.nanoTime())
                .put("request", JSONObject().put("subtype", "interrupt")).toString(),
        )
    }

    fun respond(p: Msg.Permission, allow: Boolean, rememberEdits: Boolean = false) {
        val resp = JSONObject()
        if (allow) {
            resp.put("behavior", "allow").put("updatedInput", p.input)
            if (rememberEdits && p.suggestion != null) {
                resp.put("updatedPermissions", JSONArray().put(p.suggestion))
            }
        } else {
            resp.put("behavior", "deny").put("message", "Пользователь отклонил действие")
        }
        p.decision = if (allow) ALLOWED else DENIED
        changed(p)
        write(
            JSONObject().put("type", "control_response").put(
                "response",
                JSONObject().put("subtype", "success").put("request_id", p.requestId).put("response", resp),
            ).toString(),
        )
    }

    /** Новый разговор: останавливает Claude и забывает контекст. */
    fun newChat(ctx: Context) {
        shutdown()
        Paths(ctx.applicationContext).sessionId = null
        messages.clear()
        current = null
        setBusy(false)
        ChatStore.saveNow(messages)
        listener?.cleared()
    }

    /** Останавливает процесс, но оставляет историю на экране и идентификатор разговора. */
    fun shutdown() {
        val p = proc
        proc = null
        stdin = null
        p?.destroy()
        setBusy(false)
    }

    // ------------------------------------------------------------------ process

    private fun start(ctx: Context, paths: Paths) {
        val resumeId = paths.sessionId
        resumed = resumeId != null
        gotOutput = false
        tail.clear()
        val args = StringBuilder(
            "claude -p --input-format stream-json --output-format stream-json --verbose " +
                "--include-partial-messages --permission-mode ${paths.permissionMode} --permission-prompt-tool stdio",
        )
        if (resumeId != null) args.append(" --resume ").append(resumeId)
        val hasSd = ctx.checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED
        val pb = ProcessBuilder(Proot.argv(paths, listOf("/bin/bash", "-lc", "mkdir -p ~/work; cd ~/work; exec $args"), hasSd))
        pb.environment().apply {
            clear()
            putAll(Proot.hostEnv(paths, paths.noSeccomp))
        }
        pb.directory(paths.home)
        pb.redirectErrorStream(true)
        val p = pb.start()
        proc = p
        stdin = p.outputStream
        Thread({ readLoop(ctx, paths, p) }, "chat-reader").start()
    }

    private fun readLoop(ctx: Context, paths: Paths, p: Process) {
        try {
            p.inputStream.bufferedReader().forEachLine { line ->
                val o = try { JSONObject(line) } catch (_: Exception) { null }
                if (o == null) {
                    synchronized(tail) { tail.addLast(line); while (tail.size > 6) tail.removeFirst() }
                } else {
                    gotOutput = true
                    main.post { if (proc === p) handle(ctx, paths, o) }
                }
            }
        } catch (_: Exception) {
        }
        p.waitFor()
        main.post { onExit(ctx, paths, p) }
    }

    private fun onExit(ctx: Context, paths: Paths, p: Process) {
        if (proc !== p) return // остановлен нами
        proc = null
        stdin = null
        val t = synchronized(tail) { tail.joinToString("\n") }
        if (t.contains(NO_CONVERSATION) && retryFresh(ctx, paths)) return
        if (busy) {
            add(Msg.Info(if (t.isNotBlank()) "Claude остановился:\n$t" else "Соединение с Claude прервано. Отправь сообщение ещё раз.", true))
            setBusy(false)
        }
    }

    // ------------------------------------------------------------------ events

    private fun handle(ctx: Context, paths: Paths, o: JSONObject) {
        when (o.optString("type")) {
            "system" -> if (o.optString("subtype") == "init") {
                o.optString("session_id").takeIf { it.isNotEmpty() }?.let { paths.sessionId = it }
            }
            "stream_event" -> {
                val e = o.optJSONObject("event") ?: return
                if (e.optString("type") == "content_block_delta") {
                    val d = e.optJSONObject("delta") ?: return
                    if (d.optString("type") == "text_delta") appendText(d.optString("text"))
                }
            }
            "assistant" -> {
                val content = o.optJSONObject("message")?.optJSONArray("content") ?: return
                val texts = StringBuilder()
                for (i in 0 until content.length()) {
                    val b = content.getJSONObject(i)
                    if (b.optString("type") == "text") texts.append(b.optString("text"))
                }
                if (texts.isNotEmpty()) {
                    val c = current
                    if (c != null) {
                        c.text = texts.toString()
                        changed(c)
                    } else {
                        add(Msg.Assistant(texts.toString()))
                    }
                }
                current = null
                for (i in 0 until content.length()) {
                    val b = content.getJSONObject(i)
                    if (b.optString("type") == "tool_use") {
                        add(Msg.Tool(b.optString("id"), b.optString("name"), b.optJSONObject("input") ?: JSONObject()))
                    }
                }
            }
            "user" -> {
                val content = o.optJSONObject("message")?.optJSONArray("content") ?: return
                for (i in 0 until content.length()) {
                    val b = content.optJSONObject(i) ?: continue
                    if (b.optString("type") == "tool_result") {
                        val id = b.optString("tool_use_id")
                        messages.filterIsInstance<Msg.Tool>().firstOrNull { it.id == id }?.let {
                            it.state = if (b.optBoolean("is_error")) FAILED else OK
                            changed(it)
                        }
                    }
                }
            }
            "control_request" -> {
                val r = o.optJSONObject("request") ?: return
                if (r.optString("subtype") == "can_use_tool") {
                    val sug = r.optJSONArray("permission_suggestions")?.let { a ->
                        (0 until a.length()).map { a.getJSONObject(it) }.firstOrNull { it.optString("mode") == "acceptEdits" }
                    }
                    add(
                        Msg.Permission(
                            o.optString("request_id"), r.optString("tool_use_id"), r.optString("tool_name"),
                            r.optJSONObject("input") ?: JSONObject(), sug,
                        ),
                    )
                }
            }
            "result" -> {
                o.optString("session_id").takeIf { it.isNotEmpty() }?.let { paths.sessionId = it }
                current = null
                pending = null
                if (o.optBoolean("is_error")) {
                    val text = o.optString("result").ifBlank { "Не удалось выполнить запрос" }
                    val all = text + (o.optJSONArray("errors")?.toString() ?: "")
                    // сбрасываем разговор ТОЛЬКО если Claude прямо сказал, что такого разговора нет
                    if (all.contains(NO_CONVERSATION) && retryFresh(ctx, paths)) return
                    add(Msg.Info(text, true))
                }
                setBusy(false)
            }
        }
    }

    private const val NO_CONVERSATION = "No conversation found"

    /** Разговор исчез с диска: начинаем новый и повторяем последнее сообщение. */
    private fun retryFresh(ctx: Context, paths: Paths): Boolean {
        val retry = pending ?: return false
        if (!resumed) return false
        paths.sessionId = null
        val old = proc
        proc = null
        stdin = null
        old?.destroy()
        start(ctx, paths)
        write(userLine(retry))
        return true
    }

    private fun appendText(delta: String) {
        val c = current
        if (c == null) {
            val m = Msg.Assistant(delta)
            current = m
            add(m)
        } else {
            c.text += delta
            changed(c)
        }
    }

    // ------------------------------------------------------------------ helpers

    private fun userLine(text: String): String =
        JSONObject().put("type", "user")
            .put("message", JSONObject().put("role", "user").put("content", text)).toString()

    private fun write(line: String) {
        val out = stdin ?: return
        writer.execute {
            try {
                out.write((line + "\n").toByteArray())
                out.flush()
            } catch (_: Exception) {
            }
        }
    }

    private fun add(m: Msg) {
        messages.add(m)
        ChatStore.scheduleSave(messages)
        listener?.added(m)
    }

    private fun changed(m: Msg) {
        ChatStore.scheduleSave(messages)
        listener?.changed(m)
    }

    private fun setBusy(b: Boolean) {
        if (busy == b) return
        busy = b
        listener?.busyChanged(b)
    }
}
