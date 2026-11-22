package app.claudepocket

import android.util.Log
import com.termux.terminal.TerminalSession
import com.termux.terminal.TerminalSessionClient

/**
 * Сессия терминала живёт отдельно от Activity: если экран пересоздан, она продолжает работать.
 * Вызовы терминала пересылаются текущему экрану, если он есть.
 */
object SessionHolder {
    var session: TerminalSession? = null
    var ui: Ui? = null

    interface Ui {
        fun onScreenChanged()
        fun onSessionFinished(session: TerminalSession)
        fun copyToClipboard(text: String)
        fun pasteFromClipboard(session: TerminalSession)
    }

    val client = object : TerminalSessionClient {
        override fun onTextChanged(changedSession: TerminalSession) {
            ui?.onScreenChanged()
        }

        override fun onTitleChanged(changedSession: TerminalSession) {}

        override fun onSessionFinished(finishedSession: TerminalSession) {
            ui?.onSessionFinished(finishedSession)
        }

        override fun onCopyTextToClipboard(session: TerminalSession, text: String) {
            ui?.copyToClipboard(text)
        }

        override fun onPasteTextFromClipboard(session: TerminalSession?) {
            session?.let { ui?.pasteFromClipboard(it) }
        }

        override fun onBell(session: TerminalSession) {}
        override fun onColorsChanged(session: TerminalSession) {}
        override fun onTerminalCursorStateChange(state: Boolean) {}
        override fun getTerminalCursorStyle(): Int? = null

        override fun logError(tag: String?, message: String?) { Log.e(tag, message ?: "") }
        override fun logWarn(tag: String?, message: String?) { Log.w(tag, message ?: "") }
        override fun logInfo(tag: String?, message: String?) { Log.i(tag, message ?: "") }
        override fun logDebug(tag: String?, message: String?) { Log.d(tag, message ?: "") }
        override fun logVerbose(tag: String?, message: String?) { Log.v(tag, message ?: "") }
        override fun logStackTraceWithMessage(tag: String?, message: String?, e: Exception?) {
            Log.e(tag, message ?: "", e)
        }
        override fun logStackTrace(tag: String?, e: Exception?) { Log.e(tag, "", e) }
    }
}
