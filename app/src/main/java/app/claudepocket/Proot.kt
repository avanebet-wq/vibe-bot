package app.claudepocket

import java.io.File

/** Сборка командной строки и окружения для proot (Linux без root-прав). */
object Proot {
    private const val GUEST_PATH =
        "/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    private val FAKE_PROC = mapOf(
        "loadavg" to "0.12 0.07 0.02 2/165 765\n",
        "uptime" to "284684.56 513853.46\n",
        "version" to "Linux version 6.2.1-claude-pocket (proot@localhost) (gcc 13.2.0) #1 SMP PREEMPT\n",
        "vmstat" to "nr_free_pages 1743136\nnr_zone_inactive_anon 179281\nnr_zone_active_anon 7183\n" +
            "nr_zone_inactive_file 1167425\nnr_zone_active_file 1138330\nnr_zone_unevictable 0\n",
        "stat" to "cpu  1050008 127632 898432 43828767 37203 63 99244 0 0 0\n" +
            "cpu0 212383 20476 204704 8389202 7253 42 12597 0 0 0\n" +
            "cpu1 224452 24947 215570 8372502 8135 4 42768 0 0 0\n" +
            "cpu2 222993 17440 200925 8424262 8069 9 17732 0 0 0\n" +
            "cpu3 186835 8775 195974 8486330 5746 3 8360 0 0 0\n" +
            "intr 53261351 0 686 1 0 0 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n" +
            "ctxt 38014093\nbtime 1694292441\nprocesses 26442\nprocs_running 1\nprocs_blocked 0\n" +
            "softirq 75663 0 5903 6 25375 10774 0 243 11685 0 21677\n",
    )

    /** Android закрывает часть /proc — подсовываем обычные файлы (так делает и proot-distro). */
    fun ensureFakeProc(p: Paths) {
        p.fakeProc.mkdirs()
        FAKE_PROC.forEach { (name, content) ->
            val f = File(p.fakeProc, name)
            if (!f.exists()) f.writeText(content)
        }
    }

    /** argv[0] — путь к proot. [guestCmd] выполняется внутри Ubuntu. */
    fun argv(p: Paths, guestCmd: List<String>, sdcard: Boolean): List<String> {
        val a = mutableListOf(
            p.proot.path,
            "-r", p.rootfs.path,
            "-0", "--link2symlink", "--kill-on-exit",
            "-b", "/dev", "-b", "/proc", "-b", "/sys",
            "-b", "/proc/self/fd:/dev/fd",
            "-b", "/proc/self/fd/0:/dev/stdin",
            "-b", "/proc/self/fd/1:/dev/stdout",
            "-b", "/proc/self/fd/2:/dev/stderr",
            "-b", "${p.shm.path}:/dev/shm",
        )
        FAKE_PROC.keys.forEach { name ->
            a += listOf("-b", "${File(p.fakeProc, name).path}:/proc/$name")
        }
        if (sdcard) a += listOf("-b", "/storage/emulated/0:/sdcard")
        a += listOf(
            "-w", "/root",
            "/usr/bin/env", "-i",
            "HOME=/root", "USER=root", "LOGNAME=root",
            "PATH=$GUEST_PATH",
            "TERM=xterm-256color", "COLORTERM=truecolor",
            "LANG=C.UTF-8", "SHELL=/bin/bash", "TMPDIR=/tmp",
        )
        a += guestCmd
        return a
    }

    /** Окружение самого процесса proot (на стороне Android). */
    fun hostEnv(p: Paths, noSeccomp: Boolean): Map<String, String> {
        val e = HashMap<String, String>(System.getenv())
        // proot зависит от libtalloc.so / libandroid-shmem.so, лежащих рядом с ним
        e["LD_LIBRARY_PATH"] = p.proot.parent ?: ""
        e.remove("LD_PRELOAD")
        e["PROOT_TMP_DIR"] = p.tmp.path
        e["PROOT_LOADER"] = p.loader.path
        e["PROOT_IGNORE_MISSING_BINDINGS"] = "1"
        e["TMPDIR"] = p.tmp.path
        if (noSeccomp) e["PROOT_NO_SECCOMP"] = "1"
        return e
    }

    fun hostEnvArray(p: Paths, noSeccomp: Boolean): Array<String> =
        hostEnv(p, noSeccomp).map { "${it.key}=${it.value}" }.toTypedArray()
}
