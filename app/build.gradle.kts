plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "app.claudepocket"
    compileSdk = 36

    defaultConfig {
        applicationId = "app.claudepocket"
        minSdk = 24
        // targetSdk 28 on purpose (same as Termux): apps targeting 29+ are not allowed to
        // execute files from their own data directory, which a Linux userland needs.
        targetSdk = 28
        versionCode = 35
        versionName = "1.34"
        ndk { abiFilters += "arm64-v8a" }
    }

    signingConfigs {
        // Fixed key committed to the repo so that every CI build is signed identically and a new
        // APK installs over the previous one without uninstalling (personal sideload use only).
        create("fixed") {
            storeFile = file("debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }

    buildTypes {
        debug { signingConfig = signingConfigs.getByName("fixed") }
        release {
            signingConfig = signingConfigs.getByName("fixed")
            isMinifyEnabled = false
        }
    }

    packaging {
        // proot is shipped as libproot.so and must be extracted to nativeLibraryDir to be executable.
        jniLibs {
            useLegacyPackaging = true
            keepDebugSymbols += "**/*.so"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}

kotlin {
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.compose.ui:ui:1.10.3")
    implementation("androidx.compose.foundation:foundation:1.10.3")
    implementation("io.github.kyant0:backdrop:1.0.6")
}
