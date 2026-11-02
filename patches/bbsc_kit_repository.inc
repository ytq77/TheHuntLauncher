bool BbscKitTableHeader(u64 table, std::string_view type, u16 minimum, u32 first) {
    if (!ReadablePointer(table, 0x58) ||
        std::memcmp(reinterpret_cast<const void*>(table + 0x0C), type.data(), type.size()) != 0 ||
        ReadValue<u8>(table, 0x0C + type.size()) != 0) return false;
    const auto count = ReadValue<u16>(table, 0x0A);
    return count > minimum && ReadablePointer(table, 0x40 + static_cast<size_t>(count) * 0x18) &&
           ReadValue<u32>(table, 0x40) == first;
}

// CUSA03173 1.09: SpEffect accessor 0x01F28AA0 checks repository +0x4A8,
// then follows +0x4B0 -> +0x70 -> +0x70. The native parameter-name table
// identifies SpEffectParam as type 15. Validate the loaded header before use.
u64 BbscActiveSpEffectTable() {
    const auto pointer = image_base + 0x05540340;
    if (!ReadablePointer(pointer, 8)) return 0;
    const auto repo = ReadValue<u64>(pointer, 0);
    if (!ReadablePointer(repo, 0x4B8) || ReadValue<s32>(repo, 0x4A8) == 0) return 0;
    const auto resource = ReadValue<u64>(repo, 0x4B0);
    const auto holder = ReadablePointer(resource, 0x78) ? ReadValue<u64>(resource, 0x70) : 0;
    const auto table = ReadablePointer(holder, 0x78) ? ReadValue<u64>(holder, 0x70) : 0;
    return BbscKitTableHeader(table, "SP_EFFECT_PARAM_ST", 1000, 0) ? table : 0;
}

bool BbscKitNativeTablesLoading() {
    const auto pointer = image_base + 0x05540340;
    if (!ReadablePointer(pointer, 8)) return true;
    const auto repo = ReadValue<u64>(pointer, 0);
    return !ReadablePointer(repo, 0x4B8) || ReadValue<s32>(repo, 0x148) == 0 ||
           ReadValue<s32>(repo, 0x4A8) == 0;
}

bool BbscKitGoodsRowsComplete(u64 table) {
    if (!BbscKitTableHeader(table, "EQUIP_PARAM_GOODS_ST", 100, 1)) return false;
    for (const auto& item : BbscKit) {
        if (!BbscParamRow(table, item.id, GoodsRowSize) ||
            (item.copy_of && !BbscParamRow(table, item.copy_of, GoodsRowSize))) return false;
    }
    return true;
}

bool BbscKitEffectRowsComplete(u64 table) {
    if (!BbscKitTableHeader(table, "SP_EFFECT_PARAM_ST", 1000, 0)) return false;
    for (u32 id : {7u, BbscDuelBellEffectId, BbscDuelBellSourceEffectId, BbscInvadableEffectId}) {
        if (!BbscParamRow(table, id, SpEffectRowSize)) return false;
    }
    return true;
}
