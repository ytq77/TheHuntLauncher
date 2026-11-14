// Fix the active inventory's goods definitions on the game thread as soon as
// the known native repository is ready. Full effect/copy discovery stays async.
std::mutex bbsc_kit_rows_mutex;

u64 BbscActiveGoodsTable() {
    const auto pointer = image_base + 0x05540340;
    if (!ReadablePointer(pointer, 8)) return 0;
    const auto repo = ReadValue<u64>(pointer, 0);
    if (!ReadablePointer(repo, 0x158) || ReadValue<s32>(repo, 0x148) == 0) return 0;
    const auto resource = ReadValue<u64>(repo, 0x150);
    const auto holder = ReadablePointer(resource, 0x78) ? ReadValue<u64>(resource, 0x70) : 0;
    const auto table = ReadablePointer(holder, 0x78) ? ReadValue<u64>(holder, 0x70) : 0;
    constexpr char expected[] = "EQUIP_PARAM_GOODS_ST";
    if (!ReadablePointer(table, 0x58) ||
        std::memcmp(reinterpret_cast<const void*>(table + 0x0C), expected, sizeof(expected)) != 0)
        return 0;
    const auto count = ReadValue<u16>(table, 0x0A);
    if (count <= 100 || !ReadablePointer(table, 0x40 + static_cast<size_t>(count) * 0x18) ||
        ReadValue<u32>(table, 0x40) != 1) return 0;
    return table;
}

void TickBbscKitFastPrepare() {
    static bool prepared = false;
    if (prepared || bbsc_duel_bell_ready.load(std::memory_order_acquire)) return;
    const auto table = BbscActiveGoodsTable();
    if (!table) return;
    // No partial edits while the table is being built. Require every row first.
    for (const auto& item : BbscKit) {
        if (!BbscParamRow(table, item.id, GoodsRowSize) ||
            (item.copy_of && !BbscParamRow(table, item.copy_of, GoodsRowSize))) return;
    }
    std::scoped_lock lock{bbsc_kit_rows_mutex};
    if (bbsc_duel_bell_ready.load(std::memory_order_acquire)) return;
    PrepareBbscKitRows(table);
    // The custom PvP bell must remain unusable until its effect is prepared by
    // the existing worker. Do not expose the unused slot's original action.
    const auto duel = BbscParamRow(table, BbscDuelBellGoodsId, GoodsRowSize);
    WriteValue<u8>(duel, 68, 0);
    prepared = true;
    LOG_INFO(Debug, "BBSC kit initialization: active goods prepared before background effects; PvP bell waits for effects");
}
