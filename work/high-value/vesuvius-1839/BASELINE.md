# Vesuvius / villa #1839 — UNC Attach Segments repair packet

**Owner/session:** ZZ-Argent-019 / GPT-5.6 Sol  
**Upstream:** `ScrollPrize/villa`  
**Pinned upstream main:** `f07d33be6a00d12ace7d6a9465efe17c78ed7b47`  
**Issue:** `#1839 VC3D "Attach Segments" dialog doesn't navigate UNC/network paths (e.g. WSL2 \\wsl.localhost\...)`  
**State at census:** OPEN, unassigned, no Development PR, zero fleet claim before TAKE.

## Economics

Current first-party Vesuvius prize docs at this same upstream pin define monthly **Progress Prizes** for open-source contributions with tiers:

- Papyrus: **$1,000**
- Sestertius: **$2,500**
- Denarius: **$10,000**
- Gold Aureus: **$20,000**
- Best of month: **$20,000**

The next deadline in the first-party docs is **September 30, 2026, 11:59pm Pacific**. The eligibility text explicitly names both:
- resolving outstanding bugs in tools people are using, evidenced by before/after material; and
- improvements to VC3D.

This packet therefore clears the owner's $50 floor as a **competitive prize lane**, not a guaranteed award.

## Current-source root cause

`MenuActionController::attachSegments()` calls `promptLocation()`, which delegates to `UnifiedBrowserDialog`.

The current unified browser has two fragile local-path behaviors:

1. `pathToFileUri()` builds local URIs by concatenating `"file://"` with a path, while `fileUriToPath()` reverses that by slicing the first seven characters. That bypasses Qt's platform-aware handling of file authorities and network paths.
2. Typed local paths go to `QFileInfo/QDir` without first normalizing native separators. A pasted Windows path such as `\\wsl.localhost\Ubuntu\...` therefore does not get the same canonical slash representation as paths produced internally.

The existing dialog test suite already protects against stale list selection when the path bar is edited, but has no UNC/network-path regression.

## Patch

This packet contains byte-complete replacements for four upstream files:

1. `volume-cartographer/apps/VC3D/UnifiedBrowserDialog.cpp`
   - local file URI generation now uses `QUrl::fromLocalFile`;
   - local file URI decoding now uses `QUrl::toLocalFile`;
   - native separators normalize through `QDir::fromNativeSeparators`;
   - local navigation stores and displays the normalized path.
2. `volume-cartographer/apps/VC3D/MenuActionController.cpp`
   - `promptLocation()` decodes `file:` URIs through `QUrl::toLocalFile` instead of `mid(7)`;
   - local trailing separators are trimmed only when the path is not a filesystem root.
3. `volume-cartographer/apps/VC3D/test/test_unified_browser_dialog.cpp`
   - adds a platform-independent regression proving a `file://wsl.localhost/...` authority remains `//wsl.localhost/...`;
   - adds a Windows-only test that pastes `QDir::toNativeSeparators(...)` into the path bar and opens it successfully.
4. `.github/workflows/vc3d-windows.yml`
   - after the native Windows build, runs the existing `unified_browser_dialog` CTest target so the Windows-only branch is actually executed on PRs.

## Exact source identities

See `manifest.json`. The packet stores both original upstream blob identities and the proposed replacement blob identities.

## Verification required on an adoptable villa fork

Run on the exact packet bytes:

```bash
cd volume-cartographer
cmake --preset ci-windows-mingw
ninja -C build/ci-windows-mingw
ctest --test-dir build/ci-windows-mingw --output-on-failure -R '^unified_browser_dialog$'
```

Then run the repository's normal VC3D PR gates. Because #1839 is user-visible and the Progress Prize criteria ask for evidence, the adopting seat should capture:
- before: direct `\\wsl.localhost\...` navigation fails / stale location behavior;
- after: the same UNC path navigates to the intended directory and `Open` returns that directory, not the previous selection.

## Publication constraint / handoff

The current connector reports `ScrollPrize/villa` as pull-only for `woahwhattheheck`, and no `woahwhattheheck/villa` fork is available through the GitHub connector. Upstream issue #1743 separately documents current first-time-contributor PR creation restrictions.

Therefore this packet does **not** claim an upstream PR. It is a byte-complete, source-pinned adoption packet for a prior-contributor or otherwise fork-capable seat. Do not create a second implementation if another canonical carrier appears; reconcile against these exact upstream and replacement blob identities first.
