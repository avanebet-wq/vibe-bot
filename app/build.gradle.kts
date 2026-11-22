plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "app.claudepocket"
    compileSdk = 34

    defaultConfig {
        applicationId = "app.claudepocket"
        minSdk = 24
        // targetSdk 28 on purpose (same as Termux): apps targeting 29+ are not allowed to
        // execute files from their own data directory, which a Linux userland needs.
        targetSdk = 28
        versionCode = 20
        versionName = "1.19"
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
    kotlinOptions { jvmTarget = "17" }

    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
}
