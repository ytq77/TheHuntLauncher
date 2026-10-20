// SPDX-FileCopyrightText: Copyright 2026 CLS_AMG
// SPDX-License-Identifier: GPL-2.0-or-later

#include "core/bbsc_memscan.h"

#ifdef _WIN32
#include <windows.h>
#endif

#include <algorithm>
#include <cstring>

namespace Core::Bbsc {
namespace {
#ifdef _WIN32
// The host's vectored handler terminates on CRT faults before a local SEH handler runs.
// ReadProcessMemory safely snapshots changing guest pages; search only our stable buffer.
void SearchRegion(const std::uint8_t* start, std::size_t size, const std::uint8_t* needle,
                  std::size_t length, std::vector<std::uint64_t>& hits, std::size_t max_hits) {
    constexpr std::size_t BlockSize = 64 * 1024;
    SYSTEM_INFO system{};
    GetSystemInfo(&system);
    const std::size_t page_size = system.dwPageSize;
    std::vector<std::uint8_t> buffer(BlockSize + length - 1);
    std::size_t carry = 0;
    const auto read = [&](std::size_t offset, std::size_t count) {
        SIZE_T got = 0;
        const bool ok = ReadProcessMemory(GetCurrentProcess(), start + offset,
                                         buffer.data() + carry, count, &got) != 0;
        return ok && got == count;
    };
    const auto search = [&](std::size_t offset, std::size_t count) {
        const auto* at = buffer.data();
        const auto* end = at + carry + count;
        while (hits.size() < max_hits && end - at >= static_cast<std::ptrdiff_t>(length)) {
            at = static_cast<const std::uint8_t*>(
                std::memchr(at, needle[0], static_cast<std::size_t>(end - at) - length + 1));
            if (at == nullptr) break;
            if (std::memcmp(at, needle, length) == 0) {
                hits.push_back(reinterpret_cast<std::uint64_t>(start) + offset - carry +
                               static_cast<std::size_t>(at - buffer.data()));
            }
            ++at;
        }
        carry = std::min(length - 1, carry + count);
        std::memmove(buffer.data(), end - carry, carry);
    };
    for (std::size_t offset = 0; offset < size && hits.size() < max_hits;) {
        const std::size_t count = std::min(BlockSize, size - offset);
        if (read(offset, count)) {
            search(offset, count);
        } else {
            // A page changed protection after VirtualQuery. Keep accessible pages of this block.
            for (std::size_t part = 0; part < count && hits.size() < max_hits;) {
                const auto address = reinterpret_cast<std::uint64_t>(start) + offset + part;
                const std::size_t step = std::min(count - part, page_size - address % page_size);
                if (read(offset + part, step)) search(offset + part, step);
                else carry = 0;
                part += step;
            }
        }
        offset += count;
    }
}
#endif
} // namespace

std::vector<std::uint64_t> FindInMemory(std::span<const std::uint8_t> needle, std::uint64_t lo,
                                      std::uint64_t hi, std::size_t max_hits) {
    std::vector<std::uint64_t> hits;
#ifdef _WIN32
    if (needle.empty()) return hits;
    constexpr DWORD Readable = PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READ |
                               PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;
    std::uint64_t at = lo;
    while (at < hi && hits.size() < max_hits) {
        MEMORY_BASIC_INFORMATION info{};
        if (VirtualQuery(reinterpret_cast<void*>(at), &info, sizeof(info)) == 0) break;
        const auto base = reinterpret_cast<std::uint64_t>(info.BaseAddress);
        const std::uint64_t end = base + info.RegionSize;
        if (info.State == MEM_COMMIT && (info.Protect & Readable) != 0 &&
            (info.Protect & (PAGE_GUARD | PAGE_NOACCESS)) == 0) {
            const auto begin = std::max(at, base);
            const auto limit = std::min(hi, end);
            SearchRegion(reinterpret_cast<const std::uint8_t*>(begin), limit - begin,
                         needle.data(), needle.size(), hits, max_hits);
        }
        if (end <= at) break;
        at = end;
    }
#endif
    return hits;
}
} // namespace Core::Bbsc
