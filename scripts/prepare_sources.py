"""Apply bounded compatibility changes to a clean, pinned BBSC source tree."""
import argparse
import json
from pathlib import Path
import shutil
from banner_diagnostics import apply as banner_diagnostics

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("Unexpected upstream source: " + before[:100])
    return text.replace(before, after)


def early_items(text):
    # The original starts only at the first in-world TickBbscPvp. Start while hooks
    # are installed instead; preserve its atomic once guard and the safe scanner.
    text = replace_once(
        text,
        "    StartBbscScaling();\n    StartBbscSolidNpcs();",
        "    StartBbscDuelBell(); // Prepare saved kit rows before entering a character.\n"
        "    StartBbscScaling();\n    StartBbscSolidNpcs();",
    )
    text = replace_once(
        text,
        """    std::thread{[] {
        for (int attempt = 0; attempt < 36 && !bbsc_duel_bell_ready; ++attempt) {
            std::this_thread::sleep_for(std::chrono::seconds(5));
            PrepareBbscDuelBell();
        }
    }}.detach();""",
        """    std::thread{[] {
        const s64 started_ms = SteadyNowMs();
        LOG_INFO(Debug, "BBSC kit initialization: started at hook installation");
        for (int attempt = 0; attempt < 36 && !bbsc_duel_bell_ready; ++attempt) {
            if (attempt != 0) {
                std::this_thread::sleep_for(std::chrono::seconds(5));
            }
            PrepareBbscDuelBell();
            if (bbsc_duel_bell_ready) {
                LOG_INFO(Debug, "BBSC kit initialization: ready after {} ms (attempt {})",
                         SteadyNowMs() - started_ms, attempt + 1);
            }
        }
        if (!bbsc_duel_bell_ready) {
            LOG_WARNING(Debug, "BBSC kit initialization: not ready after {} ms",
                        SteadyNowMs() - started_ms);
        }
    }}.detach();""",
    )
    # Do not publish ready or enable Duel Bell until both tables have been patched.
    text = replace_once(
        text,
        """void PrepareBbscDuelBell() {
    const auto goods_tables = FindBbscParamTables("EQUIP_PARAM_GOODS_ST", 100, 1);
    const auto speffect_tables = FindBbscParamTables("SP_EFFECT_PARAM_ST");""",
        """void PrepareBbscDuelBell() {
    const s64 started_ms = SteadyNowMs();
    const auto goods_tables = FindBbscParamTables("EQUIP_PARAM_GOODS_ST", 100, 1);
    if (goods_tables.empty()) {
        LOG_INFO(Debug, "BBSC kit initialization: goods not loaded yet ({} ms)",
                 SteadyNowMs() - started_ms);
        return; // Do not run another full memory scan before goods have loaded.
    }
    const s64 goods_ms = SteadyNowMs();
    const auto speffect_tables = FindBbscParamTables("SP_EFFECT_PARAM_ST");
    LOG_INFO(Debug, "BBSC kit initialization: goods scan {} ms; effects scan {} ms",
             goods_ms - started_ms, SteadyNowMs() - goods_ms);""",
    )
    return text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    core = args.source / "emulator/src/core"
    path = core / "bloodborne_re.cpp"
    edits = {path: banner_diagnostics(early_items(path.read_text(encoding="utf-8")))}
    for name in ("bbsc_http.cpp", "bbsc_relay.cpp"):
        path = args.source / "emulator/mod" / name
        text = path.read_text(encoding="utf-8")
        # CLANG64 libc++ accepts a filesystem::path for a UTF-16 Windows path.
        before = "std::ifstream in(path, std::ios::binary);"
        if before not in text:
            raise ValueError(f"Upstream fstream expression changed in {name}")
        edits[path] = text.replace(before, "std::ifstream in(std::filesystem::path(path), std::ios::binary);")
    for path, text in edits.items():
        path.write_text(text, encoding="utf-8")
    shutil.copy2(ROOT / "patches/bbsc_memscan.cpp", core / "bbsc_memscan.cpp")
    shutil.copy2(ROOT / "patches/bbsc_banner_diagnostics.inc", core / "bbsc_banner_diagnostics.inc")
    print("Applied early item initialization, safe scan, banner text handling, and CLANG64 path fixes.")


if __name__ == "__main__":
    main()
