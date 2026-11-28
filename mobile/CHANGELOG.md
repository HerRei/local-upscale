# Changelog (mobile app)

The desktop app has its own changelog in the repository root.

## [1.0.1] - unreleased

- **App Store listing only.** New name, subtitle and keywords so the app shows up for searches
  like "AI image upscaler", and a German store page ("Bilder hochskalieren"). The app is
  unchanged; this build exists because the App Store only takes new texts with a new version.

## [1.0.0] - 2026-11-22

- **LocalSR for iPhone.** Upscales photos 4x on the phone itself, on iOS 27 or later.
  Photos never leave the device, there is no account and the app has no network access.
- **LocalSR for Android phones.** The same app as a signed APK for Android 10 or later,
  outside the Play Store. Tablets get the phone layout and models for now.
- **Two bundled models.** Best and Fast, chosen per device, run on the Neural Engine with
  GPU or CPU as fallback.
- **Before and after.** Results open with a comparison slider and pinch to zoom, and save to
  Photos with add-only access.
- **Share extension.** Share a photo from Photos with "Upscale with LocalSR".
- **Full 4x.** An optional setting upscales 12 MP photos to 48 MP on phones with enough memory.
