// Included after SessionState(), inside bbsc_http.cpp's anonymous namespace.
constexpr int CleanupAgain=static_cast<int>(0x80431082u);
constexpr int CleanupInvalid=static_cast<int>(0x804311feu);
constexpr int CleanupBusy=static_cast<int>(0x80431021u);
struct CleanupRequest {
    std::mutex mutex;
    bool cancelled=false,dispatched=false,completed=false,deferred_delete=false;
    bool notified=false,native_deleted=false,ordered=false;
    int result=0;
};
std::map<int,std::shared_ptr<CleanupRequest>> cleanup_requests; // state_mutex
std::atomic<TheHuntSummon::SerialCleanup*> summon_cleanup{nullptr};
std::atomic<bool> cleanup_started{false};

std::optional<int> CleanupRequestResult(int request) {
    std::shared_ptr<CleanupRequest> state;
    {std::scoped_lock lock(state_mutex);const auto it=cleanup_requests.find(request);
     if(it!=cleanup_requests.end()) state=it->second;}
    if(!state) return {};
    std::scoped_lock lock(state->mutex);
    if(state->cancelled) return CleanupInvalid;
    if(!state->dispatched) return CleanupAgain;
    if(state->result<0) return state->result;
    return {};
}
int PS4_SYSV_ABI CleanupGetStatus(int request,int* status) {
    if(const auto pending=CleanupRequestResult(request)) return *pending;
    return original_status(request,status);
}
int PS4_SYSV_ABI CleanupGetLength(int request,int* kind,u64* length) {
    if(const auto pending=CleanupRequestResult(request)) return *pending;
    return original_length(request,kind,length);
}
int PS4_SYSV_ABI CleanupWait(void* epoll,CleanupEvent* events,int count,int timeout) {
    const int read=original_wait(epoll,events,count,timeout);
    if(read<=0) return read;
    int kept=0;
    for(int i=0;i<read;++i) {
        std::shared_ptr<CleanupRequest> state;
        {std::scoped_lock lock(state_mutex);const auto it=cleanup_requests.find(events[i].id);
         if(it!=cleanup_requests.end()) state=it->second;}
        bool discard=false;
        if(state) {
            std::scoped_lock lock(state->mutex);
            state->notified=true;discard=state->cancelled;
            if(discard && state->native_deleted) {
                std::scoped_lock map_lock(state_mutex);
                const auto it=cleanup_requests.find(events[i].id);
                if(it!=cleanup_requests.end() && it->second==state) cleanup_requests.erase(it);
            }
        }
        if(!discard) events[kept++]=events[i];
    }
    return kept;
}
template<class T> bool CleanupRead(u64 address,T& result) {
    MEMORY_BASIC_INFORMATION info{};
    if(address<0x10000 || !VirtualQuery(reinterpret_cast<void*>(address),&info,sizeof(info)) ||
       info.State!=MEM_COMMIT || (info.Protect&(PAGE_GUARD|PAGE_NOACCESS)) ||
       !(info.Protect&(PAGE_READONLY|PAGE_READWRITE|PAGE_WRITECOPY|PAGE_EXECUTE_READ|
                       PAGE_EXECUTE_READWRITE|PAGE_EXECUTE_WRITECOPY)) ||
       address+sizeof(T)>reinterpret_cast<u64>(info.BaseAddress)+info.RegionSize) return false;
    SIZE_T read=0;
    return ReadProcessMemory(GetCurrentProcess(),reinterpret_cast<void*>(address),&result,sizeof(T),&read) && read==sizeof(T);
}
TheHuntSummon::Sample CleanupSample() {
    TheHuntSummon::Sample sample;
    const auto image=BbscMod::EbootAddress();
    u64 manager=0,global=0,world=0,player=0;
    u8 loading=1,own=0; s32 type=-1;
    if(!image || !CleanupRead(image+0x05540290,manager) || !CleanupRead(manager+0x124,sample.state)) return sample;
    sample.readable=true;
    if(!CleanupRead(image+0x05556678,global) || !CleanupRead(global+0x08,loading) ||
       !CleanupRead(global+0x1592,own) || !CleanupRead(image+0x0553E878,world) ||
       !CleanupRead(world+0x60,player) || !CleanupRead(player+0x78,type)) return sample;
    sample.loading=loading!=0;
    sample.home=own!=0 && type==0;
    sample.in_room=BbscMod::RelayAuthInRoom();
    return sample;
}

