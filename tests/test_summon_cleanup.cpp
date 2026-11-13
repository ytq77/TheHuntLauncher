// SPDX-License-Identifier: GPL-2.0-or-later
#include "../patches/bbsc_summon_cleanup.h"
#include <cassert>
#include <algorithm>
#include <iostream>
#include <vector>
using namespace TheHuntSummon;
static void Wait(const std::function<bool()>& predicate) {
    for(int i=0;i<400;++i) {if(predicate()) return;std::this_thread::sleep_for(std::chrono::milliseconds(5));}
    assert(false && "worker did not reach expected state");
}
struct Fixture {
    std::mutex mutex;
    Sign disk;
    Sample state{0,true,true,false,false};
    std::vector<std::string> events;
    std::atomic<std::uint64_t> now{100};
    std::atomic<int> removes{0},sends{0},samples{0};
    std::atomic<bool> delete_ok{true},save_ok{true},cancelled{false};
    int load_result=0;
    Backend backend() {
        return {
            [this](Sign& s){s=disk;return load_result;},
            [this](const Sign& s){std::scoped_lock lock(mutex);if(!save_ok) return false;disk=s;events.push_back("persist:"+s.session);return true;},
            [this]{std::scoped_lock lock(mutex);disk={};events.push_back("erase");return true;},
            [this](const Sign& s){++removes;if(!delete_ok) return false;std::scoped_lock lock(mutex);events.push_back("delete:"+s.session);return true;},
            [this]{std::scoped_lock lock(mutex);++samples;return state;},
            [this]{return now.load();},
            [this](const std::string& s){std::scoped_lock lock(mutex);events.push_back(s);}
        };
    }
    void Set(Sample s) {std::scoped_lock lock(mutex);state=s;}
    void Sampled(SerialCleanup&) {const auto before=samples.load();Wait([&]{return samples>before;});}
    Operation Create(std::string session="current") {
        return {{session,"8617"},[this,session]{std::scoped_lock lock(mutex);++sends;events.push_back("send:"+session);return 0;},
                []{return 200;},[this]{return cancelled.load();},900};
    }
    void Publish(SerialCleanup& service) {Wait([&]{return service.Ready();});assert(service.Enqueue(Create()));Wait([&]{return sends==1;});}
};
int main() {
    assert((Sign{"old-0123","8617"}.Valid()));
    assert((!Sign{"bad\"json","8617"}.Valid() && !Sign{"ok","-1"}.Valid()));
    assert((!Sign{"ok","9223372036854775808"}.Valid()));
    {
        Fixture f;f.disk={"previous-process","8617"};f.load_result=1;f.delete_ok=false;
        SerialCleanup service(f.backend());
        const auto begin=std::chrono::steady_clock::now();assert(service.Enqueue(f.Create("new-process")));
        assert(std::chrono::steady_clock::now()-begin<std::chrono::milliseconds(50));
        Wait([&]{return f.removes>=2;});assert(!service.Ready() && f.sends==0);
        f.delete_ok=true;Wait([&]{return service.Ready() && f.sends==1;});
        std::scoped_lock lock(f.mutex);
        const auto removed=std::find(f.events.begin(),f.events.end(),"delete:previous-process");
        const auto sent=std::find(f.events.begin(),f.events.end(),"send:new-process");
        assert(removed<sent && f.disk.session=="new-process");
        std::cout<<"PASS cleanup: persisted previous alias before first send; async failure retry\n";
    }
    for(int transient:{3,4}) {
        Fixture f;SerialCleanup service(f.backend());f.Publish(service);
        f.Set({transient,true,false,false,false});f.Sampled(service);
        f.Set({0,true,true,false,false});f.now=20000;f.Sampled(service);f.now=25000;f.Sampled(service);
        assert(f.removes==0);
        std::cout<<"PASS cleanup: state "<<transient<<" never authorizes guest-end delete (S1)\n";
    }
    {
        Fixture f;SerialCleanup service(f.backend());f.Publish(service);
        f.Set({6,true,false,false,true});f.Sampled(service);
        f.Set({0,true,false,false,false});f.now=10000;f.Sampled(service);assert(f.removes==0);
        f.Set({0,true,true,true,false});f.now=20000;f.Sampled(service);assert(f.removes==0);
        f.Set({0,false,true,false,false});f.now=30000;f.Sampled(service);assert(f.removes==0);
        f.Set({0,true,true,false,true});f.now=40000;f.Sampled(service);assert(f.removes==0);
        f.Set({7,true,false,true,true});f.Sampled(service);f.Set({6,true,false,false,true});f.Sampled(service);
        assert(f.removes==0);
        f.Set({0,true,true,false,false});f.now=50000;f.Sampled(service);
        f.now=52999;f.Sampled(service);assert(f.removes==0);
        f.now=53000;Wait([&]{return f.removes==1;});f.Sampled(service);assert(f.removes==1);
        std::cout<<"PASS cleanup: guest plus own-world/no-load/no-room and continuous 3 seconds; travel/invalid reads ignored\n";
    }
    {
        Fixture f;SerialCleanup service(f.backend());f.Publish(service);
        service.ReclearOnNextSign();f.delete_ok=false;assert(service.Enqueue(f.Create()));
        Wait([&]{return f.removes>=2;});assert(f.sends==1);
        f.delete_ok=true;Wait([&]{return f.sends==2;});
        std::scoped_lock lock(f.mutex);
        assert(std::count(f.events.begin(),f.events.end(),"delete:current")==1);
        assert(f.events.back()=="send:current" || f.events.back()=="persist:current");
        std::cout<<"PASS cleanup: same-session delete before next create; no late delete\n";
    }
    {
        Fixture f;SerialCleanup service(f.backend());f.Publish(service);f.delete_ok=false;
        f.Set({6,true,false,false,true});f.Sampled(service);
        f.Set({0,true,true,false,false});f.now=1000;f.Sampled(service);
        f.now=5000;Wait([&]{return f.removes>0;});
        f.Set({6,true,false,false,true});f.Sampled(service);
        const auto attempts=f.removes.load();f.delete_ok=true;
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
        assert(f.removes==attempts && "guest resumed; failed end cleanup must stop retrying");
        std::cout<<"PASS cleanup: a failed close is cancelled when guest resumes\n";
    }
    {
        Fixture f;SerialCleanup service(f.backend());f.Publish(service);
        std::atomic<bool> completed{false};auto pending=f.Create();
        pending.status=[&]{return completed?200:0;};assert(service.Enqueue(std::move(pending)));
        Wait([&]{return f.sends==2;});
        f.Set({6,true,false,false,true});f.Sampled(service);
        f.Set({0,true,true,false,false});f.now=1000;f.Sampled(service);f.now=5000;f.Sampled(service);
        std::this_thread::sleep_for(std::chrono::milliseconds(150));
        assert(f.removes==0 && "end cleanup must wait for the in-flight native mutation");
        completed=true;Wait([&]{return f.removes==1;});
        std::cout<<"PASS cleanup: guest-end delete cannot overtake an in-flight create\n";
    }
    {
        Fixture f;f.save_ok=false;SerialCleanup service(f.backend());Wait([&]{return service.Ready();});
        assert(service.Enqueue(f.Create()));f.Sampled(service);assert(f.sends==0);
        f.save_ok=true;Wait([&]{return f.sends==1;});
        f.cancelled=true;assert(service.Enqueue(f.Create("cancelled")));f.Sampled(service);assert(f.sends==1);
        std::cout<<"PASS cleanup: write-ahead failure gates send; cancelled request not sent\n";
    }
    {
        Fixture f;f.load_result=-1;SerialCleanup service(f.backend());assert(service.Enqueue(f.Create()));
        std::this_thread::sleep_for(std::chrono::milliseconds(150));assert(!service.Ready() && !f.sends && !f.removes);
        std::cout<<"PASS cleanup: unreadable metadata is not silently discarded\n";
    }
}
