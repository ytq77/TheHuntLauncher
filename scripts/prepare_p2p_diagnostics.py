"""Add optional transport diagnostics to the pinned BBport 1.9.7 source, once."""
from pathlib import Path
import argparse
import hashlib
import shutil

ROOT=Path(__file__).resolve().parents[1]
BASE='gpu/bbnet/vendor/shadps4/core/net'
PINS = {'p2p_codec.cpp': 'a23ccc9824ea0d8d8c58493ea5641131fcb400048b337bac1918ceac6723e1de', 'p2p_codec.h': '25b32c418400c406eb512d6f72d27487f73730b07575f92ff7e46c5fe6c995f6', 'p2p_crypto.cpp': '92a70c0df99bfc7385435b143c7af2870b667df09248581e77d4ac8a6e44e1ab', 'p2p_crypto.h': '80581d4727d959a10c65d880b5a759d8d1d1374cb070fafae3e4495427e378ca', 'p2p_transport.cpp': '03da4c30b1d43e0fc619c3c1734bbe6ec6f82081d76206c06866281174757701', 'p2p_transport.h': 'daddcd81ef46d51b604f88cc22eaf5513300ce57967b178989ec36e8bf200bf4'}
def once(text,before,after):
    if text.count(before)!=1:raise ValueError('Unexpected P2P diagnostic anchor: '+before[:100])
    return text.replace(before,after,1)
