import java.util.Properties

plugins {
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.android.application)
    id("skip-build-plugin")
}

skip {
}

val modelsDir = projectDir.resolve("../../Models")
val stageModels = tasks.register<Sync>("stageLocalsrModels") {
    from(modelsDir.resolve("android")) { include("*.tflite") }
    from(modelsDir.resolve("android-tablet")) { include("*.tflite"); into("tablet") }
    from(modelsDir) { include("probe.png", "NOTICE.txt") }
    into(layout.buildDirectory.dir("localsr-assets"))
}
// Everything that reads the assets dir (packaging, lint) waits for the staging task.
tasks.matching { it.name != "stageLocalsrModels" && (it.name == "preBuild" || it.name.contains("Assets") || it.name.contains("lint", ignoreCase = true)) }.configureEach {
    dependsOn(stageModels)
}
// A release must include the tablet models; Models/android-tablet is git-ignored, so a fresh clone
// would otherwise build an APK without them. Debug builds stay possible without the models.
val checkTabletModels = tasks.register("checkTabletModels") {
    val tabletDir = modelsDir.resolve("android-tablet")
    val expected = listOf("fast", "best", "nomos_realplksr", "denoise_realplksr", "fbcnn", "anime_esrgan", "nomos_span", "span48")
        .map { "$it.tflite" }
    doLast {
        val missing = expected.filter { !tabletDir.resolve(it).isFile || tabletDir.resolve(it).length() == 0L }
        if (missing.isNotEmpty()) {
            throw GradleException(
                "mobile/Models/android-tablet is missing or incomplete (missing: ${missing.joinToString()}). " +
                    "A release build must include the tablet models; see mobile/tools/TABLET-MODELS.md to convert them."
            )
        }
    }
}
tasks.matching { it.name == "preReleaseBuild" }.configureEach {
    dependsOn(checkTabletModels)
}

dependencies {
    // UpscaleForegroundService / MediaStore / LiteRT are in the LocalSR module (Sources/LocalSR/Skip/*.kt)
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.11.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
    androidTestImplementation("androidx.test:rules:1.7.0")
    androidTestImplementation("androidx.test.uiautomator:uiautomator:2.3.0")
    androidTestImplementation("androidx.test:core:1.7.0")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.exifinterface:exifinterface:1.4.2")
    androidTestImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.fromTarget(libs.versions.jvm.get().toString())
    }
}

android {
    namespace = group as String
    compileSdk = libs.versions.android.sdk.compile.get().toInt()
    compileOptions {
        sourceCompatibility = JavaVersion.toVersion(libs.versions.jvm.get())
        targetCompatibility = JavaVersion.toVersion(libs.versions.jvm.get())
    }
    packaging {
        jniLibs {
            keepDebugSymbols.add("**/*.so")
            pickFirsts.add("**/*.so")
            // this option would compress JNI .so files and reduce overall size for Skip Fuse apps, but cost more at install time
            //useLegacyPackaging = true
        }
    }

    defaultConfig {
        // Android 10+: MediaStore RELATIVE_PATH / IS_PENDING (save to Pictures/LocalSR without a storage permission).
        minSdk = maxOf(29, libs.versions.android.sdk.min.get().toInt())
        targetSdk = libs.versions.android.sdk.compile.get().toInt()
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // 64-bit ARM only: every phone LiteRT's GPU/NPU paths target, and the emulator image is arm64.
        ndk { abiFilters += listOf("arm64-v8a") }
        // skip.tools.skip-build-plugin will automatically use Skip.env properties for:
        // applicationId = ANDROID_APPLICATION_ID ?? PRODUCT_BUNDLE_IDENTIFIER
        // versionCode = CURRENT_PROJECT_VERSION
        // versionName = MARKETING_VERSION
    }

    buildFeatures {
        buildConfig = true
    }

    // The LiteRT models and the device-check probe live in Models/ (shared with the iOS side, single copy in git).
    // They ship only in the APK: stage the Android ones as assets at build time; Models/ios is never included.
    sourceSets.getByName("main").assets.srcDir(layout.buildDirectory.dir("localsr-assets").get().asFile)

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
    // The device tests run against debug by default. -PtestBuildType=release runs them against the
    // minified release build (R8): gradle -p Android :app:connectedReleaseAndroidTest -PtestBuildType=release
    testBuildType = (findProperty("testBuildType") as String?) ?: "debug"

    lint {
        disable.add("Instantiatable")
        disable.add("MissingPermission")
    }

    dependenciesInfo {
        // Disables dependency metadata when building APKs.
        includeInApk = false
        // Disables dependency metadata when building Android App Bundles.
        includeInBundle = false
    }

    // default signing configuration tries to load from keystore.properties
    // see: https://skip.dev/docs/deployment/#export-signing
    signingConfigs {
        val keystorePropertiesFile = file("keystore.properties")
        create("release") {
            if (keystorePropertiesFile.isFile) {
                val keystoreProperties = Properties()
                keystoreProperties.load(keystorePropertiesFile.inputStream())
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
                storeFile = file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
            } else {
                // when there is no keystore.properties file, fall back to signing with debug config
                keyAlias = signingConfigs.getByName("debug").keyAlias
                keyPassword = signingConfigs.getByName("debug").keyPassword
                storeFile = signingConfigs.getByName("debug").storeFile
                storePassword = signingConfigs.getByName("debug").storePassword
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("release")
            isMinifyEnabled = true
            isShrinkResources = true
            isDebuggable = false // can be set to true for debugging release build, but needs to be false when uploading to store
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
}
