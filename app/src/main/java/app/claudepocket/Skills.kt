package app.claudepocket

import android.content.Context
import java.io.File

/** Навыки Claude: чтение списка из ~/.claude/skills, установка из GitHub, удаление. */
object Skills {

    class Skill(val dir: String, val name: String, val description: String)

    class Result(val installed: List<String>, val error: String?)

    private fun skillsDir(ctx: Context) = File(Paths(ctx).rootfs, "root/.claude/skills")

    fun list(ctx: Context): List<Skill> {
        val base = skillsDir(ctx)
        val dirs = base.listFiles { f -> f.isDirectory } ?: return emptyList()
        return dirs.sortedBy { it.name.lowercase() }.map { d ->
            val (name, desc) = readHeader(File(d, "SKILL.md"), d.name)
            Skill(d.name, name, desc)
        }
    }

    /** Берёт name и description из шапки SKILL.md между строками «---». */
    private fun readHeader(f: File, fallback: String): Pair<String, String> {
        var name = fallback
        var desc = ""
        try {
            val lines = f.readLines()
            if (lines.firstOrNull()?.trim() == "---") {
                var i = 1
                while (i < lines.size && lines[i].trim() != "---") {
                    val l = lines[i]
                    when {
                        l.startsWith("name:") -> name = clean(l.removePrefix("name:")).ifEmpty { fallback }
                        l.startsWith("description:") -> {
                            desc = clean(l.removePrefix("description:"))
                            // многострочное описание (> или |): склеиваем отступные строки
                            if (desc == ">" || desc == "|" || desc.isEmpty()) {
                                val sb = StringBuilder()
                                while (i + 1 < lines.size && (lines[i + 1].startsWith(" ") || lines[i + 1].startsWith("\t"))) {
                                    sb.append(lines[++i].trim()).append(' ')
                                }
                                desc = sb.toString().trim()
                            }
                        }
                    }
                    i++
                }
            }
        } catch (_: Exception) {
        }
        return name to desc
    }

    private fun clean(s: String) = s.trim().trim('"', '\'')

    fun remove(ctx: Context, dir: String): Boolean {
        if (!dir.matches(Regex("[A-Za-z0-9._-]+")) || dir == "." || dir == "..") return false
        val target = File(skillsDir(ctx), dir)
        if (!target.exists()) return true
        run("/system/bin/rm", "-rf", target.path)
        return !target.exists()
    }

    /** Ссылка на GitHub → (url, ветка, подпапка, имя репозитория) или null. */
    class Source(val url: String, val branch: String, val sub: String, val repo: String)

    fun parse(input: String): Source? {
        val s = input.trim().removeSuffix("/")
        val m = Regex("^(?:https?://)?(?:www\\.)?github\\.com/([\\w.-]+)/([\\w.-]+?)(?:\\.git)?(?:/tree/([\\w.-]+)(?:/([\\w./-]+))?)?$").matchEntire(s)
            ?: Regex("^([\\w.-]+)/([\\w.-]+)$").matchEntire(s)?.let { return Source("https://github.com/${it.groupValues[1]}/${it.groupValues[2]}", "", "", it.groupValues[2]) }
            ?: return null
        val owner = m.groupValues[1]
        val repo = m.groupValues[2]
        return Source("https://github.com/$owner/$repo", m.groupValues[3], m.groupValues[4], repo)
    }

    fun install(ctx: Context, src: Source, onLine: (String) -> Unit): Result {
        val paths = Paths(ctx)
        val script = File(paths.rootfs, "root/.install_skill.sh")
        script.delete()
        script.writeBytes(ctx.assets.open("install_skill.sh").use { it.readBytes() })
        script.setExecutable(true, false)
        // аргументы передаются отдельными элементами, а не строкой для оболочки: подставить команду нельзя
        val cmd = listOf("/bin/bash", "/root/.install_skill.sh", src.url, src.branch, src.sub, src.repo)
        val pb = ProcessBuilder(Proot.argv(paths, cmd, false))
        pb.environment().apply {
            clear()
            putAll(Proot.hostEnv(paths, paths.noSeccomp))
        }
        pb.directory(paths.home)
        pb.redirectErrorStream(true)
        val installed = ArrayList<String>()
        var err: String? = null
        val tail = ArrayDeque<String>()
        return try {
            val p = pb.start()
            p.outputStream.close()
            p.inputStream.bufferedReader().forEachLine { l ->
                when {
                    l.startsWith("SKILL:") -> installed.add(l.removePrefix("SKILL:"))
                    l.startsWith("ERR:") -> err = l.removePrefix("ERR:")
                    else -> { onLine(l); tail.addLast(l); while (tail.size > 4) tail.removeFirst() }
                }
            }
            val code = p.waitFor()
            if (code != 0 && err == null) err = "Не удалось скачать репозиторий. " + tail.joinToString(" ").take(200)
            Result(installed, if (installed.isEmpty()) err ?: "Навыки не найдены" else null)
        } catch (t: Throwable) {
            Result(emptyList(), t.message ?: t.toString())
        }
    }

    private fun run(vararg cmd: String) {
        try {
            val p = ProcessBuilder(*cmd).redirectErrorStream(true).start()
            p.inputStream.readBytes()
            p.waitFor()
        } catch (_: Exception) {
        }
    }
}
