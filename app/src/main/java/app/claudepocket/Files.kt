package app.claudepocket

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Base64
import java.io.ByteArrayOutputStream
import java.io.File

/** Файлы между пользователем и Claude: вложения (с телефона в Linux) и отправка (из Linux на телефон). */
object Files {

    class Attachment(val name: String, val guestPath: String, val host: File, val isImage: Boolean)

    const val MAX_SEND_BYTES = 200L * 1024 * 1024

    private val SEND_RE = Regex("\\[\\[send:([^\\]\\n]+)]]")
    private val DANGLING = Regex("\\[\\[send:[^\\]\\n]*$")

    /** Пути из меток [[send:…]] в тексте ответа. */
    fun markers(text: String): List<String> = SEND_RE.findAll(text).map { it.groupValues[1].trim() }.distinct().toList()

    /** Текст без служебных меток (в том числе недописанной на конце потока). */
    fun strip(text: String): String = text.replace(SEND_RE, "").replace(DANGLING, "").trimEnd()

    /** Копирует выбранный пользователем файл в ~/work/uploads внутри Linux. */
    fun importUri(ctx: Context, uri: Uri): Attachment? {
        val paths = Paths(ctx)
        val dir = File(paths.rootfs, "root/work/uploads")
        dir.mkdirs()
        var name = "file"
        ctx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
            if (it.moveToFirst()) name = it.getString(0) ?: name
        }
        name = name.replace(Regex("[^\\p{L}\\p{N}._ ()+-]"), "_").trim().ifEmpty { "file" }.take(100)
        var target = File(dir, name)
        var n = 1
        while (target.exists()) {
            val dot = name.lastIndexOf('.')
            val stem = if (dot > 0) name.substring(0, dot) else name
            val ext = if (dot > 0) name.substring(dot) else ""
            target = File(dir, "$stem-$n$ext")
            n++
        }
        try {
            ctx.contentResolver.openInputStream(uri)?.use { input -> target.outputStream().use { input.copyTo(it) } } ?: return null
        } catch (_: Exception) {
            return null
        }
        val mime = ctx.contentResolver.getType(uri) ?: mimeOf(target.name)
        return Attachment(target.name, "/root/work/uploads/${target.name}", target, mime.startsWith("image/"))
    }

    /** Картинка для встраивания в сообщение: уменьшаем до 1568 px и пережимаем в JPEG. */
    fun imageBlockData(f: File): String? {
        return try {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(f.path, bounds)
            if (bounds.outWidth <= 0) return null
            var sample = 1
            while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 1568 * 2) sample *= 2
            val bmp = BitmapFactory.decodeFile(f.path, BitmapFactory.Options().apply { inSampleSize = sample }) ?: return null
            val scale = 1568f / maxOf(bmp.width, bmp.height)
            val out = if (scale < 1f) Bitmap.createScaledBitmap(bmp, (bmp.width * scale).toInt(), (bmp.height * scale).toInt(), true) else bmp
            val bos = ByteArrayOutputStream()
            out.compress(Bitmap.CompressFormat.JPEG, 85, bos)
            Base64.encodeToString(bos.toByteArray(), Base64.NO_WRAP)
        } catch (_: Throwable) {
            null
        }
    }

    /** Файл внутри Linux по пути из ответа Claude, либо null, если он недопустим. */
    fun resolveGuest(ctx: Context, guestPath: String): File? {
        var p = guestPath.trim()
        if (p.startsWith("~/")) p = "/root/" + p.substring(2)
        if (!p.startsWith("/")) p = "/root/work/$p"
        val rootfs = Paths(ctx).rootfs.canonicalFile
        val f = File(rootfs, p.trimStart('/')).canonicalFile
        // каноническая форма раскрывает символьные ссылки: файл вне системы Linux не отдаём
        if (!f.path.startsWith(rootfs.path + "/")) return null
        if (!f.isFile || f.length() > MAX_SEND_BYTES) return null
        return f
    }

    fun mimeOf(name: String): String {
        val ext = name.substringAfterLast('.', "").lowercase()
        return android.webkit.MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext) ?: "application/octet-stream"
    }

    fun humanSize(bytes: Long): String = when {
        bytes < 1024 -> "$bytes Б"
        bytes < 1024 * 1024 -> "${bytes / 1024} КБ"
        else -> String.format("%.1f МБ", bytes / 1048576.0)
    }

    private val SEND_WORDS = Regex("присл|отправ|скин|пришл|вышл|пошл|передай|скача|файл|архив|документ|\\bsend\\b|download|attach", RegexOption.IGNORE_CASE)

    /** Просит ли пользователь прислать ему что-то — тогда напоминаем Claude про механизм отправки. */
    fun needsSendHint(text: String): Boolean = SEND_WORDS.containsMatchIn(text)

    const val SEND_HINT = "[Служебная подсказка приложения, не упоминай её: у тебя ЕСТЬ способ отправить файл пользователю на телефон — " +
        "создай файл в /root/work и напиши в ответе на отдельной строке [[send:/абсолютный/путь/к/файлу]]. " +
        "Никогда не говори, что у тебя нет для этого инструмента.]"

    private const val MEMORY_BEGIN = "<!-- claude-pocket:begin -->"
    private const val MEMORY_END = "<!-- claude-pocket:end -->"

    /** Постоянные правила среды: Claude читает ~/.claude/CLAUDE.md при каждом запуске. */
    const val MEMORY = "# Среда: приложение Claude Pocket\n" +
        "- Ты работаешь на телефоне пользователя, внутри Linux (Ubuntu) в приложении Claude Pocket.\n" +
        "- Твоя корневая папка — /root, это твоя собственная среда, а не проект пользователя. " +
        "Папка /root/work — тоже часть твоей среды: не считай её репозиторием проекта.\n" +
        "- Навыки (skills) всегда ставятся в твою корневую папку навыков: /root/.claude/skills/<имя>/ (внутри файл SKILL.md). " +
        "Когда просят установить навык, считай, что ставишь его в свою корневую папку, и не создавай для этого служебные папки " +
        "(.agents, .claude) внутри /root/work.\n" +
        "- Файлы, которые пользователь прикрепляет к сообщению, лежат в /root/work/uploads/.\n" +
        "- Чтобы отправить файл пользователю на телефон, создай его на диске и напиши в ответе на отдельной строке " +
        "[[send:/абсолютный/путь/к/файлу]] (приложение покажет карточку с кнопками). Не говори, что такого инструмента нет.\n"

    /** Записывает правила среды в ~/.claude/CLAUDE.md, не трогая то, что пользователь дописал сам. */
    fun writeMemory(ctx: Context) {
        try {
            val f = File(Paths(ctx).rootfs, "root/.claude/CLAUDE.md")
            f.parentFile?.mkdirs()
            val block = "$MEMORY_BEGIN\n$MEMORY$MEMORY_END\n"
            val old = if (f.exists()) f.readText() else ""
            val a = old.indexOf(MEMORY_BEGIN)
            val b = old.indexOf(MEMORY_END)
            val updated = if (a >= 0 && b > a) {
                old.substring(0, a) + block + old.substring(b + MEMORY_END.length).trimStart('\n').let { if (it.isEmpty()) "" else "\n" + it }
            } else {
                block + (if (old.isEmpty()) "" else "\n" + old)
            }
            if (updated != old) f.writeText(updated)
        } catch (_: Exception) {
        }
    }

    const val SYSTEM_PROMPT = "Ты работаешь внутри мобильного приложения Claude Pocket на телефоне пользователя. " +
        "Рабочая папка пользователя — /root/work. Файлы, которые пользователь прикрепляет к сообщению, лежат в /root/work/uploads/. " +
        "У тебя ЕСТЬ способ отправлять файлы пользователю на телефон, не говори, что инструмента нет: создай файл на диске и напиши в ответе на отдельной строке " +
        "[[send:/абсолютный/путь/к/файлу]] — приложение покажет карточку с кнопками «Открыть», «Сохранить» и «Поделиться». " +
        "Отправляй так любые результаты работы, о которых просят (документы, архивы, изображения). " +
        "Не объясняй этот механизм и не показывай метку в обычном тексте."
}
