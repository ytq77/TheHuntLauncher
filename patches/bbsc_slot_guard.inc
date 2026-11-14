// These slots can be temporarily unmapped or protected while BBport loads.
// Kernel copies fail safely even if their mapping changes after a prior check;
// a cached Readable/VirtualQuery check followed by *slot would still race.
bool RefreshGuardedSlot(GuardedSlot& guarded) {
    auto* slot = reinterpret_cast<void*>(EbootBase + guarded.slot_offset);
    const HANDLE process = GetCurrentProcess();
    u64 value = 0;
    SIZE_T copied = 0;
    if (!ReadProcessMemory(process, slot, &value, sizeof(value), &copied) ||
        copied != sizeof(value)) return false;
    if (value == guarded.wrapper || value == 0 ||
        (value >= EbootBase && value < EbootBase + EbootSize)) return false;
    DWORD old = 0;
    if (!VirtualProtect(slot, sizeof(value), PAGE_READWRITE, &old)) return false;
    // Do not replace a slot that the loader changed while protection was set.
    u64 current = 0;
    copied = 0;
    bool written = false;
    if (ReadProcessMemory(process, slot, &current, sizeof(current), &copied) &&
        copied == sizeof(current) && current == value) {
        *reinterpret_cast<void* volatile*>(guarded.original) = reinterpret_cast<void*>(value);
        copied = 0;
        written = WriteProcessMemory(process, slot, &guarded.wrapper,
                                     sizeof(guarded.wrapper), &copied) &&
                  copied == sizeof(guarded.wrapper);
    }
    DWORD ignored = 0;
    VirtualProtect(slot, sizeof(value), old, &ignored);
    if (written && ++guarded.rewritten <= 3) {
        LOG_INFO(Mod, "Import {} was linked again by the emulator - wrapper put back ({}x)",
                 guarded.name, guarded.rewritten);
    }
    return written;
}

DWORD WINAPI SlotGuardThread(void*) {
    for (;;) {
        Sleep(5);
        std::scoped_lock lock{guarded_mutex};
        for (auto& guarded : guarded_slots) RefreshGuardedSlot(guarded);
    }
}
