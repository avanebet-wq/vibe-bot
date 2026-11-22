package app.claudepocket

import android.os.Handler
import android.os.Looper
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Сохраняет переписку на диск, чтобы чат выглядел так же после закрытия приложения и перезагрузки. */
object ChatStore {
    private const val MAX = 300
    private val main = Handler(Looper.getMainLooper())
    private var file: File? = null
    private var scheduled = false

    fun load(f: File): List<ChatEngine.Msg> {
        file = f
        val out = ArrayList<ChatEngine.Msg>()
        try {
            if (!f.exists()) return out
            val arr = JSONArray(f.readText())
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                when (o.optString("t")) {
                    "u" -> out.add(ChatEngine.Msg.User(o.optString("text")))
                    "a" -> out.add(ChatEngine.Msg.Assistant(o.optString("text")))
                    "f" -> out.add(ChatEngine.Msg.File(o.optString("path")))
                    "sh" -> out.add(
                        ChatEngine.Msg.Shell(
                            o.optString("cmd"), o.optString("output"),
                            if (o.optInt("state") == ChatEngine.OK) ChatEngine.OK else ChatEngine.FAILED, o.optInt("exit"),
                        ),
                    )
                    "i" -> out.add(ChatEngine.Msg.Info(o.optString("text"), o.optBoolean("err")))
                    "tool" -> out.add(
                        ChatEngine.Msg.Tool(
                            o.optString("id"), o.optString("name"),
                            o.optJSONObject("input") ?: JSONObject(),
                            // действие, прерванное закрытием приложения, не должно висеть «в работе»
                            if (o.optInt("state") == ChatEngine.FAILED) ChatEngine.FAILED else ChatEngine.OK,
                        ),
                    )
                    "perm" -> out.add(
                        ChatEngine.Msg.Permission(
                            o.optString("rid"), o.optString("tid"), o.optString("tool"),
                            o.optJSONObject("input") ?: JSONObject(), null,
                            // запрос, оставшийся без ответа, уже недействителен
                            if (o.optInt("decision") == ChatEngine.ALLOWED) ChatEngine.ALLOWED else ChatEngine.DENIED,
                        ),
                    )
                }
            }
        } catch (_: Exception) {
            // повреждённый файл — начинаем с пустого чата, но разговор Claude сохранён отдельно
        }
        return out
    }

    /** Откладывает запись на полсекунды, чтобы не писать на диск при каждом символе. */
    fun scheduleSave(messages: List<ChatEngine.Msg>) {
        if (scheduled) return
        scheduled = true
        main.postDelayed({
            scheduled = false
            saveNow(messages)
        }, 500)
    }

    fun saveNow(messages: List<ChatEngine.Msg>) {
        val f = file ?: return
        try {
            val arr = JSONArray()
            messages.takeLast(MAX).forEach { m ->
                arr.put(
                    when (m) {
                        is ChatEngine.Msg.User -> JSONObject().put("t", "u").put("text", m.text)
                        is ChatEngine.Msg.Assistant -> JSONObject().put("t", "a").put("text", m.text)
                        is ChatEngine.Msg.Info -> JSONObject().put("t", "i").put("text", m.text).put("err", m.error)
                        is ChatEngine.Msg.File -> JSONObject().put("t", "f").put("path", m.guestPath)
                        is ChatEngine.Msg.Shell -> JSONObject().put("t", "sh").put("cmd", m.cmd)
                            .put("output", m.output.takeLast(5000)).put("state", m.state).put("exit", m.exit)
                        is ChatEngine.Msg.Tool -> JSONObject().put("t", "tool").put("id", m.id).put("name", m.name)
                            .put("input", m.input).put("state", m.state)
                        is ChatEngine.Msg.Permission -> JSONObject().put("t", "perm").put("rid", m.requestId)
                            .put("tid", m.toolUseId).put("tool", m.tool).put("input", m.input).put("decision", m.decision)
                    },
                )
            }
            // запись через временный файл: при внезапном выключении останется целая старая версия
            val tmp = File(f.parentFile, f.name + ".tmp")
            tmp.writeText(arr.toString())
            if (!tmp.renameTo(f)) {
                f.delete()
                tmp.renameTo(f)
            }
        } catch (_: Exception) {
        }
    }
}
