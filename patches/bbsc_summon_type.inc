// StageFlow 0x01939470 calls ReloadFinish/ApplySummonType, then consumes
// [0x0553B130]+0x8C at 0x019394AA..D1, overwriting the calculated slot type.
// comp's 01:58:20 join retained pending host type 0 from its preceding world;
// this made a type-0 character even though the session was already guest (6).
// Remove only that conflicting override BEFORE native classification/creation.
// Positive kept-death overrides and the own-world restore record stay intact.
void PrepareBbscSummonedType() {
    if (GetSessionState() != 6) return;
    const u64 reload = ReadValue<u64>(image_base + 0x0553D6D0, 0);
    const u64 slots = ReadValue<u64>(image_base + PlayerSlotManagerPointerOffset, 0);
    if (!ReadablePointer(reload, 0x88) || ReadValue<s32>(reload, 0x84) != 3 ||
        !ReadablePointer(slots, 0x90) || ReadValue<s32>(slots, 0x8C) != 0 ||
        !HasMemoryAccess(slots + 0x8C, 4, MemoryProt::CpuWrite)) return;
    WriteValue<s32>(slots, 0x8C, -1);
    LOG_INFO(Debug, "BBSC summon type: discarded pending host type 0 at guest reload finish; native summon type and character creation continue");
}
