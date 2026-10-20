// CUSA03173 1.09: the banner contains a CSWString, not an inline char16_t array.
std::string BbscBannerText() {
    const u64 object = image_base + BannerTextOffset;
    if (!ReadablePointer(object, 0x30)) {
        return {};
    }
    u64 text = ReadValue<u64>(object, 0);
    u64 limit = 120;
    if (text == 0) {
        // Owned string: allocator +8, buffer +0x10, length +0x20, capacity +0x28.
        // A capacity below eight means its seven-character inline buffer is used.
        const u64 size = ReadValue<u64>(object, 0x20);
        const u64 capacity = ReadValue<u64>(object, 0x28);
        if (size > capacity) {
            return {};
        }
        limit = std::min<u64>(limit, size);
        text = capacity < 8 ? object + 0x10 : ReadValue<u64>(object, 0x10);
    }
    std::string out;
    for (u64 i = 0; i < limit && ReadablePointer(text + i * 2, 2); ++i) {
        const u16 c = ReadValue<u16>(text, i * 2);
        if (c == 0) {
            break;
        }
        if (c < 0x80) {
            out.push_back(c == '\n' ? ' ' : static_cast<char>(c));
        } else {
            // Preserve non-ASCII UTF-16 units instead of turning all Chinese into '?'.
            out += fmt::format("\\u{:04x}", c);
        }
    }
    return out;
}

// Observe the message builder before it queries category 101 and then category 30.
// The fixed game's selected-player dismissal reads role-table +0x40. BBSC's
// reconstructed guests can have role zero. If that lookup provides no message,
// repair only this call's missing ID; never change roles or the shared text tables.
void PS4_SYSV_ABI BbscEventTextHook(u64, const GuestRegisterSnapshot* registers) {
    if (registers == nullptr) {
        return;
    }
    const u32 id = static_cast<u32>(registers->rsi);
    const u64 caller = ReadablePointer(registers->rsp, sizeof(u64))
                           ? ReadValue<u64>(registers->rsp, 0) : 0;
    const u64 caller_offset = caller >= image_base ? caller - image_base : caller;
    // This builder also runs every frame for ordinary menu help. Observe only
    // the fixed game's co-op producers, including its host-join producer.
    if (!((caller_offset >= 0x01382720 && caller_offset < 0x0138B400) ||
          (caller_offset >= 0x0196BA20 && caller_offset < 0x0196C000) ||
          (caller_offset >= 0x01BFFE80 && caller_offset < 0x01C00050))) {
        return;
    }
    u64 fmg = 0;
    const u64 slot101 = FindBbscMessageSlot(101, id, &fmg);
    const bool found101 = slot101 != 0 && ReadValue<u64>(slot101, 0) != 0;
    const u64 slot30 = FindBbscMessageSlot(30, id, &fmg);
    const bool found30 = slot30 != 0 && ReadValue<u64>(slot30, 0) != 0;
    LOG_INFO(Debug, "BBSC event text: id={} caller={:#x} category101={} category30={} "
                    "session={}", id, caller_offset, found101, found30, GetSessionState());
    constexpr u64 HostDismissCaller = 0x01387CA8;
    constexpr u32 HostDismissMessage = 20000400;
    if (id == 0 && caller_offset == HostDismissCaller && GetSessionState() == 3 &&
        !found101 && !found30) {
        const u64 slot = FindBbscMessageSlot(30, HostDismissMessage, &fmg);
        const u64 offset = slot != 0 ? ReadValue<u64>(slot, 0) : 0;
        if (offset != 0 && ReadablePointer(fmg + offset, 2) &&
            ReadValue<u16>(fmg + offset, 0) != 0) {
            // The hook owns a mutable saved-register frame. Its trampoline restores
            // RSI from this frame before executing the original message builder.
            const_cast<GuestRegisterSnapshot*>(registers)->rsi = HostDismissMessage;
            LOG_INFO(Debug, "BBSC dismiss text: recovered empty role message 0 -> {}",
                     HostDismissMessage);
        }
    }
}
