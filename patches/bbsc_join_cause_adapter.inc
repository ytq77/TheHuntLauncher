namespace BbscMod {
// Installation uses the existing marker even before J4 Start. Sampling requires
// initialized, live windows and does not touch game memory outside those windows.
bool J4JoinCauseEnabled(bool active) noexcept {
#if !defined(BBSC_J4_DIAG_DISABLED)
    try {
        if(active) {
            if(!BbscJ4Diag::enabled.load(std::memory_order_relaxed)||!BbscJ4Diag::activeWindows.load(std::memory_order_acquire))return false;
            std::unique_lock lock{BbscJ4Diag::mutex,std::try_to_lock};if(!lock)return false;
            const auto now=BbscJ4Diag::Now();
            for(const auto& w:BbscJ4Diag::windows)if(w.context>0&&w.connection>0&&now>=w.begin&&now<w.end&&now-w.begin<=8000)return true;
            return false;
        }
        HMODULE module{};
        if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            reinterpret_cast<LPCWSTR>(&BbscJ4Diag::InstallSlots),&module))return false;
        std::array<wchar_t,32768> path{};
        const auto count=GetModuleFileNameW(module,path.data(),DWORD(path.size()));
        return count&&count<path.size()&&BbscJ4Diag::EnableFile(std::filesystem::path(path.data()).parent_path()/L"j4-diag.enable");
    }catch(...){return false;}
#else
    return false;
#endif
}
void J4JoinCauseRegisterClose(void (*close)(std::uint64_t)) noexcept {
#if !defined(BBSC_J4_DIAG_DISABLED)
    BbscJ4Diag::joinCauseClose.store(close,std::memory_order_release);
#endif
}
bool J4JoinCauseWindow(std::int32_t context,std::int32_t connection,
                      std::uint64_t& id,std::uint64_t& begin,std::uint64_t& end,std::uint64_t& now) noexcept {
#if !defined(BBSC_J4_DIAG_DISABLED)
    if(!BbscJ4Diag::enabled.load(std::memory_order_relaxed)||!BbscJ4Diag::activeWindows.load(std::memory_order_acquire))return false;
    std::unique_lock lock{BbscJ4Diag::mutex,std::try_to_lock};
    if(!lock)return false;
    now=BbscJ4Diag::Now();
    const BbscJ4Diag::Window* found=nullptr;
    for(const auto& w:BbscJ4Diag::windows) {
        if(w.context<=0||w.connection<=0||now<w.begin||now>=w.end||now-w.begin>8000)continue;
        if(context||connection){if(context!=w.context||connection!=w.connection)continue;}
        if(found)return false; // Join-gate observations require one unambiguous window.
        found=&w;
    }
    if(!found)return false;
    id=found->id;begin=found->begin;end=found->end;return true;
#else
    return false;
#endif
}
}
