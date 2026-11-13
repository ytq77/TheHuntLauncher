"""Add invocation-local cancellation to the pinned native hook trampoline."""
from prepare_personal_rewards import once


def apply_hooks(text):
    text = once(text,
                '        trampoline.mov(r12, rsp);\n        trampoline.sub(rsp, 32);',
                '        trampoline.mov(snapshot(offsetof(GuestRegisterSnapshot, skip_original)), 0);\n'
                '        trampoline.mov(r12, rsp);\n        trampoline.sub(rsp, 32);')
    begin = text.index('        for (size_t index = 0; index < 16; ++index) {',
                       text.index('        trampoline.mov(rsp, r12);'))
    end = text.index('        Xbyak::Label run_original;', begin)
    restore = text[begin:end]
    # Decide before restoring flags/registers, then use the same restoration
    # sequence on both paths. No shared skip byte can affect another invocation.
    replacement = '''        const auto restore_snapshot = [&] {
''' + restore + '''        };
        Xbyak::Label run_hooked;
        trampoline.cmp(snapshot(offsetof(GuestRegisterSnapshot, skip_original)), 0);
        trampoline.je(run_hooked, Xbyak::CodeGenerator::LabelType::T_NEAR);
        restore_snapshot();
        trampoline.ret();
        trampoline.L(run_hooked);
        restore_snapshot();
'''
    return text[:begin]+replacement+text[end:]


def apply_header(text):
    return once(text, '    alignas(16) std::array<std::array<u8, 32>, 16> ymm;',
                '    alignas(16) std::array<std::array<u8, 32>, 16> ymm;\n'
                '    u64 skip_original; // Function-entry callbacks only; reset per invocation.')
