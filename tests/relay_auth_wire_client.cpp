// TEST ONLY. C ABI over the actual mod protocol, for ctypes + real native NP rooms.
// All successful byte-producing calls require a 65536-byte output buffer and
// return bytes written. Failure returns -1 (no diagnostic/secret text).
// receive sets kind: 1=context, 2=data, 3=pending placement, 4=ignored old pending control.
// context output: LE64(room), LE32(own), LE32(owner), u8(peers).
// data output: context output + LE32(sender) + unchanged inner SHAD packet.
// placement output: LE64(generation), LE64(hostroom), LE32(host), LE32(target),
//                   LE32(ttl_ms), UTF8 text. Never changes the real context.
#ifndef RELAY_AUTH_HEADER
#define RELAY_AUTH_HEADER "../patches/bbsc_relay_auth.h"
#endif
#include RELAY_AUTH_HEADER
#include <new>

using TheHuntRelayAuth::Client;
using TheHuntRelayAuth::Bytes;
using TheHuntRelayAuth::MaxFrame;
using TheHuntRelayAuth::Put;
static bool Input(const std::uint8_t* p,std::size_t n,std::size_t max=MaxFrame){return p && n>0 && n<=max;}
static bool Output(std::uint8_t* p,std::size_t n){return p && n>=MaxFrame;}
static int Copy(const Bytes& bytes,std::uint8_t* output){
    if(bytes.size()>MaxFrame)return -1;
    if(!bytes.empty())std::memcpy(output,bytes.data(),bytes.size());return static_cast<int>(bytes.size());
}
extern "C" {
__declspec(dllexport) void* th_client_new(){return new(std::nothrow) Client;}
__declspec(dllexport) void th_client_free(void* client){delete static_cast<Client*>(client);}
__declspec(dllexport) void th_client_reset(void* client){if(client)static_cast<Client*>(client)->Reset();}
__declspec(dllexport) void th_client_leave(void* client){if(client)static_cast<Client*>(client)->Leave();}
__declspec(dllexport) int th_client_begin(void* handle,std::uint64_t user,
    const std::uint8_t* token,std::size_t token_size,const std::uint8_t* challenge,std::size_t challenge_size,
    std::uint8_t* output,std::size_t capacity){
    if(!handle || !user || user>UINT32_MAX || !Input(token,token_size,4096) ||
       !Input(challenge,challenge_size) || !Output(output,capacity))return -1;
    Bytes proof;
    if(!static_cast<Client*>(handle)->Begin(static_cast<std::uint32_t>(user),{token,token_size},
                                           {challenge,challenge_size},proof,true))return -1;
    return Copy(proof,output);
}
__declspec(dllexport) int th_client_ack(void* handle,const std::uint8_t* frame,std::size_t size){
    return handle && Input(frame,size) && static_cast<Client*>(handle)->Ack({frame,size})?1:0;
}
__declspec(dllexport) int th_client_send(void* handle,const std::uint8_t* inner,std::size_t size,
                                       std::uint8_t* output,std::size_t capacity){
    if(!handle || !Input(inner,size) || !Output(output,capacity))return -1;
    Bytes frame;if(!static_cast<Client*>(handle)->Send({inner,size},frame))return -1;
    return Copy(frame,output);
}
__declspec(dllexport) int th_client_receive(void* handle,const std::uint8_t* frame,std::size_t size,
    std::uint8_t* output,std::size_t capacity,int* kind){
    if(kind)*kind=0;
    if(!handle || !kind || !Input(frame,size) || !Output(output,capacity))return -1;
    TheHuntRelayAuth::Delivery delivery;
    const auto result=static_cast<Client*>(handle)->Receive({frame,size},delivery);
    if(result==TheHuntRelayAuth::Received::Invalid){static_cast<Client*>(handle)->Reset();return -1;}
    Bytes data;
    if(result==TheHuntRelayAuth::Received::Placement){
        const auto& p=delivery.placement;*kind=p.ignored?4:3;
        Put(data,p.generation,8);Put(data,p.host_room,8);Put(data,p.host,4);Put(data,p.target,4);Put(data,p.ttl_ms,4);
        data.insert(data.end(),p.text.begin(),p.text.end());
    }else{
        const auto& c=delivery.context;
        Put(data,c.room,8);Put(data,c.own,4);Put(data,c.owner,4);data.push_back(c.peers);
        *kind=1;
        if(result==TheHuntRelayAuth::Received::Data){
            *kind=2;Put(data,delivery.sender,4);data.insert(data.end(),delivery.inner.begin(),delivery.inner.end());
        }
    }
    return Copy(data,output);
}
}
