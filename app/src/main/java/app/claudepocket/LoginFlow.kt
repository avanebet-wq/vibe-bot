package app.claudepocket

import android.content.Context
import java.io.OutputStream

/**
 * Вход в аккаунт без терминала: запускает `claude auth login`, достаёт из его вывода ссылку,
 * затем отправляет введённый код в стандартный ввод и проверяет результат через `claude auth status`.
 */
class LoginFlow(ctx: Context, private val ui: Ui) {

    interface Ui {
        fun onUrl(url: String)

        /** [error] = null — вход выполнен. */
        fun onFinished(error: String?)
    }

    private val p = Paths(ctx)
    @Volatile private var stdin: OutputStream? = null
    @Volatile private var process: Process? = null

    fun start() {
        cancel()
        Thread({
            try {
                val proc = launch("claude auth login")
                process = proc
                stdin = proc.outputStream
                var gotUrl = false
                proc.inputStream.bufferedReader().forEachLine { line ->
                    if (!gotUrl) {
                        URL_RE.find(line)?.let {
                            gotUrl = true
                            ui.onUrl(it.value)
                        }
                    }
                }
                proc.waitFor()
                if (process !== proc) return@Thread // отменён и запущен заново
                if (isLoggedIn()) {
                    p.loggedIn.writeText("1")
                    ui.onFinished(null)
                } else {
                    ui.onFinished(
                        if (gotUrl) "Код не подошёл. Он мог устареть или быть неполным — нажми «Открыть страницу входа» ещё раз и возьми новый."
                        else "Не удалось получить ссылку для входа. Проверь интернет и попробуй снова.",
                    )
                }
            } catch (t: Throwable) {
                ui.onFinished(t.message ?: t.toString())
            }
        }, "login").start()
    }

    fun submit(code: String) {
        val out = stdin ?: return
        Thread({
            try {
                out.write((code.trim() + "\n").toByteArray())
                out.flush()
            } catch (_: Exception) {
            }
        }, "login-submit").start()
    }

    fun cancel() {
        process?.destroy()
        process = null
        stdin = null
    }

    private fun isLoggedIn(): Boolean {
        val proc = launch("claude auth status --json")
        val text = proc.inputStream.bufferedReader().readText()
        proc.waitFor()
        return Regex("\"loggedIn\"\\s*:\\s*true").containsMatchIn(text)
    }

    private fun launch(command: String): Process {
        val pb = ProcessBuilder(Proot.argv(p, listOf("/bin/bash", "-lc", command), false))
        pb.environment().apply {
            clear()
            putAll(Proot.hostEnv(p, p.noSeccomp))
        }
        pb.directory(p.home)
        pb.redirectErrorStream(true)
        return pb.start()
    }

    companion object {
        private val URL_RE = Regex("https://\\S+")
    }
}
