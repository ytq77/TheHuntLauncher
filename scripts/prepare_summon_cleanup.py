"""Serialize private sign cleanup and native asynchronous HTTP dispatch, once."""
from pathlib import Path
import hashlib
from prepare_game_hardening_regression import function
PIN='7c44c12084f8e88243c35d466b3a2d0b4faf46593506e1e1e33d3eeaa69a6d20'
def once(text,a,b):
    if text.count(a)!=1:raise ValueError('Unexpected summon cleanup anchor: '+a[:90])
    return text.replace(a,b,1)
def apply_http(text):
    if hashlib.sha256(text.encode()).hexdigest()!=PIN:raise ValueError('Unexpected/already patched coverage3 HTTP source')
    text=once(text,'#include <windows.h>','#include <windows.h>\n#include <memory>\n#include <optional>\n#include "bbsc_summon_cleanup.h"')
    text=once(text,'bool RelayAuthInRoom();','bool RelayAuthInRoom();\nstd::uint32_t RelayAuthOwnId();')
    text=once(text,'constexpr u64 SlotReadData = 0x053E5668;','constexpr u64 SlotReadData = 0x053E5668;\nconstexpr u64 SlotStatus = 0x053E5658;\nconstexpr u64 SlotLength = 0x053E5660;\nconstexpr u64 SlotWait = 0x053E5598;')
    text=once(text,'ReadDataFn original_read_data = nullptr;','ReadDataFn original_read_data = nullptr;\nusing StatusFn = int PS4_SYSV_ABI (*)(int,int*);\nusing LengthFn = int PS4_SYSV_ABI (*)(int,int*,u64*);\nstruct CleanupEvent { u32 events,detail; s32 id; void* user; };\nstatic_assert(sizeof(CleanupEvent)==24);\nusing WaitFn = int PS4_SYSV_ABI (*)(void*,CleanupEvent*,int,int);\nStatusFn original_status=nullptr;\nLengthFn original_length=nullptr;\nWaitFn original_wait=nullptr;')
    text=once(text,'std::atomic<bool> stale_cleared{false};','')
    text=once(text,'std::atomic<bool> was_in_session{false};','')
    original=function(text,'void ClearStaleRecord(')
    text=once(text,original,'// Persisted previous-process records are cleared by the serial worker at startup.')
    original=function(text,'void WatchSessionForStaleClaims() {')
    watch="""#include "bbsc_summon_cleanup.inc"

void WatchSessionForStaleClaims() {
    const auto state=SessionState();
    if(state==3 || state==4 || state==6) {claim_delivered_ms=0;return;}
    const auto delivered=claim_delivered_ms.load();
    if(state==0 && delivered && NowMs()-delivered>ClaimJoinWaitMs) {
        claim_delivered_ms=0;
        if(auto* cleanup=summon_cleanup.load()) cleanup->ReclearOnNextSign();
        LOG_WARNING(Mod,"HTTP: unjoined claim expired; next sign will be serialized after cleanup");
    }
}"""
    text=once(text,original,watch)
    original=function(text,'int PS4_SYSV_ABI SendRequest(')
    changed=original.replace('return original_send(','return ForwardSend(')
    changed=once(changed,'        ClearStaleRecord(url, body);','        // Cleanup and native SendRequest share the worker queue.')
    text=once(text,original,changed)
    text=once(text,'    const int read = original_read_data(request, data, size);','    if(const auto pending=CleanupRequestResult(request)) return *pending;\n    const int read = original_read_data(request, data, size);')
    original=function(text,'int PS4_SYSV_ABI DeleteRequest(')
    replacement="""int PS4_SYSV_ABI DeleteRequest(int request) {
    std::shared_ptr<CleanupRequest> state;
    {std::scoped_lock lock(state_mutex);
     request_urls.erase(request);
     const auto it=cleanup_requests.find(request);
     if(it!=cleanup_requests.end()) state=it->second;}
    if(!state) return original_delete(request);
    std::scoped_lock lock(state->mutex);
    state->cancelled=true;
    if(state->ordered && state->dispatched && state->result>=0 && !state->completed) {
        state->deferred_delete=true;
        return 0; // Native request remains alive until its async network completion.
    }
    state->native_deleted=true;
    if(state->ordered && state->dispatched && !state->notified) return original_delete(request);
    {std::scoped_lock map_lock(state_mutex);
     const auto it=cleanup_requests.find(request);
     if(it!=cleanup_requests.end() && it->second==state) cleanup_requests.erase(it);}
    return original_delete(request);
}"""
    text=once(text,original,replacement)
    anchor='    Swap<CreateConnectionFn>(SlotCreateConnection, &CreateConnectionWithURL,'
    text=once(text,anchor,'    original_status=reinterpret_cast<StatusFn>(WaitForSlot(SlotStatus));\n    original_length=reinterpret_cast<LengthFn>(WaitForSlot(SlotLength));\n    original_wait=reinterpret_cast<WaitFn>(WaitForSlot(SlotWait));\n    StartSummonCleanup(); // No game HTTP can pass the startup barrier before old delete.\n'+anchor)
    text=once(text,'    const bool ok = original_create_connection', '    Swap<StatusFn>(SlotStatus, &CleanupGetStatus, original_status, "GetStatusCode");\n    Swap<LengthFn>(SlotLength, &CleanupGetLength, original_length, "GetResponseContentLength");\n    Swap<WaitFn>(SlotWait, &CleanupWait, original_wait, "WaitRequest");\n    const bool ok = original_status && original_length && original_wait && original_create_connection')
    return text
