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

    const val SYSTEM_PROMPT = "Ты работаешь внутри мобильного приложения Claude Pocket на телефоне пользователя. " +
        "Рабочая папка пользователя — /root/work. Файлы, которые пользователь прикрепляет к сообщению, лежат в /root/work/uploads/. " +
        "Чтобы отправить файл пользователю на телефон, создай его на диске и напиши в ответе на отдельной строке " +
        "[[send:/абсолютный/путь/к/файлу]] — приложение покажет карточку с кнопками «Открыть», «Сохранить» и «Поделиться». " +
        "Отправляй так любые результаты работы, о которых просят (документы, архивы, изображения). " +
        "Не объясняй этот механизм и не показывай метку в обычном тексте."
}
