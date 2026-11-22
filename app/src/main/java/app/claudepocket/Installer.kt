package app.claudepocket

import android.content.Context
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * Установка «одной кнопкой»:
 * 1) скачать Ubuntu (arm64) и проверить контрольную сумму,
 * 2) распаковать,
 * 3) настроить,
 * 4) внутри Ubuntu поставить инструменты и Claude Code (assets/setup.sh).
 *
 * Все шаги можно безопасно запускать повторно — то, что уже готово, пропускается.
 */
class Installer(private val ctx: Context, private val ui: Ui) {

    interface Ui {
        fun log(line: String)

        /** [percent] = -1 — неопределённый прогресс. */
        fun step(title: String, percent: Int)

        /** [error] = null — всё прошло успешно. */
        fun finished(error: String?)
    }

    private val p = Paths(ctx)

    fun start() {
        Thread({
            try {
                run()
                ui.finished(null)
            } catch (t: Throwable) {
                ui.finished(t.message ?: t.toString())
            }
        }, "installer").start()
    }

    private fun run() {
        p.ensureDirs()
        if (!p.rootfsReady.exists()) {
            prepareRootfs()
        } else {
            ui.log("Ubuntu уже скачана — пропускаю этот шаг")
        }
        configureRootfs()
        selfTest()
        runSetupScript()
        verify()
        p.installed.writeText("1")
    }

    // ---------- 1–2. Ubuntu ----------

    private fun prepareRootfs() {
        wipeRootfs(p)
        p.rootfs.mkdirs()

        ui.step("Ищу свежий образ Ubuntu…", -1)
        val (name, sha) = findTarball()
        val tar = File(p.downloads, name)
        download("$UBUNTU_BASE$name", tar, sha)

        ui.step("Распаковываю систему…", -1)
        extract(tar)
        tar.delete()
        p.rootfsReady.writeText(name)
    }

    private fun findTarball(): Pair<String, String> {
        val sums = httpText(UBUNTU_BASE + "SHA256SUMS")
        val re = Regex("""^([0-9a-f]{64}) \*(ubuntu-base-(\d+(?:\.\d+)*)-base-arm64\.tar\.gz)$""", RegexOption.MULTILINE)
        val best = re.findAll(sums).maxWithOrNull { a, b ->
            compareVersions(a.groupValues[3], b.groupValues[3])
        } ?: throw IOException("Не нашёл образ Ubuntu arm64 на сервере")
        ui.log("Образ: ${best.groupValues[2]}")
        return best.groupValues[2] to best.groupValues[1]
    }

    private fun compareVersions(a: String, b: String): Int {
        val x = a.split('.').map { it.toInt() }
        val y = b.split('.').map { it.toInt() }
        for (i in 0 until maxOf(x.size, y.size)) {
            val c = (x.getOrElse(i) { 0 }).compareTo(y.getOrElse(i) { 0 })
            if (c != 0) return c
        }
        return 0
    }

    private fun open(url: String): HttpURLConnection {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 20_000
        c.readTimeout = 30_000
        c.instanceFollowRedirects = true
        c.setRequestProperty("User-Agent", "claude-pocket")
        return c
    }

    private fun httpText(url: String): String {
        val c = open(url)
        if (c.responseCode != 200) throw IOException("Сервер ответил ${c.responseCode}: $url")
        return c.inputStream.bufferedReader().use { it.readText() }
    }

