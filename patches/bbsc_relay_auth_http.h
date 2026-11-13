#pragma once
#include <windows.h>
#include <winhttp.h>
#include <algorithm>
#include <string>
#include <string_view>

namespace TheHuntRelayAuth {
struct HttpUrl { std::wstring host; INTERNET_PORT port=0; INTERNET_SCHEME scheme{}; std::wstring path; };
inline bool SummonPath(std::wstring_view path) {
    return path==L"/summon_messenger/create" || path==L"/summon_messenger/get" ||
           path==L"/summon_messenger/request" || path==L"/summon_messenger/delete";
}
inline bool ParseHttp(std::string_view url, HttpUrl& out) {
    if (url.empty() || url.size()>4096 || url.find_first_of("#\\")!=url.npos ||
        std::any_of(url.begin(),url.end(),[](unsigned char c){return c<=0x20 || c==0x7f;})) return false;
    const auto query_at=url.find('?');
    if (query_at!=url.npos) {
        // The game puts its public/game account alias in this query. It is
        // retained on the wire and never used to authorize a private account.
        const auto query=url.substr(query_at+1);
        constexpr std::string_view key="user_id=";
        if (!query.starts_with(key)) return false;
        const auto digits=query.substr(key.size());
        if (digits.empty() || digits.size()>20 ||
            !std::all_of(digits.begin(),digits.end(),[](char c){return c>='0' && c<='9';})) return false;
        url=url.substr(0,query_at);
    }
    const int size=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,url.data(),static_cast<int>(url.size()),nullptr,0);
    if(size<=0)return false;
    std::wstring wide(size,L'\0');MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,url.data(),static_cast<int>(url.size()),wide.data(),size);
    URL_COMPONENTS parts{};parts.dwStructSize=sizeof(parts);
    parts.dwHostNameLength=parts.dwUrlPathLength=parts.dwUserNameLength=parts.dwPasswordLength=static_cast<DWORD>(-1);
    if(!WinHttpCrackUrl(wide.c_str(),size,0,&parts) || parts.dwUserNameLength || parts.dwPasswordLength ||
       (parts.nScheme!=INTERNET_SCHEME_HTTP && parts.nScheme!=INTERNET_SCHEME_HTTPS) || !parts.dwHostNameLength)return false;
    out={std::wstring(parts.lpszHostName,parts.dwHostNameLength),parts.nPort,parts.nScheme,
         parts.dwUrlPathLength ? std::wstring(parts.lpszUrlPath,parts.dwUrlPathLength) : std::wstring{}};
    return query_at==url.npos || SummonPath(out.path);
}
inline bool SameOrigin(const HttpUrl& a,const HttpUrl& b) {
    return a.port==b.port && a.scheme==b.scheme && _wcsicmp(a.host.c_str(),b.host.c_str())==0;
}
inline bool BearerTarget(const std::string& url,const std::string& origin) {
    HttpUrl target,pin;
    return ParseHttp(url,target) && ParseHttp(origin,pin) && (pin.path.empty() || pin.path==L"/") &&
           SameOrigin(target,pin) && SummonPath(target.path);
}
} // namespace TheHuntRelayAuth