def transform(name,text):
    if hashlib.sha256(text.encode()).hexdigest()!=PINS[name]:
        raise ValueError('Unexpected/already patched P2P source: '+name)
    if name=='p2p_transport.h':
        text=once(text,'#include "core/net/p2p_tcp.h"','#include "core/net/p2p_tcp.h"\n#include "core/net/p2p_diag_types.h"')
        text=once(text,'namespace Core::Net::P2P {','namespace Core::Net::P2P {\nclass Diagnostics;')
        text=once(text,'        std::vector<u8> payload;\n    };',
                  '        std::vector<u8> payload;\n        DecodeFailure failure=DecodeFailure::Malformed;\n    };')
        text=once(text,'Clock::time_point now);\n    void HandleStreamSegment',
                  'Clock::time_point now, bool via_relay=false);\n    void HandleStreamSegment')
        text=once(text,'    const TransportConfig config_;',
                  '    const TransportConfig config_;\n    std::unique_ptr<Diagnostics> diagnostics_;')
    elif name=='p2p_crypto.h':
        text=once(text,'#include "common/types.h"','#include "common/types.h"\n#include "core/net/p2p_diag_types.h"')
        text=once(text,'                      std::vector<u8>& output);',
                  '                      std::vector<u8>& output, DecodeFailure* failure=nullptr);')
    elif name=='p2p_crypto.cpp':
        text=once(text,'                      std::vector<u8>& output) {\n    const size_t prefix',
                  '                      std::vector<u8>& output, DecodeFailure* failure) {\n'
                  '    if (failure) *failure=DecodeFailure::None;\n    const size_t prefix')
        text=once(text,'    if (data.size() < prefix) {\n        return false;',
                  '    if (data.size() < prefix) {\n        if (failure) *failure=DecodeFailure::Malformed;\n        return false;')
        text=once(text,'        if (CRYPTO_memcmp(signature.data(), expected.data(), signature.size()) != 0) {\n            return false;',
                  '        if (CRYPTO_memcmp(signature.data(), expected.data(), signature.size()) != 0) {\n'
                  '            if (failure) *failure=DecodeFailure::BadSignature;\n            return false;')
        text=once(text,'    return !encrypt || Crypt(output, key, iv, false);',
                  '    const bool ok=!encrypt || Crypt(output, key, iv, false);\n'
                  '    if (!ok && failure) *failure=DecodeFailure::Cipher;\n    return ok;')
    elif name=='p2p_codec.cpp':
        text=once(text,'            expected && communication_id != expected) {\n            return out;',
                  '            expected && communication_id != expected) {\n'
                  '            out.failure=DecodeFailure::CommunicationId;\n            return out;')
        text=once(text,
            '        if (!key || !UnprotectPayload(body, (mode & 1) != 0, (mode & 2) != 0, *key, out.payload)) {\n'
            '            return out; // no key or bad signature\n        }',
            '        if (!key) {out.failure=DecodeFailure::NoKey;return out;}\n'
            '        if (!UnprotectPayload(body, (mode & 1) != 0, (mode & 2) != 0, *key, out.payload, &out.failure)) {\n'
            '            return out; // Same rejection, with diagnostic reason metadata.\n        }')
        text=once(text,'    out.src_vport = src_vport;',
                  '    out.failure=DecodeFailure::None;\n    out.src_vport = src_vport;')
    elif name=='p2p_transport.cpp':
        text=once(text,'#include "core/net/p2p_transport.h"',
            '#include "core/net/p2p_transport.h"\n#include "core/net/p2p_diagnostics.h"\n#include "common/logging/log.h"')
        text=once(text,'namespace Core::Net::P2P {','''namespace Core::Net::P2P {
static void DiagnosticLog(const std::string& row) {
    // Explicitly enabled diagnostics must remain visible even when BB_GPU_LOG is warning.
    Common::Log::Write(Common::Log::Class::Lib_Net,Common::Log::Level::info,
                      __FILE__,__LINE__,__func__,std::string(row));
}''')
        text=once(text,'    t->thread_ = std::thread([raw = t.get()] { raw->ThreadMain(); });',
                  '    t->diagnostics_=Diagnostics::Create(t->bound_port_,&DiagnosticLog);\n'
                  '    t->thread_ = std::thread([raw = t.get()] { raw->ThreadMain(); });')
        text=once(text,'    Host::CloseSocket(socket_); // thread is stopped by now',
                  '    if (diagnostics_) diagnostics_->Flush(Clock::now(),true);\n'
                  '    Host::CloseSocket(socket_); // thread is stopped by now')
        text=once(text,'    return SendPacket(codec_->EncodeSignaling(data, to), to);',
                  '    if (diagnostics_) diagnostics_->Submit(to);\n'
                  '    return SendPacket(codec_->EncodeSignaling(data, to), to);')
        text=once(text,'    return earliest;\n}',
                  '    if (diagnostics_ && (!earliest || diagnostics_->Deadline()<*earliest))\n'
                  '        earliest=diagnostics_->Deadline();\n    return earliest;\n}')
        text=once(text,'        Update(connection);\n    }\n}\n\nvoid Transport::Reschedule',
                  '        Update(connection);\n    }\n    if (diagnostics_) diagnostics_->Flush(now);\n}\n\nvoid Transport::Reschedule')
        text=once(text,
            '            return Error::Ok;\n        }\n    }\n    if (relay_server_',
            '            if (diagnostics_) diagnostics_->TestDrop(to);\n'
            '            return Error::Ok;\n        }\n    }\n    if (relay_server_')
        text=once(text,
            '            return Host::SendTo(socket_, framed.data(), framed.size(), 0,\n'
            '                                relay_server_->Sockaddr(), relay_server_->Length())\n'
            '                .error;',
            '            const auto r=Host::SendTo(socket_, framed.data(), framed.size(), 0,\n'
            '                                relay_server_->Sockaddr(), relay_server_->Length());\n'
            '            if (diagnostics_) diagnostics_->Sent(to,packet,true,r.error,\n'
            '                r.error==Error::Ok ? 0 : DiagnosticNativeError());\n'
            '            return r.error;')
        text=once(text,
            '        Host::SendTo(socket_, packet.data(), packet.size(), 0, to.Sockaddr(), to.Length());\n    return r.error;',
            '        Host::SendTo(socket_, packet.data(), packet.size(), 0, to.Sockaddr(), to.Length());\n'
            '    if (diagnostics_) diagnostics_->Sent(to,packet,false,r.error,\n'
            '        r.error==Error::Ok ? 0 : DiagnosticNativeError());\n    return r.error;')
        text=once(text,'void Transport::HandlePacket(std::span<const u8> packet, const Endpoint& from,\n'
                  '                             Clock::time_point now) {',
                  'void Transport::HandlePacket(std::span<const u8> packet, const Endpoint& from,\n'
                  '                             Clock::time_point now, bool via_relay) {')
        text=once(text,'                     now);\n        return;\n    }\n    Codec::Decoded decoded',
                  '                     now,true);\n        return;\n    }\n    Codec::Decoded decoded')
        text=once(text,'    Codec::Decoded decoded = codec_->Decode(packet, from);',
                  '    Codec::Decoded decoded = codec_->Decode(packet, from);\n'
                  '    if (diagnostics_) diagnostics_->Received(from,packet,via_relay,decoded);')
        text=once(text,'            signaling_[channel].emplace_back(std::move(decoded.payload), from);\n        }',
                  '            signaling_[channel].emplace_back(std::move(decoded.payload), from);\n'
                  '        } else if (diagnostics_) diagnostics_->QueueDrop(from);')
    # p2p_codec.h is deliberately pinned too, but needs no modification.
    return text

def prepare(root):
    folder=Path(root)/BASE
    if any((folder/name).exists() for name in ('p2p_diagnostics.h','p2p_diag_types.h')):
        raise ValueError('P2P diagnostic headers already exist')
    # Resolve and validate every original before the first source mutation.
    edits={name:transform(name,(folder/name).read_text('utf-8')) for name in PINS}
    for name in ('p2p_diagnostics.h','p2p_diag_types.h'):
        if not (ROOT/'patches'/name).is_file():raise ValueError('Missing diagnostic header: '+name)
    for name,body in edits.items():
        if body!=(folder/name).read_text('utf-8'):(folder/name).write_text(body,'utf-8',newline='\n')
    for name in ('p2p_diagnostics.h','p2p_diag_types.h'):shutil.copy2(ROOT/'patches'/name,folder/name)
    print('Prepared optional P2P transport diagnostics for BBport 1.9.7; no packet validation or route decision changed.')
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    prepare(parser.parse_args().source)
