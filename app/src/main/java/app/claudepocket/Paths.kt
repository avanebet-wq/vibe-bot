package app.claudepocket

import android.content.Context
import java.io.File

/** Все пути приложения в одном месте. */
class Paths(ctx: Context) {
    val files: File = ctx.filesDir
    val rootfs = File(files, "rootfs")
    val tmp = File(files, "tmp")
    val shm = File(files, "shm")
    val downloads = File(files, "dl")
    val home = File(files, "home")
    val fakeProc = File(files, "fakeproc")

    /** Ubuntu скачана и распакована. */
    val rootfsReady = File(files, "rootfs.ok")

    /** Установка полностью завершена (Claude Code работает). */
    val installed = File(files, "installed.ok")

    val chatFile = File(files, "chat.json")

    /** Сюда на время переустановки откладываются вход (.claude) и проекты (work). */
    val keep = File(files, "keep")

    /** Выполнен вход в аккаунт. */
    val loggedIn = File(files, "loggedin.ok")

    /** Аватар пользователя (квадрат 512×512), если выбран. */
    val avatar = File(files, "avatar.png")

    private val nativeDir: String = ctx.applicationInfo.nativeLibraryDir
    val proot = File(nativeDir, "libproot.so")
    val loader = File(nativeDir, "libproot-loader.so")

    private val prefs = ctx.getSharedPreferences("claude_pocket", Context.MODE_PRIVATE)

    /** Включается автоматически, если proot не стартует со seccomp (часть телефонов). */
    var noSeccomp: Boolean
        get() = prefs.getBoolean("no_seccomp", false)
        set(value) {
            prefs.edit().putBoolean("no_seccomp", value).apply()
        }

    /** Первоначальная настройка пройдена — разрешения больше не запрашиваются. */
    var onboardingDone: Boolean
        get() = prefs.getBoolean("onboarding_done", false)
        set(value) {
            prefs.edit().putBoolean("onboarding_done", value).commit()
        }

    var fontSizeSp: Int
        get() = prefs.getInt("font_sp", 13)
        set(value) {
            prefs.edit().putInt("font_sp", value).apply()
        }

    /** manual — спрашивать разрешения, acceptEdits — править файлы без вопросов. */
    var permissionMode: String
        get() = prefs.getString("perm_mode", "manual") ?: "manual"
        set(value) {
            prefs.edit().putString("perm_mode", value).apply()
        }

    /** Псевдоним модели для `claude --model` (пусто — по умолчанию). */
    var model: String
        get() = prefs.getString("model", "") ?: ""
        set(value) {
            prefs.edit().putString("model", value).commit()
        }

    /** Уровень мышления для `claude --effort` (пусто — по умолчанию). */
    var effort: String
        get() = prefs.getString("effort", "") ?: ""
        set(value) {
            prefs.edit().putString("effort", value).commit()
        }

    /** Последний список моделей, полученный от Claude Code. */
    var modelsJson: String?
        get() = prefs.getString("models_json", null)
        set(value) {
            prefs.edit().putString("models_json", value).apply()
        }

    /** Идентификатор последнего разговора, чтобы продолжить его после перезапуска. */
    var sessionId: String?
        get() = prefs.getString("session_id", null)
        set(value) {
            prefs.edit().putString("session_id", value).commit()
        }

    fun ensureDirs() {
        listOf(rootfs, tmp, shm, downloads, home, fakeProc).forEach { it.mkdirs() }
    }
}
