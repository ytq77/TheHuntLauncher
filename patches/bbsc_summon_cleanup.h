// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <filesystem>
#include <mutex>
#include <string>
#include <thread>

namespace TheHuntSummon {
struct Sign {
    std::string session, user;
    bool operator==(const Sign&) const = default;
    bool Valid() const {
        if (session.empty() || session.size()>128 || user.empty() || user.size()>19) return false;
        for (unsigned char c:session)
            if (!((c>='0'&&c<='9')||(c>='a'&&c<='z')||(c>='A'&&c<='Z')||c=='-'||c=='_')) return false;
        std::uint64_t n=0;
        for (unsigned char c:user) {
            if(c<'0'||c>'9'||n>(INT64_MAX-(c-'0'))/10) return false;
            n=n*10+c-'0';
        }
        return n!=0;
    }
};
struct Sample {
    int state=-1;
    bool readable=false, home=false, loading=true, in_room=true;
};
struct Backend {
    // load: 0 missing, 1 valid, -1 invalid/unreadable. Never silently discard an error.
    std::function<int(Sign&)> load;
    std::function<bool(const Sign&)> persist;
    std::function<bool()> erase;
    std::function<bool(const Sign&)> remove;
    std::function<Sample()> sample;
    std::function<std::uint64_t()> now;
    std::function<void(const std::string&)> log;
};
struct Operation {
    Sign sign; // Empty for a non-create HTTP request held behind startup cleanup.
    std::function<int()> start;
    std::function<int()> status; // HTTP status, 0 pending, -1 terminal error.
    std::function<bool()> cancelled;
    std::size_t bytes=0;
    bool started=false;
};
class SerialCleanup {
public:
    static constexpr std::uint64_t HomeDelayMs=3000;
    explicit SerialCleanup(Backend backend):backend_(std::move(backend)),worker_([this]{Run();}) {}
    ~SerialCleanup() {
        stop_=true;changed_.notify_all();
        if(worker_.joinable()) worker_.join();
    }
    bool Ready() const {return ready_.load();}
    bool Enqueue(Operation op) {
        std::scoped_lock lock(mutex_);
        if(queue_.size()>=32 || op.bytes>262144-queued_bytes_) return false;
        queued_bytes_+=op.bytes;queue_.push_back(std::move(op));changed_.notify_all();return true;
    }
    void ReclearOnNextSign() {reclear_=true;changed_.notify_all();}
private:
    bool Close() {
        if(!current_.Valid()) {guest_seen_=false;home_since_=0;ending_=false;return true;}
        if(!deleted_) {
            const auto now=std::chrono::steady_clock::now();
            if(now<retry_delete_at_) return false;
            if(!backend_.remove(current_)) {
                retry_delete_at_=now+std::chrono::seconds(1);
                Report("delete failed; retry retained");return false;
            }
            retry_delete_at_={};
            deleted_=true;
        }
        if(!backend_.erase()) {Report("metadata erase failed; new create held");return false;}
        backend_.log("old record cleared");
        current_={};deleted_=false;guest_seen_=false;home_since_=0;ending_=false;
        return true;
    }
    void Report(const std::string& error) {
        if(error!=last_error_) {last_error_=error;backend_.log(error);}
    }
    void Observe() {
        const auto s=backend_.sample();
        if(s.readable && s.state==6) {
            guest_seen_=true;home_since_=0;if(!deleted_) ending_=false;return;
        }
        if(!guest_seen_ || !s.readable || s.state!=0 || !s.home || s.loading || s.in_room) {
            home_since_=0;if(!deleted_) ending_=false;return;
        }
        const auto now=backend_.now();
        if(!home_since_) home_since_=now;
        if(now-home_since_>=HomeDelayMs && !ending_) {
            ending_=true;backend_.log("guest returned to own world; closing record");
        }
    }
    void Run() noexcept {
        bool loaded=false;
        while(!stop_) {
            bool progressed=false;
            try {
                if(!loaded) {
                    Sign old;
                    const int state=backend_.load(old);
                    if(state>=0 && (state==0 || old.Valid())) {
                        current_=old;loaded=true;
                        if(old.Valid()) backend_.log("startup old record found");
                    } else Report("startup metadata unreadable or locked; HTTP held");
                }
                if(loaded && !ready_) {
                    if(Close()) {ready_=true;backend_.log("startup cleanup ready");}
                }
                if(ready_) {
                    Observe();
                    bool in_flight=false;
                    {std::scoped_lock lock(mutex_);in_flight=!queue_.empty() && queue_.front().started;}
                    if(ending_ && !in_flight) {
                        progressed=Close();
                    } else {
                        Operation* op=nullptr;
                        {std::scoped_lock lock(mutex_);if(!queue_.empty()) op=&queue_.front();}
                        if(op) {
                            bool done=false;
                            if(op->cancelled()) done=true;
                            else if(!op->started) {
                                // A fresh create waits until a previously successful guest's
                                // confirmed home transition has settled. Hosting/joining never
                                // becomes a successful guest solely from an HTTP claim.
                                const auto s=backend_.sample();
                                const bool settling=op->sign.Valid() && guest_seen_ &&
                                    (!s.readable || s.state!=6);
                                if(!settling) {
                                    const bool close_first=op->sign.Valid() && current_.Valid() &&
                                        (!(current_==op->sign) || reclear_.load());
                                    if(!close_first || Close()) {
                                        if(!op->sign.Valid() || backend_.persist(op->sign)) {
                                            if(op->sign.Valid()) {current_=op->sign;deleted_=false;reclear_=false;}
                                            op->started=true;
                                            done=op->start()<0;progressed=true;
                                        } else Report("metadata write failed; create held");
                                    }
                                }
                            } else {
                                const int status=op->status();
                                if(status) {
                                    // Write-ahead intent already covers acceptance followed by
                                    // a crash before the game reads its response.
                                    if(status>=200 && status<300 && op->sign.Valid())
                                        backend_.persist(op->sign);
                                    done=true;
                                }
                            }
                            if(done) {
                                std::scoped_lock lock(mutex_);
                                queued_bytes_-=queue_.front().bytes;queue_.pop_front();progressed=true;
                            }
                        }
                    }
                }
            } catch(...) { /* Keep durable intent and the queue; retry on the worker. */ }
            std::unique_lock lock(mutex_);
            changed_.wait_for(lock,std::chrono::milliseconds(progressed?1:100));
        }
    }
    Backend backend_;
    std::string last_error_;
    Sign current_;
    bool deleted_=false,guest_seen_=false,ending_=false;
    std::uint64_t home_since_=0;
    std::chrono::steady_clock::time_point retry_delete_at_{};
    std::atomic<bool> stop_{false},ready_{false},reclear_{false};
    std::mutex mutex_;
    std::condition_variable changed_;
    std::deque<Operation> queue_;
    std::size_t queued_bytes_=0;
    std::thread worker_;
};
}