    private fun download(url: String, dest: File, expectedSha: String) {
        val c = open(url)
        if (c.responseCode != 200) throw IOException("Сервер ответил ${c.responseCode}: $url")
        val total = c.contentLengthLong
        val md = MessageDigest.getInstance("SHA-256")
        var done = 0L
        var lastPct = -1
        c.inputStream.use { input ->
            dest.outputStream().use { out ->
                val buf = ByteArray(64 * 1024)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    out.write(buf, 0, n)
                    md.update(buf, 0, n)
                    done += n
                    if (total > 0) {
                        val pct = (done * 100 / total).toInt()
                        if (pct != lastPct) {
                            lastPct = pct
                            ui.step("Скачиваю Ubuntu… ${done shr 20} из ${total shr 20} МБ", pct)
                        }
                    }
                }
            }
        }
        val sha = md.digest().joinToString("") { "%02x".format(it) }
        if (!sha.equals(expectedSha, ignoreCase = true)) {
            dest.delete()
            throw IOException("Контрольная сумма образа не совпала — повтори установку")
        }
        ui.log("Контрольная сумма совпала")
    }

    private fun extract(tar: File) {
        val out = StringBuilder()
        val rc = execLines(
            ProcessBuilder("/system/bin/tar", "-xzf", tar.path, "-C", p.rootfs.path),
        ) { out.appendLine(it) }
        if (!File(p.rootfs, "usr/bin/bash").exists()) {
            throw IOException("Не удалось распаковать систему (код $rc): ${out.takeLast(300)}")
        }
        if (rc != 0) ui.log("tar вернул код $rc, но система на месте: ${out.takeLast(200)}")
        // как обычный пользователь мы должны иметь право писать везде внутри системы
        execLines(ProcessBuilder("/system/bin/chmod", "-R", "u+rwX", p.rootfs.path)) { }
    }

    // ---------- 3. Настройка ----------

    private fun configureRootfs() {
        ui.step("Настраиваю систему…", -1)
        val r = p.rootfs
        File(r, "tmp").mkdirs()
        File(r, "root/work").mkdirs()
        File(r, "sdcard").mkdirs()

        // сеть
        File(r, "etc/resolv.conf").delete()
        File(r, "etc/resolv.conf").writeText("nameserver 1.1.1.1\nnameserver 8.8.8.8\n")
        File(r, "etc/hosts").delete()
        File(r, "etc/hosts").writeText("127.0.0.1 localhost\n::1 localhost ip6-localhost\n")

        // apt без отдельного пользователя _apt (в proot его не поменять)
        File(r, "etc/apt/apt.conf.d").mkdirs()
        File(r, "etc/apt/apt.conf.d/99claude-pocket").writeText(
            "APT::Sandbox::User \"root\";\nAcquire::Retries \"3\";\nDpkg::Use-Pty \"0\";\n" +
                "APT::Install-Recommends \"false\";\n",
        )
        File(r, "etc/dpkg/dpkg.cfg.d").mkdirs()
        File(r, "etc/dpkg/dpkg.cfg.d/99claude-pocket").writeText("force-unsafe-io\n")

        // не пытаться запускать системные службы при установке пакетов
        File(r, "usr/sbin").mkdirs()
        File(r, "usr/sbin/policy-rc.d").apply {
            delete()
            writeText("#!/bin/sh\nexit 101\n")
            setExecutable(true, false)
        }

        // скрипты из assets
        writeAsset("setup.sh", File(r, "root/.setup.sh"))
        writeAsset("launch.sh", File(r, "root/.launch.sh"))

        val bashrc = File(r, "root/.bashrc")
        val marker = "# --- claude-pocket ---"
        if (!bashrc.exists() || !bashrc.readText().contains(marker)) {
            bashrc.appendText(
                "\n$marker\n" +
                    "export PATH=\"\$HOME/.local/bin:\$PATH\"\n" +
                    "export LANG=C.UTF-8\n" +
                    "alias ll='ls -la'\nalias cc='claude'\n" +
                    "PS1='\\[\\e[38;5;173m\\]pocket\\[\\e[0m\\]:\\[\\e[34m\\]\\w\\[\\e[0m\\]\\$ '\n",
            )
        }
        Proot.ensureFakeProc(p)
    }

    private fun writeAsset(name: String, dest: File) {
        val bytes = ctx.assets.open(name).use { it.readBytes() }
        dest.delete()
        dest.writeBytes(bytes)
        dest.setExecutable(true, false)
    }

    // ---------- 4. Проверка proot и установка программ ----------

    private fun runGuest(cmd: List<String>, onLine: (String) -> Unit): Int {
        val pb = ProcessBuilder(Proot.argv(p, cmd, false))
        pb.environment().apply {
            clear()
            putAll(Proot.hostEnv(p, p.noSeccomp))
        }
        pb.directory(p.home)
        return execLines(pb, onLine)
    }

    private fun selfTest() {
        ui.step("Проверяю, что Linux запускается на этом телефоне…", -1)
        var out = StringBuilder()
        var rc = runGuest(listOf("/bin/sh", "-c", "echo proot-ok")) { out.appendLine(it) }
        if (rc != 0 || !out.contains("proot-ok")) {
            ui.log("Обычный запуск не удался (${out.toString().trim().takeLast(200)}). Пробую режим совместимости…")
            p.noSeccomp = true
            out = StringBuilder()
            rc = runGuest(listOf("/bin/sh", "-c", "echo proot-ok")) { out.appendLine(it) }
            if (rc != 0 || !out.contains("proot-ok")) {
                throw IOException("Linux не запускается на этом телефоне (код $rc): ${out.toString().trim().takeLast(300)}")
            }
        }
        ui.log("Linux запускается" + if (p.noSeccomp) " (режим совместимости)" else "")
    }

    private fun runSetupScript() {
        ui.step("Ставлю программы и Claude Code — это самый долгий шаг…", -1)
        val rc = runGuest(listOf("/bin/bash", "/root/.setup.sh")) { ui.log(it) }
        if (rc != 0) throw IOException("Установка завершилась с ошибкой (код $rc). Подробности — в журнале выше.")
    }

    private fun verify() {
        ui.step("Проверяю Claude Code…", -1)
        val out = StringBuilder()
        val rc = runGuest(listOf("/bin/bash", "-lc", "/root/.local/bin/claude --version")) { out.appendLine(it) }
        if (rc != 0) throw IOException("Claude Code не запускается (код $rc): ${out.toString().trim().takeLast(300)}")
        ui.log("Claude Code: ${out.toString().trim().lineSequence().lastOrNull().orEmpty()}")
    }

    companion object {
        private const val UBUNTU_BASE = "https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/"

        /** Стирает установленную систему (для «Переустановить»). Не следует по символьным ссылкам. */
        fun wipeRootfs(p: Paths) {
            if (p.rootfs.exists()) {
                execLines(ProcessBuilder("/system/bin/chmod", "-R", "u+rwX", p.rootfs.path)) { }
                execLines(ProcessBuilder("/system/bin/rm", "-rf", p.rootfs.path)) { }
            }
            p.rootfsReady.delete()
            p.installed.delete()
        }

        private fun execLines(pb: ProcessBuilder, onLine: (String) -> Unit): Int {
            pb.redirectErrorStream(true)
            val proc = pb.start()
            proc.outputStream.close()
            proc.inputStream.bufferedReader().forEachLine(onLine)
            return proc.waitFor()
        }
    }
}
