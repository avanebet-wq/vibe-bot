package app.claudepocket

import android.app.Activity
import android.view.View
import android.view.inputmethod.InputMethodManager
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.platform.ViewCompositionStrategy
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue

/** Экран чата на Compose: переписка, стеклянные кнопки и поле ввода. Состояние берёт у [ChatEngine]. */
class ChatController(
    private val act: Activity,
    private val root: View,
    private val onMenu: (View) -> Unit,
    private val onModel: () -> Unit,
    private val onPlus: (View) -> Unit,
    private val onFile: (ChatEngine.Msg.File, String) -> Unit,
) : ChatEngine.Listener {

    private val ui = ChatUi()
    private val compose: ComposeView = root.findViewById(R.id.chat_compose)
    private val menuAnchor: View = root.findViewById(R.id.menu_anchor)
    private val plusAnchor: View = root.findViewById(R.id.plus_anchor)

    var onModelsChanged: (() -> Unit)? = null

    init {
        compose.setViewCompositionStrategy(ViewCompositionStrategy.DisposeOnDetachedFromWindowOrReleasedFromPool)
        compose.setContent {
            ChatScreen(
                ui = ui,
                act = act,
                onMenu = { onMenu(menuAnchor) },
                onModel = onModel,
                onPlus = { onPlus(plusAnchor) },
                onFile = onFile,
                onSend = ::send,
            )
        }
    }

    private fun send() {
        if (ChatEngine.busy) return
        val text = ui.draft.text.trim()
        if (text.isNotEmpty() || ui.attachments.isNotEmpty()) {
            val files = ArrayList(ui.attachments)
            ui.attachments.clear()
            ui.draft = TextFieldValue("")
            ChatEngine.send(act, text, files)
        }
    }

    /** Подставляет текст в поле ввода (например, заготовку запроса). */
    fun prefill(text: String) {
        ui.draft = TextFieldValue(text, TextRange(text.length))
        ui.focusTick++
    }

    /** Подключает контроллер к движку и показывает накопленную переписку. */
    fun attach() {
        ChatEngine.listener = this
        ui.busy = ChatEngine.busy
        ui.tick++
        ui.forceScroll++
    }

    fun detach() {
        if (ChatEngine.listener === this) ChatEngine.listener = null
    }

    fun addAttachment(a: Files.Attachment) {
        ui.attachments.add(a)
    }

    fun hideKeyboard() {
        ui.blurTick++
        val imm = act.getSystemService(android.content.Context.INPUT_METHOD_SERVICE) as InputMethodManager
        imm.hideSoftInputFromWindow(root.windowToken, 0)
    }

    // ---- ChatEngine.Listener

    override fun added(m: ChatEngine.Msg) {
        ui.tick++
        if (m is ChatEngine.Msg.User) ui.forceScroll++
    }

    override fun changed(m: ChatEngine.Msg) {
        ui.tick++
    }

    override fun busyChanged(busy: Boolean) {
        ui.busy = busy
        ui.tick++
    }

    override fun cleared() {
        ui.tick++
        ui.forceScroll++
    }

    override fun removed(m: ChatEngine.Msg) {
        ui.tick++
    }

    override fun modelsChanged() {
        onModelsChanged?.invoke()
    }
}