struct CleanupStore {
    std::filesystem::path path;
    std::string origin,account;
    HANDLE lease=INVALID_HANDLE_VALUE; // OS releases the per-install lock on a crash.
    int Load(TheHuntSummon::Sign& sign) {
        if(lease==INVALID_HANDLE_VALUE) return -1;
        const auto attrs=GetFileAttributesW(path.c_str());
        if(attrs==INVALID_FILE_ATTRIBUTES) return GetLastError()==ERROR_FILE_NOT_FOUND?0:-1;
        if(attrs&(FILE_ATTRIBUTE_REPARSE_POINT|FILE_ATTRIBUTE_DIRECTORY)) return -1;
        std::error_code error;
        const auto length=std::filesystem::file_size(path,error);
        if(error || length>2048) return -1;
        std::ifstream input(path,std::ios::binary);
        std::string bytes((std::istreambuf_iterator<char>(input)),{});
        if(!input || bytes.size()>2048) return -1;
        std::istringstream lines(bytes);std::string version,saved_origin,saved_account,extra;
        if(!std::getline(lines,version)||!std::getline(lines,saved_origin)||!std::getline(lines,saved_account)||
           !std::getline(lines,sign.session)||!std::getline(lines,sign.user)||std::getline(lines,extra)||
           version!="TheHuntSummon1" || saved_origin!=origin || saved_account!=account || !sign.Valid()) return -1;
        return 1;
    }
    bool Persist(const TheHuntSummon::Sign& sign) {
        if(lease==INVALID_HANDLE_VALUE || !sign.Valid()) return false;
        const auto bytes="TheHuntSummon1\n"+origin+"\n"+account+"\n"+sign.session+"\n"+sign.user+"\n";
        const auto temp=std::filesystem::path(path).concat(L".tmp");
        HANDLE file=CreateFileW(temp.c_str(),GENERIC_WRITE,0,nullptr,CREATE_ALWAYS,
                               FILE_ATTRIBUTE_NORMAL|FILE_FLAG_OPEN_REPARSE_POINT,nullptr);
        if(file==INVALID_HANDLE_VALUE) return false;
        BY_HANDLE_FILE_INFORMATION info{};DWORD written=0;
        const bool ok=GetFileInformationByHandle(file,&info) && !(info.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT) &&
            WriteFile(file,bytes.data(),static_cast<DWORD>(bytes.size()),&written,nullptr) && written==bytes.size() && FlushFileBuffers(file);
        CloseHandle(file);
        return ok && MoveFileExW(temp.c_str(),path.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH);
    }
    bool Erase() {return DeleteFileW(path.c_str()) || GetLastError()==ERROR_FILE_NOT_FOUND;}
};

void StartSummonCleanup() {
    if(!BbscMod::RelayAuthPrivate() || cleanup_started.exchange(true)) return;
    const char* pin=std::getenv("BBSC_RELAY_HTTP_ORIGIN");
    TheHuntRelayAuth::HttpUrl origin;
    if(!pin || !TheHuntRelayAuth::ParseHttp(pin,origin) ||
       (!origin.path.empty() && origin.path!=L"/") || !BbscMod::RelayAuthOwnId()) {
        LOG_ERROR(Mod,"Summon cleanup: private origin/account unavailable; recruitment disabled");return;
    }
    auto* store=new CleanupStore;
    store->origin=pin;
    while(store->origin.ends_with('/')) store->origin.pop_back();
    store->account=std::to_string(BbscMod::RelayAuthOwnId());
    HMODULE module=nullptr;
    if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
        reinterpret_cast<LPCWSTR>(&StartSummonCleanup),&module)) return;
    std::wstring location(32768,L'\0');
    const auto size=GetModuleFileNameW(module,location.data(),static_cast<DWORD>(location.size()));
    if(!size || size>=location.size()) return;
    location.resize(size);
    std::uint64_t scope=14695981039346656037ull;
    for(unsigned char byte:store->origin) scope=(scope^byte)*1099511628211ull;
    const auto filename="summon-record-"+store->account+"-"+std::to_string(scope)+".state";
    store->path=std::filesystem::path(location).parent_path()/filename;
    const auto lock=std::filesystem::path(store->path).concat(L".lock");
    store->lease=CreateFileW(lock.c_str(),GENERIC_READ|GENERIC_WRITE,0,nullptr,OPEN_ALWAYS,
                             FILE_ATTRIBUTE_NORMAL|FILE_FLAG_OPEN_REPARSE_POINT,nullptr);
    TheHuntSummon::Backend backend;
    backend.load=[store](auto& s){return store->Load(s);};
    backend.persist=[store](const auto& s){return store->Persist(s);};
    backend.erase=[store]{return store->Erase();};
    backend.remove=[store](const auto& s){
        const auto body="{\"MessageId\":\"SummonDataRemoveRequest\",\"SessionId\":\""+s.session+"\",\"UserId\":"+s.user+",\"Force\":true}";
        const auto code=Post(store->origin+"/summon_messenger/delete",body);
        static DWORD previous=~DWORD{};static ULONGLONG stamp=0;
        const auto now=GetTickCount64();
        if(code!=previous || now-stamp>=5000) {
            LOG_INFO(Mod,"HTTP: summon cleanup delete result {}",code);previous=code;stamp=now;
        }
        return code>=200 && code<300;
    };
    backend.sample=&CleanupSample;
    backend.now=[] {return static_cast<std::uint64_t>(NowMs());};
    backend.log=[](const auto& message){LOG_INFO(Mod,"HTTP: summon cleanup {}",message);};
    summon_cleanup=new TheHuntSummon::SerialCleanup(std::move(backend));
}

