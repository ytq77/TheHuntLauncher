"""Prefer verified active kit tables; keep full effects and inventory safeguards."""
import argparse
from pathlib import Path
import shutil
from prepare_personal_rewards import once

ROOT=Path(__file__).resolve().parents[1]


def apply(text):
    text=once(text,'void PrepareBbscDuelBell() {',
        '#include "bbsc_kit_repository.inc"\n\nvoid PrepareBbscDuelBell() {')
    begin=text.index('    const s64 started_ms = SteadyNowMs();',text.index('void PrepareBbscDuelBell() {'))
    end=text.index('    size_t goods = 0;',begin)
    expected='''    const s64 started_ms = SteadyNowMs();
    const auto goods_tables = FindBbscParamTables("EQUIP_PARAM_GOODS_ST", 100, 1);
    if (goods_tables.empty()) {
        LOG_INFO(Debug, "BBSC kit initialization: goods not loaded yet ({} ms)",
                 SteadyNowMs() - started_ms);
        return; // Do not run another full memory scan before goods have loaded.
    }
    const s64 goods_ms = SteadyNowMs();
    const auto speffect_tables = FindBbscParamTables("SP_EFFECT_PARAM_ST");
    LOG_INFO(Debug, "BBSC kit initialization: goods scan {} ms; effects scan {} ms",
             goods_ms - started_ms, SteadyNowMs() - goods_ms);
'''
    if text[begin:end]!=expected:raise ValueError('Unexpected kit lookup block')
    text=text[:begin]+'''    if (bbsc_duel_bell_ready.load(std::memory_order_acquire)) return;
    const s64 started_ms = SteadyNowMs();
    const u64 goods_table = BbscActiveGoodsTable();
    const u64 effect_table = BbscActiveSpEffectTable();
    std::vector<u64> goods_tables, speffect_tables;
    if (goods_table && effect_table && BbscKitGoodsRowsComplete(goods_table) &&
        BbscKitEffectRowsComplete(effect_table)) {
        goods_tables.push_back(goods_table);
        speffect_tables.push_back(effect_table);
        LOG_INFO(Debug, "BBSC kit initialization: using active Goods and SpEffect repositories");
    } else if (BbscKitNativeTablesLoading()) {
        return; // Normal loading: poll the small native repository, never start a full scan.
    } else {
        LOG_WARNING(Debug, "BBSC kit initialization: loaded repository validation failed; using scan fallback");
        goods_tables = FindBbscParamTables("EQUIP_PARAM_GOODS_ST", 100, 1);
        speffect_tables = FindBbscParamTables("SP_EFFECT_PARAM_ST");
        std::erase_if(goods_tables, [](u64 table) { return !BbscKitGoodsRowsComplete(table); });
        std::erase_if(speffect_tables, [](u64 table) { return !BbscKitEffectRowsComplete(table); });
        if (goods_tables.empty() || speffect_tables.empty()) return;
    }
    LOG_INFO(Debug, "BBSC kit initialization: verified tables found after {} ms",
             SteadyNowMs() - started_ms);
'''+text[end:]
    text=once(text,"        for (int attempt = 0; attempt < 36 && !bbsc_duel_bell_ready; ++attempt) {",
        "        for (int attempt = 0; attempt < 180 && !bbsc_duel_bell_ready &&\n"
        "                              SteadyNowMs() - started_ms < 180'000; ++attempt) {")
    return once(text,"                std::this_thread::sleep_for(std::chrono::seconds(5));\n            }\n            PrepareBbscDuelBell();",
        "                std::this_thread::sleep_for(std::chrono::seconds(1));\n            }\n            PrepareBbscDuelBell();")


def main():
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);args=parser.parse_args()
    core=args.source/'emulator/src/core';path=core/'bloodborne_re_zh_cn.cpp'
    updated=apply(path.read_text('utf-8'))
    shutil.copy2(ROOT/'patches/bbsc_kit_repository.inc',core)
    path.write_text(updated,encoding='utf-8',newline='\n')
    print('Prepared verified active kit repositories; full effect and inventory guards retained.')


if __name__=='__main__':main()
