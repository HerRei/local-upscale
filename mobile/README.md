# LocalSR mobile

LocalSR is an on-device 4x photo upscaler for iPhone and Android. It bundles two models: Best (PULSE-48 v1) and Fast (SPAN-28 v1). A per-device check prefers the NPU, with GPU or CPU fallback. The app does not use the network.

## Layout

- `Sources/LocalSRCore/`: shared logic.
- `Sources/LocalSR/`: app module, including `UI/`, `Service/`, `Apple/` (`#if !SKIP`), `Android/` (`#if SKIP`), and hand-written Kotlin hot-path code in `Skip/*.kt`.
- `Darwin/`: Xcode project.
- `Android/`: Gradle project.
- `Models/`: bundled models, probe image and notice.
- `tools/export_models.py`: model export and checks.

## Setup

Install Xcode 27 or later and Skip with `brew install skip`. Install the Android SDK with `skip android home install`.

In every shell, run:

```sh
. ~/"Resources/Research notes/LocalSR mobile engine/android_env.sh"
```

This path is specific to the maintainer's Mac. Recreate the file for your machine: it sets `JAVA_HOME` to openjdk@21, sets `ANDROID_HOME`, and puts cmdline-tools 19.0 first on `PATH`. Skip 1.9.21 rejects the newer "Android CLI" cmdline-tools versioning.

## Build, run and test

Run these commands from `mobile/`. Open `Project.xcworkspace` in Xcode to build and run the app. Start an Android emulator or connect a device for Android tests.

```sh
swift test --filter LocalSRCoreTests
swift test --filter 'Apple|LocalSRTests'
gradle -p Android :app:testDebugUnitTest
gradle -p Android :app:connectedDebugAndroidTest
xcodebuild test -workspace Project.xcworkspace -scheme "LocalSR App" -destination 'platform=iOS Simulator,name=iPhone 18 Pro' SKIP_ACTION=none CODE_SIGNING_ALLOWED=NO
```

The connected Android test uses the `localsr_api36` emulator and takes about 10 minutes. APKs are at `.build/Android/app/outputs/apk/{debug,release}/`.

Run `tools/export_models.py` to export models and `tools/export_models.py --check` to check them. See [tools/README.md](tools/README.md) for the required interpreter and dependencies.

## Known limits

- Inputs up to 3 MP produce 4× output. Larger inputs produce 2× output after a 12 MP input cap, keeping output at or below 48 MP. An optional Full 4× mode requires sufficient memory and can produce up to 192 MP.
- The simulator has no NPU. Android NPU use needs vendor runtimes that are not bundled; the check falls back to GPU or CPU.
- Android output is sRGB JPEG without an HDR gain map. The Android photo picker may strip GPS data.
- Completed results are restored after process death, but the pending batch queue is not.
- Measured on real iPhones: both models run on the Neural Engine. A 12 MP photo at Full 4× takes about 1.8 s on an iPhone 17 Pro and 6.6 s on an iPhone 11 Pro. Android has been tested on emulators only.

## Release

- The version and build number are in `Skip.env` (`MARKETING_VERSION`, `CURRENT_PROJECT_VERSION`). Raise the build number for every upload.
- iOS 1.0.0 is iPhone only, with iOS 27 as the minimum. iPad support can't be removed again after it ships, so it comes in a later version.
- The tablet tier (the iPad and Android tablet layout and the larger tablet models in `Models/ios-tablet` and `Models/android-tablet`) is a paused idea: the code stays, but 1.0 builds don't bundle it. On Android, `-PlocalsrTablet=true` bundles the tablet models again; without them a tablet gets the phone layout.
- Store texts, the privacy answers ("Data Not Collected"), the age rating and the review notes are in `Darwin/fastlane/metadata/`; Google Play texts are in `Android/fastlane/metadata/`.
- To archive: open `Project.xcworkspace`, pick the scheme "LocalSR App" and "Any iOS Device", then Product → Archive. For a command-line Release build, set `SKIP_ACTION=none`, or Skip also tries to build and launch the Android app.