int ForwardSend(int request,const void* data,u64 size) {
    std::string url;
    {std::scoped_lock lock(state_mutex);const auto it=request_urls.find(request);
     if(it!=request_urls.end()) url=it->second;}
    const bool create=IsSummonCreate(url) && !IsPublicServer(url);
    const bool mutation=create || (IsSummonDelete(url) && !IsPublicServer(url));
    auto* cleanup=summon_cleanup.load();
    if(!cleanup_started) return original_send(request,data,size);
    if(!cleanup) return mutation && BbscMod::RelayAuthPrivate()?CleanupBusy:original_send(request,data,size);
    if(!mutation && cleanup->Ready()) return original_send(request,data,size);
    if(size>262144 || (size && !data)) return CleanupInvalid;
    std::string body=size?std::string(static_cast<const char*>(data),static_cast<size_t>(size)):std::string{};
    TheHuntSummon::Sign sign;
    if(create) {
        sign={JsonString(body,"SessionId"),JsonNumber(body,"UserId")};
        if(!sign.Valid()) return CleanupInvalid;
    }
    auto state=std::make_shared<CleanupRequest>();
    state->ordered=mutation;
    {std::scoped_lock lock(state_mutex);if(cleanup_requests.contains(request)) return CleanupBusy;
     cleanup_requests[request]=state;}
    TheHuntSummon::Operation op;
    op.sign=std::move(sign);op.bytes=body.size();
    op.cancelled=[state]{std::scoped_lock lock(state->mutex);return state->cancelled && !state->dispatched;};
    op.start=[state,request,body=std::move(body)]{
        std::scoped_lock lock(state->mutex);
        if(state->cancelled) return CleanupInvalid;
        state->result=original_send(request,body.empty()?nullptr:body.data(),body.size());
        state->dispatched=true;return state->result;
    };
    op.status=[state,request]{
        {std::scoped_lock lock(state->mutex);if(state->result<0) return -1;}
        if(!state->ordered) {std::scoped_lock lock(state->mutex);state->completed=true;return 200;}
        int status=0;const int result=original_status(request,&status);
        if(result==CleanupAgain) return 0;
        std::scoped_lock lock(state->mutex);
        state->completed=true;
        if(state->deferred_delete) {
            original_delete(request);
            state->native_deleted=true;
            if(state->notified) {
                std::scoped_lock map_lock(state_mutex);
                const auto found=cleanup_requests.find(request);
                if(found!=cleanup_requests.end() && found->second==state) cleanup_requests.erase(found);
            }
        }
        return result<0?-1:status;
    };
    if(!cleanup->Enqueue(std::move(op))) {
        std::scoped_lock lock(state_mutex);cleanup_requests.erase(request);return CleanupBusy;
    }
    return 0; // Native SendRequest is asynchronous; its original epoll event follows dispatch.
}
