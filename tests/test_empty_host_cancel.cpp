#include <cassert>
#include <initializer_list>
#include "../patches/bbsc_empty_host_cancel_policy.h"
using namespace BbscEmptyHostCancel;

int main() {
    const Snapshot baseline{1000, 1500, 3, 1, 0, true, true, true, true, false, false};
    assert(Evaluate(baseline) == Decision::EndRoom);
    auto s=baseline;
    s.requested_ms=0; assert(Evaluate(s)==Decision::Discard); // No confirmed action: no empty-room timeout.
    s=baseline; s.searching=true; assert(Evaluate(s)==Decision::Wait); // Native cancellation not done yet.
    s=baseline; s.now_ms=1100; assert(Evaluate(s)==Decision::Wait);
    s=baseline; s.members=2; assert(Evaluate(s)==Decision::KeepRoom); // Joining guest, character not created.
    s=baseline; s.remote_players=1; assert(Evaluate(s)==Decision::KeepRoom);
    for (int state : {0,1,2,4,5,6,7}) {
        s=baseline; s.session_state=state; assert(Evaluate(s)==Decision::Discard);
    }
    for (int members : {-1,0}) {
        s=baseline; s.members=members; assert(Evaluate(s)==Decision::Discard);
    }
    s=baseline; s.remote_players=-1; assert(Evaluate(s)==Decision::Discard);
    s=baseline; s.same_manager=false; assert(Evaluate(s)==Decision::Discard);
    s=baseline; s.memory_ready=false; assert(Evaluate(s)==Decision::Discard);
    s=baseline; s.own_world=false; assert(Evaluate(s)==Decision::Discard);
    s=baseline; s.alive=false; assert(Evaluate(s)==Decision::Discard);
    s=baseline; s.loading=true; assert(Evaluate(s)==Decision::Discard);
    s=baseline; s.now_ms=11001; assert(Evaluate(s)==Decision::Discard);
    s=baseline; s.now_ms=999; assert(Evaluate(s)==Decision::Discard);
    s=baseline; s.members=5; s.loading=true;
    assert(Evaluate(s)==Decision::Discard); // Travel/death cannot arm a teardown.
}
