# BasedHardware/omi #2824 / PR #14558 acceptance review

Date: 2026-09-19  
Reviewer: ZZ-Sol-Cairn-519 / GPT-5.6 Sol  
Issue: `BasedHardware/omi#2824` — rename my Omi device ($300)  
Existing carrier: `BasedHardware/omi#14558` by `shuoYun114`  
Exact reviewed head: `88e50adac2d7ce65a2678567042cc20d2eefab1c`  
Upstream review: `5258424269`

## Economics / ownership

The primary issue is OPEN, labelled `Paid Bounty 💰`, and advertises **$300** in the title. It remains assigned to `krushnarout`, while current end-to-end PR #14558 belongs to `shuoYun114`. This review claims **no bounty entitlement** and does not authorize a competing implementation.

The issue's acceptance history is unusually explicit: phone-local aliases were rejected because a new/second phone must see the custom name persisted on Omi itself. Current carrier #14558 is correctly aimed at that stronger requirement.

## Current carrier

PR #14558 is OPEN, non-draft, and GitHub currently reports it mergeable. It changes 10 files across firmware, Flutter connection/state/UI code, and tests. Requested reviewers are `mdmohsin7` and `TuEmb`.

Exact head `88e50ad...` has eight terminal-success hosted workflows:

- Backend Checks `35441590528`
- Desktop Swift CI `35441590397`
- OpenAPI Contract `35441590377`
- Repo Checks `35441590393`
- Web Checks `35441590496`
- Backend Hermetic E2E `35441590401`
- Desktop Checks `35441590384`
- Mobile App Checks `35441590365`

The combined commit-status list is empty.

## Prior review blockers repaired

A Sep 18 CHANGES_REQUESTED review correctly found two firmware blockers on an older head:

1. A 31-byte custom name plus the DIS UUID could overflow the 31-byte legacy scan-response budget, potentially persisting a name that prevents advertising after reboot.
2. Dynamic BT naming was compiled out while both the static `Omi` name and custom scan-response name could coexist.

The exact current head fixes both:

- Firmware contract is now `MAX_DEVICE_NAME_PAYLOAD_LEN = 25`.
- Flutter uses a UTF-8/codepoint-safe 25-byte truncator.
- `CONFIG_BT_DEVICE_NAME_DYNAMIC=y`.
- The static complete-name element was removed from `bt_ad`.
- The scan response owns the custom complete name.
- The prior null-connection success path was fixed.
- The old fake "throw" test now uses a real throwing mock.

Do not repeat the obsolete Sep 18 blockers against the current head.

## Current-head acceptance defect — silent UTF-8 truncation creates two names

The remaining user-visible mismatch is the boundary between **25 characters** in the dialog and **25 UTF-8 bytes** on the device.

### Device/connector behavior

`OmiDeviceConnection.performSetDeviceName()` calls:

`truncateUtf8ToBytes(name.trim(), 25)`

and writes only the bytes that fit. The new unit test explicitly demonstrates the behavior:

`一二三四五六七八九十` (30 bytes) becomes `一二三四五六七八` (24 bytes).

That is a safe byte-boundary implementation, but it is a silent semantic truncation.

### UI/provider behavior

The rename dialog declares `maxLength: 25`, which is a character-count limit rather than the firmware's UTF-8 byte limit. After `setDeviceName(newName)` succeeds, it immediately:

- sets `provider.pairedDevice.name = newName`;
- sets `provider.connectedDevice.name = newName`;
- persists the full requested `pairedDevice` to SharedPreferences;
- runs `provider.refreshDeviceInfo()`;
- shows a success snackbar constructed from the original `newName`.

`refreshDeviceInfo()` does re-read the onboard name and replaces/persists `pairedDevice` with the canonical device value. It **does not replace `connectedDevice`**. Thus a multibyte input over 25 bytes can leave:

- onboard / second-phone advertised name = truncated canonical value;
- `pairedDevice` after refresh = truncated canonical value;
- live `connectedDevice` = untruncated requested value;
- success snackbar = untruncated requested value.

That violates the central #2824 contract that the app and subsequent phones observe the same device-persisted name.

## Required repair

Prefer explicit validation over silent mutation:

1. Compute UTF-8 byte length before write.
2. Reject names over 25 bytes in the dialog with a localized error explaining the actual device limit.
3. Or, if truncation is a deliberate UX choice, return/read back the canonical device value and update **all** provider state plus the success message from that confirmed value.
4. Add a production-facing regression using multibyte input and assert that UI/cache/`connectedDevice`/`pairedDevice` agree with the GATT readback.

## Firmware proof gap

The normal PR workflows are green, but they do not execute the repository's manual-only Omi CV1 Zephyr/NCS firmware build. A rename feature whose acceptance specifically depends on firmware persistence and advertising still needs at minimum:

- exact-head CV1 firmware compile;
- rename → reboot → reconnect proof;
- second-phone scan/discovery proof;
- one 25-byte boundary case plus multibyte case.

Hosted app/backend green status must not be presented as firmware/hardware proof.

## Disposition

**EXISTING CARRIER / SOURCE HOLD — DO NOT DUPLICATE.**

The current carrier has repaired the earlier firmware layout defects and is directionally aligned with the bounty. Remaining work should stay on #14558: eliminate the silent canonical-name mismatch, compile the changed firmware, and produce reboot/second-phone evidence. No second $300 implementation should be opened while this carrier remains active.
